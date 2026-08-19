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

from schemas.assessment_state import (
    CurrentQualityMetric,
    ProcessRole,
    DataReadiness,
    EffortBand,
    ImpactSeverity,
    Sector,
)


class ValueType(str, Enum):
    NUMBER = "number"
    PROCESS_ROLE = "process_role"       # canonical process-labor role
    QUALITY_METRIC = "quality_metric"   # sector-specific current-quality metric
    COMPLIANCE = "compliance"           # canonical standard + original wording
    READINESS = "readiness"      # none/minimal/partial/good/excellent
    SEVERITY = "severity"        # negligible/minor/moderate/major/severe
    INT = "int"
    STRING = "string"
    BOOL = "bool"
    EFFORT = "effort"            # small/medium/large
    STRING_LIST = "string_list"
    RANGE = "range"              # min/max numeric range


class Tier(str, Enum):
    """Fix spec 5. Tier decides what BLOCKS completion, not what is useful.

    TIER_1  decision-critical: without it no defensible analysis runs
    TIER_2  materially improves the assessment; asked if budget allows,
            otherwise reported ABSENT rather than guessed
    TIER_3  opportunistic: never asked directly, only filled when volunteered
    """
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


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
    tier: Tier = Tier.TIER_2
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
    # ---- Geography: Tier 1. Selects the labor-rate registry and derives the
    # currency. Without it calc/labor.py cannot resolve a rate at all, so the
    # Economic Engine refuses and the pipeline stops. ----
    FieldSpec(
        key="geography", label="Where the team is based",
        value_type=ValueType.STRING, sectors=_both(),
        feeds=[Score.ECONOMIC], priority=95, tier=Tier.TIER_1,
        required_for_completion=True,
        probe="Where is the team that does this work based?",
        extraction_hint=(
            "The country or region whose labor market applies to this process "
            "(e.g. India, US). Infer it from ANY mention of location, office or "
            "market. If the user names more than one, do NOT pick one — flag it "
            "as ambiguous so a follow-up can ask which to use for the baseline."),
    ),
    # ---- Economic core (labor baseline) ----
    FieldSpec(
        key="process", tier=Tier.TIER_1, label="Process being assessed", value_type=ValueType.STRING,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=90,
        required_for_completion=True,
        probe="Can you describe the exact process you want to assess? What is the end-to-end workflow?",
        extraction_hint="A named process/workflow the user wants to automate.",
    ),
    FieldSpec(
        key="monthly_volume", tier=Tier.TIER_1, label="Monthly transaction volume", value_type=ValueType.INT,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=85,
        required_for_completion=True, ask_range=True,
        probe="How many of these units does your team handle in a typical month?",
        extraction_hint="Number of units/items/tickets processed per month.",
    ),
    FieldSpec(
        key="avg_time_per_unit_minutes", tier=Tier.TIER_1, label="Avg handling time (minutes)",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=80, required_for_completion=True, ask_range=True,
        probe="How long does one unit take to handle, on average?",
        extraction_hint="Average minutes to process one unit.",
    ),
    FieldSpec(
        key="current_headcount", tier=Tier.TIER_1, label="People currently on the process",
        value_type=ValueType.INT, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=75, required_for_completion=True, ask_range=True,
        probe="How many people are currently doing this work?",
        extraction_hint="Number of full-time-equivalent people.",
    ),
    FieldSpec(
        key="worker_role", tier=Tier.TIER_3, label="Worker role (as described)",
        value_type=ValueType.STRING,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=70, analysis_relevant=False,
        probe="What role are these workers? (e.g. support agent, AP clerk)",
        extraction_hint="The user's own words for the role doing this process.",
    ),
    FieldSpec(
        key="worker_role_canonical", tier=Tier.TIER_3,
        label="Worker role (canonical)", value_type=ValueType.PROCESS_ROLE,
        sectors=_both(), feeds=[Score.ECONOMIC], priority=69,
        analysis_relevant=False,
        probe="Are these frontline agents, or more senior specialists handling escalations?",
        extraction_hint=(
            "Normalise the role to EXACTLY one of: customer_support_agent, "
            "customer_support_specialist, accounts_payable_clerk. "
            "'tier-2 support', 'escalation handler', 'senior support engineer' -> "
            "customer_support_specialist. 'support agent', 'frontline', 'call "
            "centre agent' -> customer_support_agent. 'AP clerk', 'invoice "
            "processor', 'accounts payable' -> accounts_payable_clerk. "
            "If the description is ambiguous between agent and specialist, mark "
            "it ambiguous rather than guessing — the two are priced very "
            "differently."),
    ),
    FieldSpec(
        key="fully_loaded_annual_cost", tier=Tier.TIER_2, label="Fully loaded annual cost per worker",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=65, benchmark_substitutable=True, ask_range=True,
        probe="Roughly, what is the fully-loaded annual cost (salary + overhead) of one such worker?",
        extraction_hint="Annual cost including salary, benefits, overhead.",
    ),
    FieldSpec(
        key="fraction_time_on_process", tier=Tier.TIER_2, label="Fraction of time spent on this process",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=60, benchmark_substitutable=True, ask_range=True,
        probe="What fraction of these workers' time is spent on this specific process? (e.g. 0.6 = 60%)",
        extraction_hint="A fraction 0-1 or percent of worker time on this process.",
    ),
    # ---- Feasibility ----
    FieldSpec(
        key="required_accuracy", tier=Tier.TIER_1, label="Required accuracy", value_type=ValueType.RANGE,
        sectors=_both(), feeds=[Score.FEASIBILITY, Score.RISK], priority=55,
        required_for_completion=True,
        probe="What level of accuracy does this task require? Give a range if you can.",
        extraction_hint="Accuracy requirement 0-1 or percent, ideally a range.",
    ),
    FieldSpec(
        key="existing_data", tier=Tier.TIER_2, label="Existing data readiness",
        value_type=ValueType.STRING, sectors=_both(), feeds=[Score.FEASIBILITY],
        priority=50,
        probe="What data do you already have that an AI could learn from? (e.g. chat logs, invoices)",
        extraction_hint="Description of available/labelled data.",
    ),
    FieldSpec(
        key="data_readiness", tier=Tier.TIER_1, label="Data readiness (category)",
        value_type=ValueType.READINESS, sectors=_both(),
        feeds=[Score.FEASIBILITY], priority=49, required_for_completion=True,
        probe="Overall, how ready is that data to train or ground an AI — none, minimal, partial, good, or excellent?",
        extraction_hint=(
            "Classify the user's description of their available data into exactly one of: "
            "none, minimal, partial, good, excellent. Infer this from ANY description of "
            "their data (volume, labelling, cleanliness, accessibility) — it usually does "
            "not need its own question."),
    ),
    FieldSpec(
        key="current_tools", tier=Tier.TIER_1, label="Current tools", value_type=ValueType.STRING_LIST,
        sectors=_both(), feeds=[Score.FEASIBILITY], priority=45,
        # D1: these are the observable facts the derived integration-complexity
        # band is computed from, so they are required where the band no longer is.
        required_for_completion=True,
        probe="What tools or systems is this process currently running on?",
        extraction_hint="Names of current software/systems/tools.",
    ),
    FieldSpec(
        key="integration_complexity", tier=Tier.TIER_2, label="Integration complexity (user's own view)",
        value_type=ValueType.EFFORT, sectors=_both(), feeds=[Score.FEASIBILITY],
        priority=40, required_for_completion=False, analysis_relevant=False,
        # D1: the estimator DERIVES this band from integration facts. Asking a
        # user to grade the complexity of a system that does not exist yet
        # spends an interview turn on a subjective value the analysis then
        # overrides. Kept in the schema as a cross-check when volunteered,
        # never chased.
        probe="How complex do you expect integrating with your existing systems to be?",
        extraction_hint="Effort band: small/medium/large, only if the user offers it.",
    ),
    # ---- Tier 2: current-cost components (spec 8.2). Optional by design —
    # the user is never pushed to invent a number, and an uncollected component
    # is reported ABSENT rather than becoming zero. ----
    FieldSpec(
        key="annual_tooling_cost", label="Annual tooling / infrastructure cost",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=28, tier=Tier.TIER_2, ask_range=True,
        probe="Are you paying for any software or infrastructure specifically for this process?",
        extraction_hint=(
            "Annual cost of tools/licences/infrastructure dedicated to THIS process. "
            "If the user gives a monthly figure, put it in monthly_tooling_cost "
            "instead — do not multiply it yourself."),
    ),
    FieldSpec(
        key="monthly_tooling_cost", label="Monthly tooling / infrastructure cost",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=27, tier=Tier.TIER_2, analysis_relevant=False, ask_range=True,
        probe="Roughly what does that tooling cost per month?",
        extraction_hint="Monthly tooling cost, only when the user states it monthly.",
    ),
    FieldSpec(
        key="error_rate", label="Share of items needing rework",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=26, tier=Tier.TIER_2, ask_range=True,
        probe="Roughly what share of these end up needing rework or correction?",
        extraction_hint="Fraction 0-1 (or percent) of items that go wrong and need fixing.",
    ),
    FieldSpec(
        key="rework_time_per_error_minutes", label="Time to fix one error",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=25, tier=Tier.TIER_2, ask_range=True,
        probe="When something goes wrong, roughly how much extra work does fixing it take?",
        extraction_hint="Minutes of additional handling per error.",
    ),
    FieldSpec(
        key="annual_other_direct_cost", label="Other direct operating costs",
        value_type=ValueType.NUMBER, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=22, tier=Tier.TIER_2, analysis_relevant=False, ask_range=True,
        probe="Any other direct costs tied to running this process?",
        extraction_hint=(
            "Annual direct operating cost specific to this process. Do NOT include "
            "general corporate overhead."),
    ),

    # ---- Tier 2: current-process quality (spec 8.6 / E6). Asked as the metric
    # the sector actually tracks, never as 'what is your accuracy'. ----
    FieldSpec(
        key="current_quality_metric", label="Current quality metric",
        value_type=ValueType.QUALITY_METRIC, sectors=_both(),
        feeds=[Score.FEASIBILITY], priority=33, tier=Tier.TIER_2,
        probe="Do you track how often this process gets it right first time?",
        extraction_hint=(
            "Which quality metric the user quoted. For customer_support choose one "
            "of: first_contact_resolution, escalation_rate, rework_rate. For "
            "document_processing choose one of: exception_rate, first_pass_yield, "
            "straight_through_rate. Return the metric NAME only; the number goes in "
            "current_quality_value. NEVER convert one metric into another — an "
            "exception rate is not an accuracy rate."),
    ),
    FieldSpec(
        key="current_quality_value", label="Current quality value",
        value_type=ValueType.NUMBER, sectors=_both(),
        feeds=[Score.FEASIBILITY], priority=32, tier=Tier.TIER_2, ask_range=True,
        analysis_relevant=False,
        probe="Roughly what does that run at today?",
        extraction_hint=(
            "The value for current_quality_metric as a fraction 0-1 (or percent). "
            "Only fill this when current_quality_metric is also known."),
    ),

    # ---- Risk ----
    FieldSpec(
        key="risk.failure_impact", tier=Tier.TIER_3,
        label="Impact if the AI output is wrong (narrative)",
        value_type=ValueType.STRING, sectors=_both(), feeds=[Score.RISK],
        priority=35, required_for_completion=False,
        # Fix spec 18: no downstream module reads this; only
        # failure_impact_severity is consumed. Kept as report context, never
        # spent as a required question.
        probe="If the AI gets an answer wrong, how severe is the consequence? (financial, customer harm, operational)",
        extraction_hint="Description of consequence severity.",
    ),
    FieldSpec(
        key="risk.failure_impact_severity", tier=Tier.TIER_2, label="Failure impact severity (category)",
        value_type=ValueType.SEVERITY, sectors=_both(), feeds=[Score.RISK],
        priority=34, required_for_completion=True,
        probe="How severe would that consequence be — negligible, minor, moderate, major, or severe?",
        extraction_hint=(
            "Classify the consequence of a wrong AI output into exactly one of: "
            "negligible, minor, moderate, major, severe. Infer from ANY description of "
            "impact the user gives — it usually does not need its own question."),
    ),
    FieldSpec(
        key="risk.compliance_exposure", tier=Tier.TIER_2,
        label="Compliance constraints (canonical)",
        value_type=ValueType.COMPLIANCE, sectors=_both(), feeds=[Score.RISK],
        priority=30,
        probe="Are there any compliance, regulatory, or privacy constraints on this process?",
        extraction_hint=(
            "Normalise each constraint the user names to EXACTLY one of these "
            "canonical keys: hipaa, gdpr, soc 1, soc 2, soc 3, iso 27001, "
            "iso 27017, iso 27018, iso 27701, iso 42001, iso 9001, pci dss, "
            "data_residency, no_training_on_input_data. "
            "'We need to comply with GDPR' -> gdpr. "
            "If the user is vague ('some healthcare privacy requirements'), do NOT "
            "guess hipaa — mark the answer ambiguous so a follow-up can ask. "
            "You are normalising vocabulary ONLY: never state or imply whether a "
            "requirement is satisfied.")
    ),
    # ---- Staged implementation (economic, buy-vs-build) ----
    FieldSpec(
        key="process_stages", tier=Tier.TIER_3,
        label="Implementation stages (buy vs build)",
        value_type=ValueType.STRING_LIST, sectors=_both(), feeds=[Score.ECONOMIC],
        priority=20, analysis_relevant=False,
        probe="Would you tend to buy off-the-shelf tooling or build custom for this?",
        extraction_hint=(
            "Buy-vs-build intent for implementation stages. Emit a list of "
            "objects {\"stage\": <one of data_preparation, model_selection, "
            "integration, testing_qa, deployment, monitoring_setup, "
            "ongoing_maintenance>, \"required\": true, \"buy_or_build\": "
            "\"buy\"|\"build\"}. If the user expresses only a GENERAL "
            "preference ('we'd rather buy'), emit one entry per stage with that "
            "preference. Only fill this when the user actually expresses an "
            "intent — an unstated stage defaults to what the selected platform "
            "supplies, which is more accurate than guessing."),
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


def tier_fields(sector: Sector, tier: Tier) -> list[FieldSpec]:
    return [f for f in fields_for_sector(sector) if f.tier == tier]


def score_map() -> dict[Score, list[str]]:
    """Score -> list of field keys that feed it."""
    m: dict[Score, list[str]] = {}
    for f in FIELDS:
        for s in f.feeds:
            m.setdefault(s, []).append(f.key)
    return m
