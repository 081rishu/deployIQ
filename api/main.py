"""FastAPI app wiring (ARCHITECTURE.txt 3.1)."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse

from api.interview import router as interview_router
from api.voice import voice_interview

app = FastAPI(title="AI Deployment Decision Engine")

app.include_router(interview_router)
# FastAPI 0.141 lazy-includes routers; register the WebSocket handler directly
# on the app so the route is live at startup (an included-router WS can 403).
app.add_api_websocket_route("/ws/interview/voice", voice_interview)

_VOICE_PAGE = os.path.join(os.path.dirname(__file__), "..", "static", "voice.html")


@app.get("/voice", response_class=FileResponse)
def voice_page():
    """Serve the browser voice-interview client (cache-busted, from disk)."""
    return FileResponse(_VOICE_PAGE, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    })
