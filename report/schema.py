"""Report data contract — spec 13.  [PRESENTATION LAYER]

The typed model the report is assembled into, rendered from, and validated
against. Nothing here calculates anything: every value arrives already computed
by a frozen layer and is carried, with its provenance, to the renderer.

Three invariants are enforced STRUCTURALLY here rather than left to the
renderer, because a renderer that forgets one produces exactly the failure the
whole system exists to prevent:

  * an ABSENT or NOT_COMPUTABLE figure cannot carry a value. It is not zero,
    not blank, not "none" — it is a reason for not having a number.
  * a KNOWN figure cannot exist without provenance and a derivation. If
    provenance genuinely cannot be established, that must be DECLARED
    (flag `provenance_unknown`), never inferred and never omitted.
  * a money figure cannot exist without a currency, unless the unresolved
    currency is DECLARED (flag `currency_unresolved`).

Provenance vocabulary is the canonical five (schemas/assessment_state.py).
There is no sixth value. `verification` is a SEPARATE axis and never becomes a
provenance tag.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.assessment_state import Provenance, RangeEstimate, Sector

# The verification tiers of spec 4.3. A registry that does not record a tier
# yields None — never a guessed one.
Verification = Literal["primary_document", "search_snippet", "unverified"]

# Flags that DECLARE a known absence of metadata. They are the only way to
# build a figure that lacks provenance or currency, so the omission is always
# deliberate and always visible.
FLAG_PROVENANCE_UNKNOWN = "provenance_unknown"
FLAG_CURRENCY_UNRESOLVED = "currency_unresolved"


class FigureStatus(str, Enum):
    """Spec 13.4. A zero and an unknown mean opposite things to a reader."""
    KNOWN = "known"
    ABSENT = "absent"                     # never collected; excluded from totals
    NOT_COMPUTABLE = "not_computable"     # inputs missing; the inputs are named


class RangeSemantics(str, Enum):
    """What a pair of bounds actually means.

    ENVELOPE is the default for anything that came through the economic
    engine's interval arithmetic: it is the widest defensible span, NOT a
    confidence interval, because the arithmetic assumes the inputs move
    together (calc/models.py). Rendering it without that label invites every
    business reader to misread it.
    """
    ENVELOPE = "envelope"
    SCENARIO = "scenario"                 # discrete scenario bounds
    POINT = "point"                       # min == max; a point, not a spread
    CATEGORY = "category"                 # a category, not a number


class Unit(str, Enum):
    """The unit family. MONEY is singled out because it drives the currency
    invariant; `unit_detail` carries anything more specific."""
    MONEY = "money"
    PERCENT = "percent"
    RATIO = "ratio"
    MONTHS = "months"
    HOURS = "hours"
    MINUTES = "minutes"
    COUNT = "count"
    SCORE = "score"
    CATEGORY = "category"
    TEXT = "text"


class EvidenceRegistry(str, Enum):
    """Which registry an evidence id resolves in."""
    BENCHMARK = "benchmark"
    PRICING = "pricing"
    LABOR_RATE = "labor_rate"
    COMPLIANCE = "compliance"
    CALIBRATION = "calibration"


class Citation(BaseModel):
    """A resolved evidence record. Built only from a registry — never authored.

    `verification` stays separate from `provenance` (spec 4.3): a figure can be
    `sourced` and still only `search_snippet`-verified, and the report has to be
    able to say so. A registry that records no tier yields None rather than a
    flattering default.
    """
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source: str
    source_url: Optional[str] = None
    as_of: str = ""
    geography: str = ""
    verification: Optional[Verification] = None
    provenance: Optional[Provenance] = None
    registry: EvidenceRegistry
    # Why provenance/verification are what they are, when the registry needed
    # interpreting. Never used to infer a value that was not recorded.
    note: str = ""

    @property
    def below_primary(self) -> bool:
        """Spec 13.4: figures verified below `primary_document` are disclosed.

        An unrecorded tier counts as below primary — the report may not treat
        "we do not know how firmly this was checked" as "firmly checked".
        """
        return self.verification != "primary_document"


class Figure(BaseModel):
    """One presented quantity, with everything needed to defend it.

    Build these through the classmethods rather than the constructor: they are
    what make the status/value invariants impossible to get wrong by accident.
    """
    model_config = ConfigDict(validate_assignment=True)

    key: str
    label: str
    status: FigureStatus

    value_min: Optional[float] = None
    value_max: Optional[float] = None
    # Categorical figures ("data readiness: good") carry text, not numbers.
    value_text: Optional[str] = None

    unit: Unit = Unit.TEXT
    unit_detail: str = ""
    currency: Optional[str] = None

    provenance: Optional[Provenance] = None
    # For a DERIVED figure: the provenance kinds that actually fed it. `derived`
    # alone hides whether a number came from measured facts or from assumptions.
    provenance_mix: list[Provenance] = Field(default_factory=list)
    confidence: Optional[str] = None
    range_semantics: RangeSemantics = RangeSemantics.POINT

    # Leaf evidence ids that contributed. A derived figure cites its INPUT SET;
    # it does not pretend to have one source.
    source_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    # Evidence ids that matched no registry. Surfaced, never dropped, never
    # turned into a Citation.
    unresolved_source_ids: list[str] = Field(default_factory=list)

    # The upstream `source` string, verbatim — how this number was combined.
    derivation: str = ""
    # Required whenever the figure is not KNOWN.
    absence_reason: str = ""
    flags: list[str] = Field(default_factory=list)
    origin_module: str = ""

    # ---- invariants -----------------------------------------------------

    @model_validator(mode="after")
    def _enforce(self) -> "Figure":
        self._dedupe()
        if self.status is FigureStatus.KNOWN:
            self._check_known()
        else:
            self._check_missing()
        return self

    def _dedupe(self) -> None:
        seen: set[str] = set()
        ordered: list[str] = []
        for sid in self.source_ids:
            if sid and sid not in seen:
                seen.add(sid)
                ordered.append(sid)
        object.__setattr__(self, "source_ids", ordered)

        cseen: set[str] = set()
        cites: list[Citation] = []
        for c in self.citations:
            if c.evidence_id not in cseen:
                cseen.add(c.evidence_id)
                cites.append(c)
        object.__setattr__(self, "citations", cites)

        useen: set[str] = set()
        unres: list[str] = []
        for sid in self.unresolved_source_ids:
            if sid and sid not in useen:
                useen.add(sid)
                unres.append(sid)
        object.__setattr__(self, "unresolved_source_ids", unres)

        mix: list[Provenance] = []
        for p in self.provenance_mix:
            if p not in mix:
                mix.append(p)
        object.__setattr__(self, "provenance_mix", mix)

    def _check_missing(self) -> None:
        if self.value_min is not None or self.value_max is not None \
                or self.value_text is not None:
            raise ValueError(
                f"{self.key}: a {self.status.value} figure cannot carry a value. "
                f"Absence is not zero, blank or 'none' — it is a reason.")
        if not self.absence_reason.strip():
            raise ValueError(
                f"{self.key}: a {self.status.value} figure requires an "
                f"absence_reason naming what is missing and why.")

    def _check_known(self) -> None:
        if self.provenance is None and FLAG_PROVENANCE_UNKNOWN not in self.flags:
            raise ValueError(
                f"{self.key}: a known figure requires a provenance tag. If it "
                f"genuinely cannot be established, declare it with the "
                f"{FLAG_PROVENANCE_UNKNOWN!r} flag — never infer one.")
        if not self.derivation.strip():
            raise ValueError(
                f"{self.key}: a known figure requires a derivation describing "
                f"where the number came from.")
        if self.absence_reason.strip():
            raise ValueError(f"{self.key}: a known figure cannot carry an "
                             f"absence_reason.")

        if self.range_semantics is RangeSemantics.CATEGORY:
            if self.value_text is None:
                raise ValueError(f"{self.key}: a categorical figure requires "
                                 f"value_text.")
            if self.value_min is not None or self.value_max is not None:
                raise ValueError(f"{self.key}: a categorical figure cannot carry "
                                 f"numeric bounds.")
        else:
            if self.value_min is None or self.value_max is None:
                raise ValueError(
                    f"{self.key}: a known numeric figure requires both bounds. A "
                    f"point value is min == max, never a half-open range.")
            if self.value_min > self.value_max:
                raise ValueError(f"{self.key}: value_min {self.value_min} > "
                                 f"value_max {self.value_max}")
            if self.range_semantics is RangeSemantics.POINT \
                    and self.value_min != self.value_max:
                raise ValueError(
                    f"{self.key}: POINT semantics but bounds differ "
                    f"({self.value_min} != {self.value_max}). Use ENVELOPE or "
                    f"SCENARIO — a range is never silently collapsed.")

        if self.unit is Unit.MONEY and not self.currency \
                and FLAG_CURRENCY_UNRESOLVED not in self.flags:
            raise ValueError(
                f"{self.key}: a money figure requires a currency. If it is "
                f"unresolved, declare it with the {FLAG_CURRENCY_UNRESOLVED!r} "
                f"flag — the report never invents a symbol or a convention.")

    # ---- properties -----------------------------------------------------

    @property
    def is_point(self) -> bool:
        return (self.value_min is not None and self.value_min == self.value_max)

    @property
    def below_primary_citations(self) -> list[Citation]:
        return [c for c in self.citations if c.below_primary]

    # ---- constructors ---------------------------------------------------

    @classmethod
    def known(
        cls, key: str, label: str, *, value_min: float, value_max: float,
        unit: Unit, derivation: str, provenance: Optional[Provenance] = None,
        range_semantics: Optional[RangeSemantics] = None, **kw: Any,
    ) -> "Figure":
        if range_semantics is None:
            range_semantics = (RangeSemantics.POINT if value_min == value_max
                               else RangeSemantics.ENVELOPE)
        return cls(key=key, label=label, status=FigureStatus.KNOWN,
                   value_min=value_min, value_max=value_max, unit=unit,
                   derivation=derivation, provenance=provenance,
                   range_semantics=range_semantics, **kw)

    @classmethod
    def category(cls, key: str, label: str, *, value_text: str, derivation: str,
                 provenance: Optional[Provenance] = None, **kw: Any) -> "Figure":
        return cls(key=key, label=label, status=FigureStatus.KNOWN,
                   value_text=value_text, unit=Unit.CATEGORY,
                   range_semantics=RangeSemantics.CATEGORY, derivation=derivation,
                   provenance=provenance, **kw)

    @classmethod
    def absent(cls, key: str, label: str, reason: str, **kw: Any) -> "Figure":
        """Never collected. Excluded from totals, and said so."""
        return cls(key=key, label=label, status=FigureStatus.ABSENT,
                   absence_reason=reason, **kw)

    @classmethod
    def not_computable(cls, key: str, label: str, missing: list[str],
                       **kw: Any) -> "Figure":
        """Inputs missing. Reported as unknown, with the inputs named."""
        return cls(key=key, label=label, status=FigureStatus.NOT_COMPUTABLE,
                   absence_reason=("cannot be computed — missing: "
                                   + ", ".join(missing)), **kw)

    @classmethod
    def from_range(
        cls, key: str, label: str, r: RangeEstimate, *, unit: Unit,
        origin_module: str, range_semantics: RangeSemantics = RangeSemantics.ENVELOPE,
        currency: Optional[str] = None, **kw: Any,
    ) -> "Figure":
        """Carry a calculated RangeEstimate across, inferring NOTHING.

        Provenance, confidence, derivation and the leaf evidence id all come
        from the value itself. A point range stays a point.
        """
        semantics = (RangeSemantics.POINT if r.min == r.max else range_semantics)
        flags = list(kw.pop("flags", []))
        if unit is Unit.MONEY and not currency \
                and FLAG_CURRENCY_UNRESOLVED not in flags:
            flags.append(FLAG_CURRENCY_UNRESOLVED)
        return cls(
            key=key, label=label, status=FigureStatus.KNOWN,
            value_min=r.min, value_max=r.max, unit=unit, currency=currency,
            provenance=r.provenance, confidence=r.confidence,
            range_semantics=semantics, derivation=r.source or "(no derivation "
            "recorded upstream)",
            source_ids=([r.source_id] if r.source_id else []),
            origin_module=origin_module, flags=flags, **kw)


class StatementOrigin(str, Enum):
    CODE = "code"
    LLM = "llm"


class Statement(BaseModel):
    """One sentence of prose.

    An LLM statement must carry the code-authored text it rephrases, so the
    deterministic fallback is always available and a guard rejection never
    leaves a hole. This is why the report stays complete with no LLM at all.
    """
    model_config = ConfigDict(validate_assignment=True)

    text: str
    origin: StatementOrigin = StatementOrigin.CODE
    source_statement: Optional[str] = None
    guard_notes: list[str] = Field(default_factory=list)
    # Set when the text is carried VERBATIM from an upstream field, naming that
    # field. Assembly-authored prose leaves this None and must therefore contain
    # no figures of its own — every number the report authors becomes a Figure.
    # An upstream statement (a payback statement, a driver statement) is exempt
    # precisely because rewriting it is what the spec forbids.
    verbatim_from: Optional[str] = None

    @model_validator(mode="after")
    def _enforce(self) -> "Statement":
        if self.origin is StatementOrigin.LLM and not (self.source_statement or "").strip():
            raise ValueError(
                "an LLM statement must carry the code-authored source_statement it "
                "rephrases, so a rejected fragment can always fall back rather "
                "than leaving the report incomplete.")
        return self

    @classmethod
    def code(cls, text: str) -> "Statement":
        """Prose authored by the assembler. Must carry no figures of its own."""
        return cls(text=text, origin=StatementOrigin.CODE)

    @classmethod
    def verbatim(cls, text: str, source: str) -> "Statement":
        """An upstream statement, carried across unchanged.

        Used for the engine's payback and realization statements, driver
        statements and consistency verdicts: the spec requires these to be
        reproduced rather than rephrased, so they keep whatever figures their
        author put in them.
        """
        return cls(text=text, origin=StatementOrigin.CODE, verbatim_from=source)

    @property
    def is_authored(self) -> bool:
        return self.origin is StatementOrigin.CODE and self.verbatim_from is None


class GapKind(str, Enum):
    """What the analysis could not do (spec 13.4).

    Every kind maps to something a frozen layer actually reports; none is a
    judgement about the business.
    """
    ABSENT_COST = "absent_cost"
    UNEVALUATED_CONDITION = "unevaluated_condition"
    NOT_COMPUTABLE_SCORE = "not_computable_score"
    BELOW_PRIMARY_VERIFICATION = "below_primary_verification"
    UNRESOLVED_FIELD = "unresolved_field"
    REGISTRY_GAP = "registry_gap"
    EXCLUDED_COMPONENT = "excluded_component"
    CURRENCY_UNRESOLVED = "currency_unresolved"
    PROVENANCE_UNKNOWN = "provenance_unknown"
    UNRESOLVED_EVIDENCE_ID = "unresolved_evidence_id"
    UNRESOLVED_POLICY = "unresolved_policy"


class Gap(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: GapKind
    label: str
    detail: str = ""
    # What this does to the numbers, in words. A gap with no stated consequence
    # reads as a footnote; the point of the section is that these are findings.
    consequence: str = ""


class DriverClass(str, Enum):
    """The presentation partition for Decision Drivers (P0 finding F1).

    Drivers are ranked upstream against the UNBOUNDED ECONOMIC quantities, so a
    real business fact that feeds only the feasibility score legitimately scores
    0.0 impact and sorts below the economic drivers. Presenting such an item
    under "what matters most" would misread the module's own output.

    This is a PARTITION, never an ordering. The report preserves
    DecisionDrivers' order exactly and re-ranks nothing.
    """
    ECONOMICALLY_ACTIVE = "economically_active"
    FACTUAL_INPUT = "factual_input"
    DATA_COVERAGE = "data_coverage"

    @classmethod
    def for_driver(cls, driver_type: str, impact: float) -> "DriverClass":
        """Classify one upstream driver. Reads its type and its own impact;
        computes nothing and compares drivers against nothing."""
        if driver_type == "data_coverage":
            return cls.DATA_COVERAGE
        return cls.ECONOMICALLY_ACTIVE if impact > 0.0 else cls.FACTUAL_INPUT


class DriverEntry(BaseModel):
    """One Decision Driver, carried into the report unchanged.

    `rank` is the upstream position, not a report-computed one.
    """
    model_config = ConfigDict(validate_assignment=True)

    key: str
    label: str
    statement: Statement
    driver_type: str                       # calc.driver_ranking.DriverType value
    presentation_class: DriverClass
    rank: int
    impact: float = 0.0
    dominant_quantity: str = ""
    confidence: str = "medium"
    provenance: Optional[Provenance] = None
    uncertainty_type: str = "none"
    relative_width: Optional[float] = None
    uncertainty_index: Optional[float] = None
    # NOTE: upstream `DriverImpact.evidence_ids` carries rendered CITATION
    # STRINGS, not evidence ids (they come from TaskAutomationEstimate.
    # benchmark_anchor). They are kept verbatim as prose and are never resolved
    # against a registry. See report/evidence.py.
    evidence_notes: list[str] = Field(default_factory=list)


class ValidationItem(BaseModel):
    """One entry in "What to Validate Next" (spec 13.9).

    Every item is triggered by a specific upstream finding. `impact` is
    INHERITED from the Decision Driver that would be resolved by collecting
    this, where such a driver exists — it is never computed here, and an item
    with no corresponding driver carries None rather than a manufactured
    number.
    """
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    what_is_missing: str
    why_it_matters: str
    what_to_collect: str
    gap_kind: GapKind
    priority_basis: str = ""
    impact: Optional[float] = None


class Cell(BaseModel):
    """A table cell: either literal text or a reference to a Figure by key.

    A figure is never stringified into a table at assembly time — the renderer
    resolves the reference, so the figure's status and provenance survive.
    """
    model_config = ConfigDict(frozen=True)

    text: Optional[str] = None
    figure_key: Optional[str] = None

    @model_validator(mode="after")
    def _one_of(self) -> "Cell":
        if (self.text is None) == (self.figure_key is None):
            raise ValueError("a cell carries either text or a figure_key, not "
                             "both and not neither")
        return self


class ReportTable(BaseModel):
    key: str
    label: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Cell]] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def _rectangular(self) -> "ReportTable":
        for i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(f"{self.key}: row {i} has {len(row)} cells but "
                                 f"there are {len(self.columns)} columns")
        return self


ReportLayer = Literal[1, 2, 3]


class ReportSection(BaseModel):
    """One numbered section of spec 13's fourteen, in its presentation layer."""
    model_config = ConfigDict(validate_assignment=True)

    key: str
    number: int
    title: str
    layer: ReportLayer
    statements: list[Statement] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    tables: list[ReportTable] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Content that belongs in the audit view rather than the business-facing
    # one, for a section whose layer is otherwise business-facing (rejected
    # alternative candidates, for instance).
    audit_notes: list[str] = Field(default_factory=list)
    # Drivers are structured, not prose, so the partition survives to the
    # renderer. Only the drivers section populates this.
    drivers: list[DriverEntry] = Field(default_factory=list)
    validation_items: list[ValidationItem] = Field(default_factory=list)


class ReportMode(str, Enum):
    """Spec 13.6. The pipeline has terminal states; the report has a shape for
    each and fabricates neither a solution nor an economics it does not have."""
    FULL = "full"
    PARTIAL = "partial"
    REFUSED = "refused"


class LaborRealizationSource(str, Enum):
    USER = "user"
    UNSET = "unset"


class ReportManifest(BaseModel):
    """Everything needed to reproduce and audit one report."""
    model_config = ConfigDict(validate_assignment=True)

    generated_at: str
    sector: Sector
    pack_version: str = ""
    pack_health: dict[str, int] = Field(default_factory=dict)

    economic_calibration_version: Optional[int] = None
    scoring_calibration_version: Optional[int] = None
    solution_calibration_version: Optional[int] = None

    registry_pattern_id: str = ""
    registry_implementation_id: str = ""
    registry_last_reviewed: str = ""

    labor_realization: Optional[str] = None
    labor_realization_source: LaborRealizationSource = LaborRealizationSource.UNSET

    currency: Optional[str] = None
    currency_basis: str = ""

    llm_model: Optional[str] = None
    llm_used_for: list[str] = Field(default_factory=list)
    guard_actions: list[str] = Field(default_factory=list)

    # Every figure the report rendered, for audit.
    figure_ledger: list[Figure] = Field(default_factory=list)
    # Evidence ids encountered that resolved in no registry.
    unresolved_evidence_ids: list[str] = Field(default_factory=list)

    @property
    def llm_used(self) -> bool:
        return bool(self.llm_used_for)


class Report(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    mode: ReportMode
    refusal_reason: str = ""
    sections: list[ReportSection] = Field(default_factory=list)
    manifest: ReportManifest

    @model_validator(mode="after")
    def _enforce(self) -> "Report":
        if self.mode is ReportMode.REFUSED and not self.refusal_reason.strip():
            raise ValueError("a refused report must state why it was refused")
        keys = [s.key for s in self.sections]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate section keys: {sorted(dupes)}")
        return self

    def section(self, key: str) -> Optional[ReportSection]:
        return next((s for s in self.sections if s.key == key), None)

    def layer(self, n: int) -> list[ReportSection]:
        return [s for s in self.sections if s.layer == n]


# ---------------------------------------------------------------------------
# ReportInput — the frozen bundle
#
# The report consumes ONE bundle, produced once by the orchestration layer. It
# never calls an engine itself: `calc.engine.run`, `driver_ranking.rank_drivers`,
# `sensitivity.sweep` and `solution.estimator.estimate` are all off-limits to
# this package, which is what stops the report becoming a second analytical
# layer that can disagree with the first.
# ---------------------------------------------------------------------------

from calc.ai_state import LaborRealization                      # noqa: E402
from calc.assessment_confidence import AssessmentConfidence     # noqa: E402
from calc.driver_ranking import DecisionDrivers, ScoreBundle    # noqa: E402
from calc.engine import EconomicResult                          # noqa: E402
from calc.sensitivity import SensitivityReport                  # noqa: E402
from schemas.assessment_state import AssessmentState            # noqa: E402
from solution.schema import AlternativesResult, SolutionEstimate  # noqa: E402


class ReportInput(BaseModel):
    """Immutable snapshot of everything the pipeline produced, in one place.

    `economics` and `scores` are the SAME objects the drivers were ranked
    against — enforced below, not merely intended. Two copies that drifted
    apart would let the report print an economics section that no score or
    driver in the same document was computed from.
    """
    model_config = ConfigDict(frozen=True)

    state: AssessmentState
    solution: SolutionEstimate
    # All three are absent together when the economic engine could not run
    # (calc.engine.EconomicInputError). That is a legitimate terminal state, not
    # an error to paper over: the report renders in `partial` mode and the
    # dependent sections stay explicitly empty.
    economics: Optional[EconomicResult] = None
    scores: Optional[ScoreBundle] = None
    drivers: Optional[DecisionDrivers] = None
    alternatives: AlternativesResult
    sensitivity: Optional[SensitivityReport] = None
    confidence: Optional[AssessmentConfidence] = None
    # EconomicInputError.reasons, carried verbatim from the engine.
    economic_error: list[str] = Field(default_factory=list)

    labor_realization: Optional[LaborRealization] = None
    labor_realization_source: LaborRealizationSource = LaborRealizationSource.UNSET

    @model_validator(mode="after")
    def _one_canonical_result(self) -> "ReportInput":
        if self.drivers is None:
            if self.economics is not None or self.scores is not None:
                raise ValueError(
                    "economics and scores exist only alongside the drivers they "
                    "were computed with; a bundle cannot carry half a pipeline.")
            if not self.economic_error:
                raise ValueError(
                    "a bundle with no economics must carry the engine's own "
                    "reasons (EconomicInputError.reasons), so the report can say "
                    "why rather than simply omitting the section.")
            return self
        if self.scores != self.drivers.scores:
            raise ValueError(
                "scores must be the SAME bundle the drivers were ranked against. "
                "Two independently computed bundles can disagree, and the report "
                "has no way to tell which one its drivers came from.")
        if self.economics != self.drivers.scores.result:
            raise ValueError(
                "economics must be the canonical EconomicResult the scores and "
                "drivers were computed from (drivers.scores.result).")
        if self.labor_realization is None \
                and self.labor_realization_source is LaborRealizationSource.USER:
            raise ValueError("labor_realization_source=user requires a policy")
        return self

    @classmethod
    def from_pipeline(
        cls, *, state: AssessmentState, solution: SolutionEstimate,
        drivers: Optional[DecisionDrivers], alternatives: AlternativesResult,
        sensitivity: Optional[SensitivityReport] = None,
        confidence: Optional[AssessmentConfidence] = None,
        labor_realization: Optional[LaborRealization] = None,
        labor_realization_source: LaborRealizationSource = LaborRealizationSource.UNSET,
        economic_error: Optional[list[str]] = None,
    ) -> "ReportInput":
        """Wire the bundle from the pipeline's own objects.

        Takes `economics` and `scores` from inside `drivers` rather than as
        separate arguments, so the canonical-result rule cannot be violated by
        a caller passing a second, separately-computed run.
        """
        return cls(
            state=state, solution=solution,
            economics=(drivers.scores.result if drivers else None),
            scores=(drivers.scores if drivers else None), drivers=drivers,
            alternatives=alternatives, sensitivity=sensitivity,
            confidence=confidence, labor_realization=labor_realization,
            labor_realization_source=labor_realization_source,
            economic_error=list(economic_error or []))
