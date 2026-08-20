"""Voice interview over WebSocket (ARCHITECTURE 3.1 extension).

Protocol (binary-friendly for a browser mic):
  client -> server   JSON {"action":"resume","state":...,"context":...,"speak":"<text>"}
  server -> client   JSON {"type":"ready","state":...,"context":...,"status":...,"question":...,"audio":<b64 mp3>}

`resume` is what a browser uses: /api/interview/start has already run the
first turn, so the socket adopts that state instead of starting a second,
independent interview. `start` remains for non-browser clients that have no
prior state (scripts/ws_test_client.py).
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
from interviewer.conversation import ConversationContext
from interviewer.engine import TurnResult
from interviewer.voice import VoiceSession
from lib.logging_config import get_logger
from schemas.assessment_state import AssessmentState, Sector

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
    #
    # Speech is the leg most likely to be missing: it is the one a free or
    # low-cost provider tends not to offer. A turn without audio is still a
    # complete turn — the question is on screen either way — so a synthesis
    # failure costs the voice, not the answer.
    text = (result.acknowledgment or "") + " " + (result.question or "")
    if text.strip():
        try:
            data["audio"], data["audio_format"] = _base64_audio(text)
        except Exception:  # noqa: BLE001 - degrade to text, never lose the turn
            log.warning("speech_unavailable; returning the turn without audio",
                        exc_info=True)
            data["speech_unavailable"] = True
    return data


def _base64_audio(text: str) -> tuple[str, str]:
    """Base64 audio plus the container it is in, so the client can play it."""
    from llm.tts import synthesize
    audio, fmt = synthesize(text)
    return base64.b64encode(audio).decode("ascii"), fmt


def _turn_error_message(exc: Exception) -> str:
    """What to tell the user about a failed turn.

    Deliberately not the exception text: it can carry account and billing
    detail. The distinction that matters to the person talking is whether
    retrying is worth it, so the message says which case this is and that
    typing still works. The specifics stay in the log.
    """
    name = type(exc).__name__
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                "InternalServerError"):
        return ("The voice service is temporarily unavailable. Your interview "
                "is still here — try again in a moment, or continue by typing.")
    return ("That answer could not be processed. Please try again, or "
            "continue by typing.")


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
                try:
                    result = session.respond(raw["bytes"])
                except Exception as exc:  # noqa: BLE001 - one turn, not the session
                    # A failed turn must not end the interview. This used to
                    # fall through to the handler below, which closed the
                    # socket, so a single transcription failure — a provider
                    # hiccup, an exhausted quota — destroyed a conversation
                    # the user had already spent ten questions on.
                    log.exception("voice_turn_failed")
                    await ws.send_json({
                        "type": "turn_error",
                        "recoverable": True,
                        "message": _turn_error_message(exc),
                        "request_id": get_request_id(),
                    })
                    continue
                await ws.send_json(_payload(result, {
                    "type": "turn",
                    "transcript": session.last_transcript or "",
                }))
                continue

            # JSON message = control/start.
            if "text" in raw:
                msg = json.loads(raw["text"])
                action = msg.get("action")
                if action == "resume":
                    # Adopt the interview /api/interview/start already began.
                    # No turn is run: running one here would duplicate the
                    # greeting the client is already showing and burn a second
                    # LLM call. `speak` is TTS-only text the server itself
                    # produced on the REST turn; it never enters interview
                    # logic, and an absent/blank value simply means silence.
                    session = VoiceSession()
                    session.resume(
                        AssessmentState.model_validate(msg.get("state") or {}),
                        (ConversationContext.model_validate(msg["context"])
                         if msg.get("context") else None),
                    )
                    log.info("ws_resume turn_count=%d", session.state.turn_count)
                    speak = str(msg.get("speak") or "").strip()
                    payload = {
                        "type": "ready",
                        "request_id": get_request_id(),
                        "state": session.state.model_dump(mode="json"),
                        "context": (session.context.model_dump(mode="json")
                                    if session.context is not None else None),
                        "status": session.state.status.value,
                        "stop": False,
                        "stop_reason": None,
                        # Deliberately null: the client already rendered these
                        # from the REST turn, and re-sending them is exactly
                        # the duplicate this action exists to remove.
                        "question": None,
                        "acknowledgment": None,
                        "updated_fields": [],
                    }
                    if speak:
                        try:
                            payload["audio"], payload["audio_format"] = _base64_audio(speak)
                        except Exception:  # noqa: BLE001 - a mute resume is fine
                            log.warning("speech_unavailable on resume", exc_info=True)
                            payload["speech_unavailable"] = True
                    await ws.send_json(payload)
                elif action == "start":
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
