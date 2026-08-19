"""Voice interview over WebSocket (ARCHITECTURE 3.1 extension).

Protocol (binary-friendly for a browser mic):
  client -> server   JSON {"action":"start","sector":...,"problem":...}
  server -> client   JSON {"type":"ready","state":...,"context":...,"status":...,"question":...,"audio":<b64 mp3>}
  client -> server   binary frame = user speech audio (e.g. webm/ogg from mic)
  server -> client   JSON {"type":"turn","state":...,"context":...,"transcript":...,"status":...,"question":...,"audio":<b64 mp3>,"stop":bool}

The server holds one VoiceSession per websocket connection, so the
AssessmentState persists across frames for that socket only (still no
cross-session persistence).
"""

from __future__ import annotations

import base64
import json
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.request_context import get_request_id, reset_request_id, set_request_id
from interviewer.engine import TurnResult
from interviewer.voice import VoiceSession
from lib.logging_config import get_logger
from schemas.assessment_state import Sector

router = APIRouter(tags=["voice"])
log = get_logger("api.voice")


def _payload(result: TurnResult, extra: dict | None = None) -> dict:
    data: dict = {
        "request_id": get_request_id(),
        "state": result.state.model_dump(mode="json"),
        "context": (result.context.model_dump(mode="json")
                    if result.context is not None else None),
        "status": result.state.status.value,
        "stop": result.stop,
        "stop_reason": result.stop_reason,
        "question": result.question,
        "acknowledgment": result.acknowledgment,
        "updated_fields": [u.field for u in result.updated_fields],
    }
    if extra:
        data.update(extra)
    # Attach spoken audio for the reply text when there is something to say.
    text = (result.acknowledgment or "") + " " + (result.question or "")
    if text.strip():
        data["audio"] = _base64_audio(text)
    return data


def _base64_audio(text: str) -> str:
    from llm.tts import synthesize
    return base64.b64encode(synthesize(text)).decode("ascii")


@router.websocket("/ws/interview/voice")
async def voice_interview(ws: WebSocket) -> None:
    await ws.accept()
    connection_id = str(uuid4())
    context_token = set_request_id(connection_id)
    log.info("ws_connected")
    session: VoiceSession | None = None
    try:
        while True:
            raw = await ws.receive()
            if raw.get("type") == "websocket.disconnect":
                log.info("ws disconnect")
                break

            # Binary frame = user speech audio.
            if raw.get("type") == "websocket.receive" and "bytes" in raw:
                if session is None:
                    log.warning("binary frame before start; ignored")
                    await ws.send_json({"type": "error", "message": "start first"})
                    continue
                log.info("received audio bytes=%d", len(raw["bytes"]))
                result = session.respond(raw["bytes"])
                await ws.send_json(_payload(result, {
                    "type": "turn",
                    "transcript": session.last_transcript or "",
                }))
                continue

            # JSON message = control/start.
            if "text" in raw:
                msg = json.loads(raw["text"])
                action = msg.get("action")
                if action == "start":
                    sector = Sector(msg.get("sector", "customer_support"))
                    problem = msg.get("problem", "")
                    log.info("ws_start sector=%s problem_chars=%d",
                             sector.value, len(problem))
                    session = VoiceSession()
                    result = session.start(sector, problem)
                    await ws.send_json(_payload(result, {"type": "ready"}))
                elif action == "ping":
                    await ws.send_json({"type": "pong"})
                else:
                    log.warning("ws_unknown_action")
                    await ws.send_json({"type": "error", "message": "unknown action"})
                continue

    except WebSocketDisconnect:
        log.info("ws disconnected (client)")
    except Exception:  # noqa: BLE001 - log detail, return safe wire error
        log.exception("ws_unhandled_exception")
        try:
            await ws.send_json({
                "type": "error",
                "message": "Voice interview unavailable",
                "request_id": connection_id,
            })
        except Exception:
            pass
    finally:
        reset_request_id(context_token)
        await ws.close()
