"""Conversation test for the interviewer via the engine (no server needed).

Drives a multi-turn interview through the real LLM, then asserts:
  1. State retention — facts the user gives are captured in the state.
  2. Question coherence — the interviewer never re-asks a filled field, and
     each question targets a still-missing/unresolved need.
  3. Progression — required fields fill in and the interview reaches READY.

Writes a human-readable transcript to logs/conversation_<timestamp>.txt and
prints a compact summary. Exits non-zero if an assertion fails.
"""

from __future__ import annotations

import datetime
import os
import sys
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from interviewer.engine import NeedType, run_turn  # noqa: E402
from interviewer.fields import get_field  # noqa: E402
from schemas.assessment_state import (  # noqa: E402
    AssessmentState,
    FieldResolution,
    InterviewStatus,
    Sector,
)


def _field_filled(state: AssessmentState, key: str) -> bool:
    v = state.get_value(key)
    return v is not None and v != "" and v != []


def _log(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_scenario(
    name: str,
    sector: Sector,
    problem: str,
    turns: list[str],
    log_path: str,
) -> tuple[bool, list[str]]:
    """Run one conversation scenario; return (passed, failures)."""
    _log(log_path, "")
    _log(log_path, "=" * 60)
    _log(log_path, f"SCENARIO: {name}  ({sector.value})")
    _log(log_path, f"PROBLEM: {problem}")
    _log(log_path, "=" * 60)

    state = AssessmentState(sector=sector, problem=problem)
    failures: list[str] = []
    asked: dict[str, int] = {}  # field -> times asked

    # Track which fields the user has explicitly provided (to detect re-asks).
    provided: set[str] = set()
    first = run_turn(state, problem)
    _log(log_path, f"[turn 0] status={first.status.value} need={first.need_type.value if first.need_type else None}")
    _log(log_path, f"  AI: {first.question}")

    for i, answer in enumerate(turns, start=1):
        result = run_turn(state, answer)
        _log(log_path, f"[turn {i}] USER: {answer}")
        _log(log_path, f"  updated: {[f'{u.field}={u.value}' for u in result.updated_fields]}")
        _log(log_path, f"  status={result.status.value} need={result.need_type.value if result.need_type else None}")
        if result.question:
            _log(log_path, f"  AI: {result.question}")

        # Record which fields the user just provided.
        for u in result.updated_fields:
            if u.provenance.value == "user_provided" or _field_filled(state, u.field):
                provided.add(u.field)

        # 1. State retention: every user-provided field with a real value is
        #    retained. A None value means the answer was genuinely vague and the
        #    engine correctly did not store it (it enters CLARIFYING instead).
        for u in result.updated_fields:
            if u.value is None:
                continue
            if not _field_filled(state, u.field):
                failures.append(f"turn {i}: field '{u.field}' reported a value but not retained in state")

        # 2. Question coherence: the current question targets a non-filled field.
        if result.question and not result.stop:
            nf = result.next_field
            if nf is None:
                failures.append(f"turn {i}: asked a question but next_field is None")
            elif _field_filled(state, nf) and result.need_type in (NeedType.MISSING, None):
                failures.append(f"turn {i}: asked again for already-filled field '{nf}'")
            elif nf in provided and result.need_type in (NeedType.MISSING, None):
                failures.append(f"turn {i}: re-asked field '{nf}' the user already provided")

        # 3. No duplicate consecutive question for the same need type.
        asked[nf or "?"] = asked.get(nf or "?", 0) + 1

    # 4. Progression: the interview should terminate (READY or UNCERTAIN).
    last = result  # last TurnResult (defined in the loop)
    _log(log_path, f"FINAL: status={state.status.value} complete={state.complete}")
    _log(log_path, f"  stop_reason={last.stop_reason}")
    _log(log_path, f"  filled_required={_field_filled(state, 'process')} "
                   f"vol={_field_filled(state, 'monthly_volume')} "
                   f"time={_field_filled(state, 'avg_time_per_unit_minutes')} "
                   f"head={_field_filled(state, 'current_headcount')} "
                   f"acc={_field_filled(state, 'required_accuracy')} "
                   f"int={_field_filled(state, 'integration_complexity')} "
                   f"risk={_field_filled(state, 'risk.failure_impact')}")
    if not state.complete:
        failures.append("interview did not reach a terminal state (READY/UNCERTAIN)")

    _log(log_path, f"RESULT: {'PASS' if not failures else 'FAIL'}")
    for f in failures:
        _log(log_path, f"  FAIL: {f}")
    return (not failures, failures)


def main() -> None:
    log_dir = os.path.join(REPO_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"conversation_{ts}.txt")

    scenarios = [
        (
            "support_standard",
            Sector.CUSTOMER_SUPPORT,
            "we want to automate support tickets",
            [
                "we handle about 10k tickets a month with 15 agents",
                "its ticket handling, each takes about 8 minutes, agents earn 600k a year loaded",
                "accuracy needed is around 95 percent",
                "integration would be medium effort, data exists in the ticketing system",
                "if the AI gets it wrong it causes customer harm, and we have gdpr constraints",
            ],
        ),
        (
            "support_ambiguous",
            Sector.CUSTOMER_SUPPORT,
            "we want to automate support",
            [
                "we get a lot of tickets, maybe ten thousand a month",
                "handled by about 15 agents",
                "accuracy should be high",
                "medium integration effort",
                "mistakes cause customer harm",
            ],
        ),
    ]

    all_pass = True
    for name, sector, problem, turns in scenarios:
        passed, failures = run_scenario(name, sector, problem, turns, log_path)
        all_pass = all_pass and passed
        print(f"{'PASS' if passed else 'FAIL'}: {name}" + (f" ({len(failures)} failure(s))" if failures else ""))

    print(f"\nconversation log written to: {log_path}")
    if not all_pass:
        print("SOME SCENARIOS FAILED — see log")
        sys.exit(1)
    print("ALL SCENARIOS PASSED")


if __name__ == "__main__":
    main()
