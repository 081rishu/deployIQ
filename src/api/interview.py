"""Interview endpoint (ARCHITECTURE.txt 3.1).

One turn of the adaptive interviewer. Stateless per request: the client
sends the current state + latest user message, gets back the updated state,
an acknowledgment, and the next question (or a stop flag).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from interviewer.conversation import ConversationContext
from interviewer.engine import NeedType, TurnResult, run_turn
from schemas.assessment_state import AssessmentState, InterviewStatus, Sector

router = APIRouter(prefix="/api", tags=["interview"])


class StartRequest(BaseModel):
    sector: Sector
    problem: str = ""
    # Opt into the conversational warm-up (name, brief rapport, then work).
    warm_up: bool = True


class TurnRequest(BaseModel):
    state: AssessmentState
    message: str
    # Shipped back each turn alongside the state, exactly like AssessmentState.
    # Holds name/phase only — never anything analytical.
    context: Optional[ConversationContext] = None


class TurnResponse(BaseModel):
    state: AssessmentState
    context: Optional[ConversationContext] = None
    phase: Optional[str] = None
    uncollected_tier2: list[str] = []
    completion_statement: str = ""
    updated_fields: list[str]
    acknowledgment: Optional[str] = None
    question: Optional[str] = None
    stop: bool
    status: InterviewStatus = InterviewStatus.INTERVIEWING
    need_type: Optional[NeedType] = None
    stop_reason: Optional[str] = None


@router.post("/interview/start")
def start(req: StartRequest) -> TurnResponse:
    """First turn: seed the state from the intake problem and ask the first
    question (screen 1 -> screen 2)."""
    state = AssessmentState(sector=req.sector, problem=req.problem)
    context = ConversationContext() if req.warm_up else None
    result = run_turn(state, req.problem, context)
    return _to_response(result)


@router.post("/interview/turn")
def turn(req: TurnRequest) -> TurnResponse:
    """Subsequent turn: given current state + latest message, return the next
    question or a stop signal."""
    result = run_turn(req.state, req.message, req.context)
    return _to_response(result)


def _to_response(r: TurnResult) -> TurnResponse:
    return TurnResponse(
        state=r.state,
        context=r.context,
        phase=r.phase,
        uncollected_tier2=r.uncollected_tier2,
        completion_statement=r.completion_statement,
        updated_fields=[u.field for u in r.updated_fields],
        acknowledgment=r.acknowledgment,
        question=r.question,
        stop=r.stop,
        status=r.status,
        need_type=r.need_type,
        stop_reason=r.stop_reason,
    )
