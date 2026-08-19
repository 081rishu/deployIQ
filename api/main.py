"""FastAPI app wiring (ARCHITECTURE.txt 3.1)."""

from __future__ import annotations

import os
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.interview import router as interview_router
from api.voice import voice_interview
from calc.ai_state import LaborRealization
from pipeline import orchestrate
from report.schema import LaborRealizationSource, ReportMode
from schemas.assessment_state import AssessmentState

def _allowed_origins() -> list[str]:
    raw = os.getenv("DEPLOYIQ_ALLOWED_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if "*" in origins:
        raise RuntimeError(
            "DEPLOYIQ_ALLOWED_ORIGINS cannot contain '*' when credentials are enabled"
        )
    return origins


app = FastAPI(title="AI Deployment Decision Engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(interview_router)
# FastAPI 0.141 lazy-includes routers; register the WebSocket handler directly
# on the app so the route is live at startup (an included-router WS can 403).
app.add_api_websocket_route("/ws/interview/voice", voice_interview)

_VOICE_PAGE = os.path.join(os.path.dirname(__file__), "..", "static", "voice.html")


class AssessmentRunRequest(BaseModel):
    state: AssessmentState
    labor_realization: Optional[LaborRealization] = None
    labor_realization_source: LaborRealizationSource = LaborRealizationSource.UNSET
    enable_narration: bool = False
    narration_temperature: float = 0.2
    narration_model: Optional[str] = None
    report_format: Literal["json", "markdown", "both"] = "both"


class AssessmentRunResponse(BaseModel):
    mode: ReportMode
    used_narration: bool
    narration_issues: list[str] = []
    labor_realization: Optional[LaborRealization] = None
    labor_realization_source: LaborRealizationSource
    economic_error: list[str] = []
    report_json: Optional[dict] = None
    report_markdown: Optional[str] = None


@app.post("/api/assessment/run", response_model=AssessmentRunResponse)
def assessment_run(req: AssessmentRunRequest) -> AssessmentRunResponse:
    try:
        run = orchestrate.run_assessment(
            req.state,
            labor_realization=req.labor_realization,
            labor_realization_source=req.labor_realization_source,
            enable_narration=req.enable_narration,
            narration_temperature=req.narration_temperature,
            narration_model=req.narration_model,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to API failure semantics
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    report_json = run.rendered.json_doc if req.report_format in ("json", "both") else None
    report_markdown = run.rendered.markdown if req.report_format in ("markdown", "both") else None

    return AssessmentRunResponse(
        mode=run.final_report.mode,
        used_narration=run.used_narration,
        narration_issues=list(run.narration_issues),
        labor_realization=run.bundle.labor_realization,
        labor_realization_source=run.bundle.labor_realization_source,
        economic_error=list(run.bundle.economic_error),
        report_json=report_json,
        report_markdown=report_markdown,
    )


@app.get("/voice", response_class=FileResponse)
def voice_page():
    """Serve the browser voice-interview client (cache-busted, from disk)."""
    return FileResponse(_VOICE_PAGE, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    })
