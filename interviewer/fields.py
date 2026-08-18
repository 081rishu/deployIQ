"""Interviewer field/question registry.

Declarative definition of every piece of information the interviewer can
collect, generalized across the two locked sectors. Each FieldSpec drives:
  - what the extraction step looks for,
  - how the question-generation step phrases a natural question,
  - how the priority step ranks missing fields (decision-relevance).

Design notes:
  * FieldSpecs are the ONLY place these details live — the engine reads
    from here, it does not hardcode questions.
  * `priority` is the deterministic tie-breaker for "which missing field
    do we ask about next". Higher = asked earlier.
  * `feeds` records which scores the field affects (economic/feasibility/
    risk) so the stop-condition can tell "enough to run the numbers".
  * Every number field must be asked as a range where sensible; the spec
    forbids false precision (spec 4.4).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import EffortBand, Sector


class ValueType(str, Enum):
    NUMBER = "number"
    INT = "int"
    STRING = "string"
    BOOL = "bool"
    EFFORT = "effort"            # small/medium/large
    STRING_LIST = "string_list"
    RANGE = "range"              # min/max numeric range


class Score(str, Enum):
    ECONOMIC = "economic"
    FEASIBILITY = "feasibility"
    RISK = "risk"


class FieldSpec(BaseModel):
    key: str
    label: str                       # human label for review/report
    value_type: ValueType
    sectors: list[Sector]
    feeds: list[Score]               # which scores this informs
    priority: int = 0                # decision-relevance rank (higher = sooner)
    required_for_completion: bool = False
    # True = this field genuinely feeds the downstream analysis and must be
    # pursued. False = schema-only field; the interviewer never chases it.
    analysis_relevant: bool = True
    # True = a reasonable default can come from the sector benchmark pack, so
    # the interviewer won't block/ask for it if the user omits it (spec 10.4).
    benchmark_substitutable: bool = False
    # A question template the engine can adapt (never sent verbatim).
    probe: Optional[str] = None
    # Guidance for the extraction step on how to interpret this field.
    extraction_hint: Optional[str] = None
    # When the field is numeric, ask for a best/likely and a low/high bound.
    ask_range: bool = False


def _cs() -> list[Sector]:
    return [Sector.CUSTOMER_SUPPORT]


def _doc() -> list[Sector]:
    return [Sector.DOCUMENT_PROCESSING]


def _both() -> list[Sector]:
    return [Sector.CUSTOMER_SUPPORT, Sector.DOCUMENT_PROCESSING]


FIELDS: list[FieldSpec] = [
    # ---- Economic core (labor baseline) ----
    FieldSpec(
        key="process", label="Process being assessed", value_type=ValueType.STRING,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=90,
        required_for_completion=True,
        probe="Can you describe the exact process you want to assess? What is the end-to-end workflow?",
        extraction_hint="A named process/workflow the user wants to automate.",
    ),
    FieldSpec(
        key="monthly_volume", label="Monthly transaction volume", value_type=ValueType.INT,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=85,
        required_for_completion=True, ask_range=True,
        probe="How many of these units does your team handle in a typical month?",
        extraction_hint="Number of units/items/tickets processed per month.",
    ),
    FieldSpec(
        key="avg_time_per_unit_minutes", label="Avg handling time (minutes)",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=80, required_for_completion=True, ask_range=True,
        probe="How long does one unit take to handle, on average?",
        extraction_hint="Average minutes to process one unit.",
    ),
    FieldSpec(
        key="current_headcount", label="People currently on the process",
        value_type=ValueType.INT, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=75, required_for_completion=True, ask_range=True,
        probe="How many people are currently doing this work?",
        extraction_hint="Number of full-time-equivalent people.",
    ),
    FieldSpec(
        key="worker_role", label="Worker role", value_type=ValueType.STRING,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=70, analysis_relevant=False,
        probe="What role are these workers? (e.g. support agent, AP clerk)",
        extraction_hint="Job title/role of the people doing the process.",
    ),
    FieldSpec(
        key="fully_loaded_annual_cost", label="Fully loaded annual cost per worker",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=65, benchmark_substitutable=True, ask_range=True,
        probe="Roughly, what is the fully-loaded annual cost (salary + overhead) of one such worker?",
        extraction_hint="Annual cost including salary, benefits, overhead.",
    ),
    FieldSpec(
        key="fraction_time_on_process", label="Fraction of time spent on this process",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=60, benchmark_substitutable=True, ask_range=True,
        probe="What fraction of these workers' time is spent on this specific process? (e.g. 0.6 = 60%)",
        extraction_hint="A fraction 0-1 or percent of worker time on this process.",
    ),
    # ---- Feasibility ----
    FieldSpec(
        key="required_accuracy", label="Required accuracy", value_type=ValueType.RANGE,
        sectors=_both(), feeds=[Score.FEASIBILITY, Score.RISK], priority=55,
        required_for_completion=True,
        probe="What level of accuracy does this task require? Give a range if you can.",
        extraction_hint="Accuracy requirement 0-1 or percent, ideally a range.",
    ),
    FieldSpec(
        key="existing_data", label="Existing data readiness",
        value_type=ValueType.STRING, sectors=_both(), feeds=[Score.FEASIBILITY],
        priority=50,
        probe="What data do you already have that an AI could learn from? (e.g. chat logs, invoices)",
        extraction_hint="Description of available/labelled data.",
    ),
    FieldSpec(
        key="current_tools", label="Current tools", value_type=ValueType.STRING_LIST,
        sectors=_both(), feeds=[Score.FEASIBILITY], priority=45,
        probe="What tools or systems is this process currently running on?",
        extraction_hint="Names of current software/systems/tools.",
    ),
    FieldSpec(
        key="integration_complexity", label="Integration complexity",
        value_type=ValueType.EFFORT, sectors=_both(), feeds=[Score.FEASIBILITY],
        priority=40, required_for_completion=True,
        probe="How complex would integrating with your existing systems be? Small, Medium, or Large effort?",
        extraction_hint="Effort band: small/medium/large.",
    ),
    # ---- Risk ----
    FieldSpec(
        key="risk.failure_impact", label="Impact if the AI output is wrong",
        value_type=ValueType.STRING, sectors=_both(), feeds=[Score.RISK],
        priority=35, required_for_completion=True,
        probe="If the AI gets an answer wrong, how severe is the consequence? (financial, customer harm, operational)",
        extraction_hint="Description of consequence severity.",
    ),
    FieldSpec(
        key="risk.compliance_exposure", label="Compliance constraints",
        value_type=ValueType.STRING_LIST, sectors=_both(), feeds=[Score.RISK],
        priority=30,
        probe="Are there any compliance, regulatory, or privacy constraints on this process?",
        extraction_hint="List of compliance/regulatory/privacy constraints.",
    ),
    # ---- Staged implementation (economic, buy-vs-build) ----
    FieldSpec(
        key="process_stages", label="Implementation stages buy vs build",
        value_type=ValueType.STRING, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=20, analysis_relevant=False,
        probe="Would you tend to buy off-the-shelf tooling or build custom for this?",
        extraction_hint="Whether the org prefers buy or build for implementation stages.",
    ),
]


def get_field(key: str) -> Optional[FieldSpec]:
    for f in FIELDS:
        if f.key == key:
            return f
    return None


def fields_for_sector(sector: Sector) -> list[FieldSpec]:
    return [f for f in FIELDS if sector in f.sectors]


def required_fields(sector: Sector) -> list[FieldSpec]:
    return [f for f in fields_for_sector(sector) if f.required_for_completion]


def score_map() -> dict[Score, list[str]]:
    """Score -> list of field keys that feed it."""
    m: dict[Score, list[str]] = {}
    for f in FIELDS:
        for s in f.feeds:
            m.setdefault(s, []).append(f.key)
    return m
