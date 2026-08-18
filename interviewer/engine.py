"""AI interviewer engine — adaptive, 4-state.

State machine:
    INTERVIEWING  -> need more information (ask a NEW field)
    CLARIFYING    -> existing answer is ambiguous/insufficient (re-ask/deepen)
    READY         -> minimum sufficient state reached (stop)
    UNCERTAIN     -> cannot obtain reliable info after reasonable attempts

Design rules enforced here:
  * Stateless: the engine holds no server-side memory. The client ships the
    whole AssessmentState back each turn (see api/interview.py).
  * The engine controls WHAT is needed and WHEN the interview terminates —
    deterministic logic only. The LLM only interprets the latest message and
    phrases the question naturally. It never decides which field to ask.
  * A need may be: a missing field, an ambiguous answer, a low-confidence
    estimate, contradictory information, or a need to drill deeper.
  * Never chase a field that is irrelevant to the downstream analysis just
    because it exists in the schema (FieldSpec.analysis_relevant) or is
    satisfiable by a benchmark default (FieldSpec.benchmark_substitutable).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from interviewer.fields import (
    FIELDS,
    FieldSpec,
    ValueType,
    get_field,
)
from lib.logging_config import get_logger
from llm.openai_client import complete_json
from schemas.assessment_state import (
    AssessmentState,
    FieldResolution,
    InterviewStatus,
    Provenance,
    Sector,
)

log = get_logger("interviewer.engine")

MAX_ATTEMPTS_PER_FIELD = 3
MAX_QUESTIONS = 12


class NeedType(str, Enum):
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    LOW_CONFIDENCE = "low_confidence"
    CONTRADICTION = "contradiction"
    DRILL_DEEPER = "drill_deeper"


@dataclass
class ExtractedUpdate:
    field: str
    value: Any
    provenance: Provenance
    raw_confidence: Optional[str] = None
    ambiguous: bool = False
    low_confidence: bool = False
    contradiction: bool = False
    needs_detail: bool = False


@dataclass
class Need:
    field: FieldSpec
    need_type: NeedType
    attempts: int
    score: float


@dataclass
class TurnResult:
    state: AssessmentState
    updated_fields: list[ExtractedUpdate] = field(default_factory=list)
    question: Optional[str] = None
    acknowledgment: Optional[str] = None
    stop: bool = False
    status: InterviewStatus = InterviewStatus.INTERVIEWING
    stop_reason: Optional[str] = None
    need_type: Optional[NeedType] = None
    next_field: Optional[str] = None


def _field_registry_json(sector: Sector) -> str:
    """Serialize the applicable field specs to JSON for the LLM prompt."""
    from interviewer.fields import fields_for_sector

    def _band_members() -> list[str]:
        return ["small", "medium", "large"]

    rows = []
    for f in fields_for_sector(sector):
        t = f.value_type.value
        if f.value_type == ValueType.EFFORT:
            t = "string (one of: " + ", ".join(_band_members()) + ")"
        rows.append({
            "key": f.key,
            "label": f.label,
            "value_type": t,
            "probe": f.probe,
            "extraction_hint": f.extraction_hint,
            "ask_range": f.ask_range,
        })
    return json.dumps(rows, indent=2)


def _current_values(state: AssessmentState) -> dict[str, Any]:
    """Current known values keyed by field, for the LLM context."""
    known = {}
    for f in FIELDS:
        v = state.get_value(f.key)
        if v is not None and v != "" and v != []:
            known[f.key] = v
    known["sector"] = state.sector.value
    return known


def _extract(state: AssessmentState, message: str) -> list[ExtractedUpdate]:
    """Extraction call: pull every fact from the latest message and flag the
    quality of each extracted value (ambiguous / low-confidence / needs detail
    / contradiction). The deterministic layer uses these flags to decide the
    next need."""
    registry = _field_registry_json(state.sector)
    system = (
        "You are the extraction step of an adaptive AI-deployment interviewer. "
        "Given the user's latest answer, extract every fact it contains into "
        "the available structured fields, and judge the quality of each.\n\n"
        "RULES:\n"
        "- Only fill fields present in the registry. Never invent new fields.\n"
        "- value_type 'number'/'int'/'range': parse numeric. If the user gives "
        "a single value where a range is expected, set min=max=that value.\n"
        "- value_type 'effort': output exactly one of small/medium/large.\n"
        "- value_type 'string_list': output a list of strings.\n"
        "- provenance: 'user_provided' if the user states a concrete number or "
        "fact in their message — even if phrased casually (e.g. \"about 10k "
        "tickets\", \"15 agents\", \"95%\", \"8 minutes\"). These are "
        "user_provided, NOT estimated. Use 'estimated' ONLY when you must "
        "infer/guess a value the user did not give (e.g. a plausible default, "
        "or a figure you reason out from context). NEVER upgrade an inferred "
        "value to user_provided, and never downgrade a stated value to "
        "estimated.\n"
        "- Per extracted field, set flags to describe quality:\n"
        "  * ambiguous: true if the value is vague, could mean several things, "
        "or the user did not actually answer the question.\n"
        "  * low_confidence: true if you had to estimate broadly or the answer "
        "is rough/unreliable.\n"
        "  * needs_detail: true if the answer is directionally useful but the "
        "downstream analysis would need more depth (e.g. a very wide range).\n"
        "  * contradiction: true if the user's answer conflicts with an "
        "existing value already in the current state for that field.\n"
        "  Default each flag to false unless it clearly applies.\n"
        "\n"
        "OUTPUT FORMAT (STRICT): return JSON with exactly one key, 'updates', "
        "an array of objects. Each object: 'field' (a registry key), 'value' "
        "(extracted value or null), 'provenance' (user_provided|estimated), and "
        "the boolean flags above. Example: {\"updates\": [{\"field\": "
        "\"monthly_volume\", \"value\": 10000, \"provenance\": \"user_provided\", "
        "\"ambiguous\": false}]}. Do NOT return bare top-level fields.\n\n"
        f"FIELD REGISTRY (applicable fields):\n{registry}"
    )
    user = (
        f"Current assessment state:\n{json.dumps(_current_values(state), indent=2)}\n\n"
        f"Latest user message:\n\"{message}\""
    )
    result = complete_json(system, user)
    updates = []
    raw_updates = result.get("updates")
    if raw_updates is None:
        raw_updates = [
            {"field": k, "value": v, "provenance": "user_provided"}
            for k, v in result.items()
            if get_field(k)
        ]
    for u in raw_updates:
        if not isinstance(u, dict):
            continue
        key = u.get("field")
        if not get_field(key):
            continue
        prov = u.get("provenance", "user_provided")
        try:
            provenance = Provenance(prov)
        except ValueError:
            provenance = Provenance.ESTIMATED
        updates.append(
            ExtractedUpdate(
                field=key,
                value=u.get("value"),
                provenance=provenance,
                raw_confidence=u.get("confidence"),
                ambiguous=bool(u.get("ambiguous")),
                low_confidence=bool(u.get("low_confidence")),
                contradiction=bool(u.get("contradiction")),
                needs_detail=bool(u.get("needs_detail")),
            )
        )
    return updates


def _apply_updates(state: AssessmentState, updates: list[ExtractedUpdate]) -> list[ExtractedUpdate]:
    """Write extracted values into the state, tag provenance, and record the
    per-field resolution (unless the value is a contradiction we should keep
    investigating rather than overwrite silently)."""
    applied: list[ExtractedUpdate] = []
    for u in updates:
        f = get_field(u.field)
        if not f:
            continue
        meta = state.get_meta(u.field)
        meta.attempts += 1

        has_value = u.value is not None or u.provenance == Provenance.USER_PROVIDED

        # Resolution from quality flags (deterministic).
        if u.contradiction:
            state.set_resolution(u.field, FieldResolution.CONTRADICTORY,
                                 reason="user's answer conflicts with an existing value")
        elif u.ambiguous and has_value:
            # Ambiguous wording but a usable value was given — keep it as a
            # low-confidence estimate rather than dropping it (prevents loops).
            state.set_resolution(u.field, FieldResolution.LOW_CONFIDENCE,
                                 reason="ambiguous wording; value retained as low-confidence")
        elif u.ambiguous:
            state.set_resolution(u.field, FieldResolution.AMBIGUOUS,
                                 reason="answer was ambiguous or did not answer")
        elif u.needs_detail:
            state.set_resolution(u.field, FieldResolution.NEEDS_DETAIL,
                                 reason="directionally useful but needs more depth")
        elif u.low_confidence:
            state.set_resolution(u.field, FieldResolution.LOW_CONFIDENCE,
                                 reason="answer was rough/unreliable")
        elif u.value is not None or u.provenance == Provenance.USER_PROVIDED:
            state.set_resolution(u.field, FieldResolution.RESOLVED)

        # Store value whenever a usable one was provided.
        if has_value:
            state.set_value(u.field, u.value)
            state.tag(u.field, u.provenance)
        applied.append(u)
    return applied


def _resolved(state: AssessmentState, f: FieldSpec) -> bool:
    val = state.get_value(f.key)
    if val is None or val == "" or val == []:
        return False
    # A value plus RESOLVED or LOW_CONFIDENCE counts as usable for minimum-
    # sufficient; AMBIGUOUS (no value) and CONTRADICTORY still need attention.
    return state.get_meta(f.key).status in (FieldResolution.RESOLVED, FieldResolution.LOW_CONFIDENCE)


def _need_type_for(status: FieldResolution) -> NeedType:
    return {
        FieldResolution.AMBIGUOUS: NeedType.AMBIGUOUS,
        FieldResolution.LOW_CONFIDENCE: NeedType.LOW_CONFIDENCE,
        FieldResolution.CONTRADICTORY: NeedType.CONTRADICTION,
        FieldResolution.NEEDS_DETAIL: NeedType.DRILL_DEEPER,
    }.get(status, NeedType.MISSING)


def _need_score(f: FieldSpec, need_type: NeedType) -> float:
    bonus = {
        NeedType.CONTRADICTION: 60,
        NeedType.AMBIGUOUS: 50,
        NeedType.LOW_CONFIDENCE: 40,
        NeedType.DRILL_DEEPER: 30,
        NeedType.MISSING: 0,
    }
    base = float(f.priority)
    if f.required_for_completion:
        base += 10
    return base + bonus[need_type]


def select_next_need(state: AssessmentState) -> Optional[Need]:
    """Deterministically pick the single highest-value unresolved need.

    Considers every analysis-relevant field that is not yet resolved and not
    satisfiable by a benchmark default, and ranks candidates by decision
    relevance (priority) + the severity of the unresolved state.
    """
    from interviewer.fields import fields_for_sector

    candidates: list[Need] = []
    for f in fields_for_sector(state.sector):
        if not f.analysis_relevant:
            continue
        val = state.get_value(f.key)
        if f.benchmark_substitutable and (val is None or val == "" or val == []):
            # Satisfiable by the benchmark pack — never chase it.
            continue
        if _resolved(state, f):
            continue
        meta = state.get_meta(f.key)
        need_type = _need_type_for(meta.status)
        score = _need_score(f, need_type)
        candidates.append(Need(field=f, need_type=need_type,
                               attempts=meta.attempts, score=score))
    if not candidates:
        return None
    candidates.sort(key=lambda n: (n.score, n.field.priority), reverse=True)
    return candidates[0]


def _minimum_sufficient_reached(state: AssessmentState) -> bool:
    """READY when every required, analysis-relevant, non-benchmark field is
    resolved. Benchmark-substitutable and optional fields are excluded by
    definition, so a minimum-sufficient state can be reached without them."""
    from interviewer.fields import fields_for_sector

    for f in fields_for_sector(state.sector):
        if not f.required_for_completion or not f.analysis_relevant:
            continue
        if f.benchmark_substitutable:
            continue
        if not _resolved(state, f):
            return False
    return True


def _generate_question(state: AssessmentState, need: Need, last_message: str) -> dict[str, Any]:
    """Question-generation call: phrase ONE natural question for the need.

    For CLARIFYING needs, ask a targeted follow-up on the same field instead
    of re-asking the broad original question (spec 10.3/10.6)."""
    mode = "clarify/deepen an existing answer"
    if need.need_type == NeedType.MISSING:
        mode = "ask for a new missing field"
    system = (
        "You are the question-generation step of an adaptive AI-deployment "
        "interviewer. You must produce exactly ONE natural, conversational "
        f"turn that addresses the need to {mode}.\n\n"
        "RULES:\n"
        "- Ask for exactly one piece of information. Never bundle two questions.\n"
        "- Sound like a real conversation, not a form. Vary wording from the "
        "template; feel free to acknowledge what the user just said.\n"
        "- If the field is numeric and ask_range is true, invite a range "
        "(e.g. 'roughly...? any best/likely and a low and high bound').\n"
        "- If the field has a fixed set (effort bands), give the choices "
        "(small/medium/large) so the answer is a clean pick.\n"
        "- For CLARIFYING/DRILL_DEEPER/CONTRADICTION needs: reference the "
        "existing value and ask ONE targeted follow-up to sharpen or reconcile "
        "it, not the same broad question again.\n"
        f"- The specific need type is: {need.need_type.value}.\n"
        "- Return ONLY JSON with keys: 'acknowledgment' (optional short "
        "acknowledgement of the last answer), 'question' (the single question)."
    )
    user = (
        f"Current state:\n{json.dumps(_current_values(state), indent=2)}\n\n"
        f"Last user message:\n\"{last_message}\"\n\n"
        f"Field to {mode}:\n{json.dumps(need.field.model_dump(), indent=2)}"
    )
    return complete_json(system, user)


def run_turn(state: AssessmentState, user_message: str) -> TurnResult:
    """Run one interviewer turn on the given state + latest user message."""
    if state.complete or state.status in (InterviewStatus.READY, InterviewStatus.UNCERTAIN):
        log.info("turn skipped: already terminated (status=%s)", state.status.value)
        return TurnResult(state=state, stop=True, status=state.status,
                          stop_reason="already terminated")

    state.turn_count += 1
    log.info("turn=%d sector=%s msg=%r", state.turn_count, state.sector.value, user_message)

    # 1. Extraction + deterministic update/resolution.
    extracted = _extract(state, user_message)
    applied = _apply_updates(state, extracted)
    log.info("turn=%d extracted=%d applied=%d", state.turn_count, len(extracted), len(applied))
    if applied:
        log.debug("turn=%d updates=%s", state.turn_count,
                  [(u.field, u.value, u.provenance.value) for u in applied])

    # 2. Select the highest-value unresolved need (deterministic).
    need = select_next_need(state)
    if need:
        # A CLARIFYING need that the user keeps failing to resolve must grow
        # attempts so it eventually reaches UNCERTAIN instead of looping.
        if need.need_type != NeedType.MISSING:
            state.get_meta(need.field.key).attempts += 1
            need.attempts = state.get_meta(need.field.key).attempts
        log.info("turn=%d need=%s type=%s attempts=%d", state.turn_count,
                 need.field.key, need.need_type.value, need.attempts)
    else:
        log.info("turn=%d no unresolved need", state.turn_count)

    # 3. Determine the state.
    if need is None or _minimum_sufficient_reached(state):
        state.complete = True
        state.status = InterviewStatus.READY
        log.info("turn=%d -> READY (minimum sufficient state)", state.turn_count)
        return TurnResult(state=state, updated_fields=applied, stop=True,
                          status=InterviewStatus.READY,
                          stop_reason="minimum sufficient state reached")

    if need.attempts >= MAX_ATTEMPTS_PER_FIELD:
        state.complete = True
        state.status = InterviewStatus.UNCERTAIN
        log.warning("turn=%d -> UNCERTAIN (field=%s attempts=%d)", state.turn_count,
                    need.field.key, need.attempts)
        return TurnResult(state=state, updated_fields=applied, stop=True,
                          status=InterviewStatus.UNCERTAIN,
                          stop_reason=f"could not obtain reliable value for "
                                      f"'{need.field.key}' after {MAX_ATTEMPTS_PER_FIELD} attempts",
                          need_type=need.need_type, next_field=need.field.key)

    if state.turn_count >= MAX_QUESTIONS:
        state.complete = True
        state.status = InterviewStatus.UNCERTAIN
        log.warning("turn=%d -> UNCERTAIN (question cap=%d)", state.turn_count, MAX_QUESTIONS)
        return TurnResult(state=state, updated_fields=applied, stop=True,
                          status=InterviewStatus.UNCERTAIN,
                          stop_reason=f"question cap ({MAX_QUESTIONS}) reached",
                          need_type=need.need_type, next_field=need.field.key)

    if need.need_type == NeedType.MISSING:
        state.status = InterviewStatus.INTERVIEWING
    else:
        state.status = InterviewStatus.CLARIFYING
    log.info("turn=%d -> %s (field=%s)", state.turn_count, state.status.value, need.field.key)

    # 4. Phrase the question for the chosen need (LLM = language only).
    q = _generate_question(state, need, user_message)
    return TurnResult(
        state=state,
        updated_fields=applied,
        question=q.get("question"),
        acknowledgment=q.get("acknowledgment"),
        stop=False,
        status=state.status,
        need_type=need.need_type,
        next_field=need.field.key,
    )
