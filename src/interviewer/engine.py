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

from pydantic import BaseModel
from enum import Enum
from typing import Any, Optional

from interviewer.conversation import (
    ConversationContext,
    Phase,
    opening_prompt,
    transition_hint,
    warmup_prompt,
)
from interviewer.fields import (
    FIELDS,
    FieldSpec,
    Tier,
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
# Tier-2 questions are asked only while this much of the budget remains, so
# enrichment can never crowd out a clarification on a Tier-1 answer.
# Reserved turns at the END of the budget in which Tier-2 will not be asked.
#
# Zero, deliberately. The name suggests it protects Tier 2, but it did the
# opposite: it closed the Tier-2 window three turns early, and since Tier 1 is
# always selected first, Tier 1 alone consumed the budget before the window
# opened. Staff cost was never asked and the economics came back partial.
# Tier 1 is prioritised by selection, so there is nothing left to reserve for;
# MAX_QUESTIONS alone bounds the interview.
TIER2_BUDGET_RESERVE = 0


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
    context: Optional[ConversationContext] = None
    updated_fields: list[ExtractedUpdate] = field(default_factory=list)
    question: Optional[str] = None
    acknowledgment: Optional[str] = None
    stop: bool = False
    status: InterviewStatus = InterviewStatus.INTERVIEWING
    stop_reason: Optional[str] = None
    need_type: Optional[NeedType] = None
    next_field: Optional[str] = None
    phase: Optional[str] = None
    # Tier-2 fields never established, reported at termination so the user
    # learns what would have improved the assessment (fix spec 5/17).
    uncollected_tier2: list[str] = field(default_factory=list)
    completion_statement: str = ""


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
        elif f.value_type == ValueType.READINESS:
            t = "string (one of: none, minimal, partial, good, excellent)"
        elif f.value_type == ValueType.SEVERITY:
            t = "string (one of: negligible, minor, moderate, major, severe)"
        rows.append({
            "key": f.key,
            "label": f.label,
            "value_type": t,
            "probe": f.probe,
            "extraction_hint": f.extraction_hint,
            "ask_range": f.ask_range,
        })
    return json.dumps(rows, indent=2)


def _render(value: Any) -> Any:
    """Render a stored value for the LLM prompt.

    Ranges are shown as "10000-15000" rather than a nested object: the model
    only needs to know what is already answered, and a point range reads as a
    single number so it never looks like an invented spread.
    """
    from schemas.assessment_state import RangeEstimate

    if isinstance(value, RangeEstimate):
        if value.min == value.max:
            return value.min
        return f"{value.min:g}-{value.max:g}"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_render(v) for v in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _current_values(state: AssessmentState) -> dict[str, Any]:
    """Current known values keyed by field, for the LLM context."""
    known = {}
    for f in FIELDS:
        v = state.get_value(f.key)
        if v is not None and v != "" and v != []:
            known[f.key] = _render(v)
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
        "- value_type 'readiness'/'severity': output exactly one of the listed "
        "values. Classify from whatever the user described — these are usually "
        "inferable from an answer given to another question, so fill them "
        "whenever the message supports it rather than waiting to be asked.\n"
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
        # NOTE: attempts counts how many times the interviewer ASKED about a
        # field (incremented in run_turn), not how many times extraction
        # touched it — a volunteered fact must not burn an attempt.
        has_value = u.value is not None and u.value != "" and u.value != []

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
        elif has_value:
            state.set_resolution(u.field, FieldResolution.RESOLVED)

        # Store only a usable value — a null/empty extraction must never
        # overwrite a value already collected on an earlier turn.
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


def _tier1_complete(state: AssessmentState) -> bool:
    """Every chased, non-benchmark Tier-1 field is resolved."""
    from interviewer.fields import fields_for_sector

    for f in fields_for_sector(state.sector):
        if f.tier != Tier.TIER_1 or not f.analysis_relevant:
            continue
        if f.benchmark_substitutable:
            continue
        if not _resolved(state, f):
            return False
    return True


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


def _generate_question(state: AssessmentState, need: Need, last_message: str,
                       transition: Optional[str] = None) -> dict[str, Any]:
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
        "- Ask for the PRIMARY fact. If companion facts are listed below, you "
        "may invite them in the same natural sentence — the way a person would "
        "actually ask. Do not turn it into a list of separate questions, and "
        "never exceed the facts given to you.\n"
        "- Companions are optional: if the user answers only the primary fact, "
        "that is a complete answer and the rest will be asked later.\n"
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
    if transition:
        system += "\n\nTRANSITION: " + transition
    user = (
        f"Current state:\n{json.dumps(_current_values(state), indent=2)}\n\n"
        f"Last user message:\n\"{last_message}\"\n\n"
        f"PRIMARY field to {mode}:\n{json.dumps(need.field.model_dump(), indent=2)}"
    )
    companions = _companion_specs(state, need)
    if companions:
        user += ("\n\nCompanion facts you may invite in the same sentence:\n"
                 + json.dumps(companions, indent=2))
    return complete_json(system, user)


def _companion_specs(state: AssessmentState, need: Need) -> list[dict]:
    """Which other fields may ride along with this question.

    Deterministic: the grouping is declared in fields.py and filtered here to
    what is still unanswered and applicable. The LLM receives the list and
    phrases it; it never chooses what to ask.

    Only for MISSING needs — a clarification is already narrow, and widening
    it would lose the point of asking again.
    """
    from interviewer.fields import (MAX_FACTS_PER_QUESTION, companions_for,
                                    fields_for_sector, get_field)

    if need.need_type != NeedType.MISSING:
        return []
    applicable = {f.key for f in fields_for_sector(state.sector)}
    out = []
    for key in companions_for(need.field.key):
        if key not in applicable or _resolved(state, get_field(key)):
            continue
        spec = get_field(key)
        if spec is None:
            continue
        out.append({"key": spec.key, "label": spec.label,
                    "value_type": spec.value_type.value,
                    "ask_range": spec.ask_range, "probe": spec.probe})
        if len(out) >= MAX_FACTS_PER_QUESTION - 1:
            break
    return out


def _extract_name(message: str) -> Optional[str]:
    """Pull a name out of a warm-up reply. Deliberately conservative.

    This is conversational metadata only — it is stored on the
    ConversationContext and never reaches AssessmentState, so a wrong guess
    costs a slightly awkward greeting and nothing else.
    """
    import re
    text = message.strip()
    for pat in (r"\bI'?m\s+([A-Z][a-z]+)", r"\bmy name is\s+([A-Z][a-z]+)",
                r"\bthis is\s+([A-Z][a-z]+)", r"^([A-Z][a-z]+)[.,!]?$"):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _known_summary(state: AssessmentState) -> str:
    known = _current_values(state)
    known.pop("sector", None)
    if not known:
        return ""
    return ", ".join(f"{k}={v}" for k, v in list(known.items())[:6])


# Tier-2 fields grouped for a readable closing statement, in the words a
# person would use rather than field labels.
_TIER2_PHRASES = {
    "annual_tooling_cost": "current tooling cost",
    "monthly_tooling_cost": "current tooling cost",
    "error_rate": "current rework rate",
    "rework_time_per_error_minutes": "rework effort",
    "annual_other_direct_cost": "other direct operating costs",
    "current_quality_metric": "current quality rate",
    "current_quality_value": "current quality rate",
    "risk.compliance_exposure": "compliance requirements",
    "fully_loaded_annual_cost": "fully loaded staff cost",
    "fraction_time_on_process": "share of time spent on this process",
    "existing_data": "what data already exists",
    "integration_complexity": "your own view of integration complexity",
    "risk.failure_impact_severity": "how severe a wrong output would be",
}


def completion_statement(state: AssessmentState, status: InterviewStatus) -> str:
    """A plain closing sentence naming what could not be established.

    Transparency at the point the user can still do something about it: the
    ABSENT lines otherwise only surface deep inside the report.
    """
    missing = uncollected_tier2(state, as_phrases=True)
    if status == InterviewStatus.UNCERTAIN:
        head = "The assessment is incomplete."
    else:
        head = "Your assessment is complete."
    if not missing:
        return f"{head} Every input we look for was established."
    shown, overflow = missing[:4], len(missing) - 4
    if len(shown) == 1:
        listed = shown[0]
    elif len(shown) == 2:
        listed = f"{shown[0]} and {shown[1]}"
    else:
        listed = ", ".join(shown[:-1]) + f" and {shown[-1]}"
    if overflow > 0:
        listed += f", plus {overflow} other input{'s' if overflow > 1 else ''}"
    tail = ("that was" if len(missing) == 1 else "those were")
    return (f"{head} We couldn't establish {listed}, so {tail} excluded from "
            f"the analysis rather than estimated.")


def uncollected_tier2(state: AssessmentState, as_phrases: bool = False) -> list[str]:
    """Tier-2 fields that were never filled (fix spec 5/17).

    Reported at termination so the user learns what would have improved the
    assessment, rather than the gap only surfacing as an ABSENT line much
    later in the report.
    """
    from interviewer.fields import fields_for_sector
    out = []
    for f in fields_for_sector(state.sector):
        if f.tier != Tier.TIER_2:
            continue
        v = state.get_value(f.key)
        if v is None or v == "" or v == []:
            out.append(_TIER2_PHRASES.get(f.key, f.label) if as_phrases else f.label)
    # De-duplicate phrases (several fields map to one human phrase) while
    # keeping order.
    seen, unique = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _warmup_turn(
    state: AssessmentState, context: ConversationContext, user_message: str,
) -> Optional[TurnResult]:
    """Run a warm-up turn, or return None if warm-up is over.

    Facts volunteered during warm-up are extracted exactly as in discovery —
    someone who opens with "we're a BPO in India handling 5,000 tickets a
    month" should never be asked those things again.
    """
    context.note_turn(user_message)

    if context.warmup_turns == 0 and not user_message.strip():
        context.warmup_turns = 1
        q = complete_json(opening_prompt(), "Begin the conversation.")
        return TurnResult(state=state, context=context, question=q.get("question"),
                          acknowledgment=q.get("acknowledgment"),
                          status=InterviewStatus.INTERVIEWING,
                          phase=Phase.WARMUP.value)

    # Opportunistic extraction: warm-up answers can carry real facts.
    applied = _apply_updates(state, _extract(state, user_message))
    if context.name is None:
        context.name = _extract_name(user_message)

    facts = len([f for f in FIELDS if state.get_value(f.key) not in (None, "", [])])
    context.warmup_turns += 1

    if not context.should_warm_up(facts):
        context.complete_warmup()
        return None

    q = complete_json(warmup_prompt(context, _known_summary(state)),
                      f"The user just said: \"{user_message}\"")
    return TurnResult(state=state, context=context, updated_fields=applied,
                      question=q.get("question"),
                      acknowledgment=q.get("acknowledgment"),
                      status=InterviewStatus.INTERVIEWING,
                      phase=Phase.WARMUP.value)


def run_turn(state: AssessmentState, user_message: str,
             context: Optional[ConversationContext] = None) -> TurnResult:
    """Run one interviewer turn on the given state + latest user message."""
    # A caller that does not manage ConversationContext gets NO warm-up. A
    # fresh context per turn would otherwise reset warmup_turns every call and
    # loop the greeting forever — the warm-up is opt-in for callers that
    # actually ship the context back.
    if context is None:
        context = ConversationContext(warmup_completed=True, phase=Phase.DISCOVERY)
    if state.complete or state.status in (InterviewStatus.READY, InterviewStatus.UNCERTAIN):
        log.info("turn skipped: already terminated (status=%s)", state.status.value)
        return TurnResult(state=state, context=context, stop=True,
                          status=state.status, stop_reason="already terminated")

    # Phase 1: warm up. Deterministic need selection is untouched — warm-up
    # only decides TONE and when to start, never which field is asked.
    if not context.warmup_completed:
        warm = _warmup_turn(state, context, user_message)
        if warm is not None:
            state.turn_count += 1
            return warm

    state.turn_count += 1
    log.info("turn=%d sector=%s message_chars=%d",
             state.turn_count, state.sector.value, len(user_message))

    # 1. Extraction + deterministic update/resolution.
    extracted = _extract(state, user_message)
    applied = _apply_updates(state, extracted)
    log.info("turn=%d extracted=%d applied=%d", state.turn_count, len(extracted), len(applied))
    if applied:
        log.debug("turn=%d updated_fields=%s", state.turn_count,
                  [(u.field, u.provenance.value) for u in applied])

    # 2. Select the highest-value unresolved need (deterministic).
    #    `need.attempts` is how many times we have ALREADY asked about this
    #    field; it is incremented in step 4, when we actually ask again.
    need = select_next_need(state)
    if need:
        log.info("turn=%d need=%s type=%s prior_attempts=%d", state.turn_count,
                 need.field.key, need.need_type.value, need.attempts)
    else:
        log.info("turn=%d no unresolved need", state.turn_count)

    # 3. Determine the state.
    #
    # Tier 1 is the completion gate. Once it is satisfied we keep going ONLY
    # while budget remains and only for Tier-2 enrichment — a user with time
    # gets asked about rework cost and current quality; a user without it
    # finishes, and those fields are reported as explicitly uncollected rather
    # than guessed (fix spec 5/11).
    tier1_done = _tier1_complete(state)
    budget_left = state.turn_count < (MAX_QUESTIONS - TIER2_BUDGET_RESERVE)
    if tier1_done and need is not None and need.field.tier != Tier.TIER_1:
        if not budget_left:
            need = None

    if need is None or (tier1_done and not budget_left):
        state.complete = True
        state.status = InterviewStatus.READY
        log.info("turn=%d -> READY (minimum sufficient state)", state.turn_count)
        context.phase = Phase.DONE
        return TurnResult(state=state, context=context, updated_fields=applied,
                          stop=True, status=InterviewStatus.READY,
                          stop_reason="minimum sufficient state reached",
                          phase=Phase.DONE.value,
                          uncollected_tier2=uncollected_tier2(state),
                          completion_statement=completion_statement(
                              state, InterviewStatus.READY))

    if need.attempts >= MAX_ATTEMPTS_PER_FIELD:
        state.complete = True
        state.status = InterviewStatus.UNCERTAIN
        log.warning("turn=%d -> UNCERTAIN (field=%s attempts=%d)", state.turn_count,
                    need.field.key, need.attempts)
        return TurnResult(state=state, updated_fields=applied, stop=True,
                          status=InterviewStatus.UNCERTAIN,
                          stop_reason=f"could not obtain reliable value for "
                                      f"'{need.field.key}' after "
                                      f"{MAX_ATTEMPTS_PER_FIELD} attempts",
                          need_type=need.need_type, next_field=need.field.key,
                          context=context, phase=Phase.DONE.value,
                          uncollected_tier2=uncollected_tier2(state),
                          completion_statement=completion_statement(
                              state, InterviewStatus.UNCERTAIN))

    if state.turn_count >= MAX_QUESTIONS:
        state.complete = True
        state.status = InterviewStatus.UNCERTAIN
        log.warning("turn=%d -> UNCERTAIN (question cap=%d)", state.turn_count, MAX_QUESTIONS)
        return TurnResult(state=state, updated_fields=applied, stop=True,
                          status=InterviewStatus.UNCERTAIN,
                          stop_reason=f"question cap ({MAX_QUESTIONS}) reached",
                          need_type=need.need_type, next_field=need.field.key,
                          context=context, phase=Phase.DONE.value,
                          uncollected_tier2=uncollected_tier2(state),
                          completion_statement=completion_statement(
                              state, InterviewStatus.UNCERTAIN))

    if need.need_type == NeedType.MISSING:
        state.status = InterviewStatus.INTERVIEWING
    else:
        state.status = InterviewStatus.CLARIFYING
    log.info("turn=%d -> %s (field=%s)", state.turn_count, state.status.value, need.field.key)

    # 4. We are about to ask about this field — that is one attempt. Counting
    #    it here (rather than on extraction) means MAX_ATTEMPTS_PER_FIELD is
    #    exactly "asked N times and still unresolved" (spec 10.6).
    meta = state.get_meta(need.field.key)
    meta.attempts += 1
    need.attempts = meta.attempts

    # 5. Phrase the question for the chosen need (LLM = language only).
    just_transitioned = context.phase == Phase.DISCOVERY and state.turn_count <= 3
    q = _generate_question(state, need, user_message,
                           transition=(transition_hint(context, need.field.label)
                                       if just_transitioned else None))
    context.phase = (Phase.CLARIFICATION if need.need_type != NeedType.MISSING
                     else Phase.DISCOVERY)
    return TurnResult(
        state=state,
        context=context,
        phase=context.phase.value,
        updated_fields=applied,
        question=q.get("question"),
        acknowledgment=q.get("acknowledgment"),
        stop=False,
        status=state.status,
        need_type=need.need_type,
        next_field=need.field.key,
    )
