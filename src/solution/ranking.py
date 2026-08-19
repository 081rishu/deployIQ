"""Deterministic filter + rank of candidate solution patterns.

Every term below is computed from explicit metadata — Compatibility on the
registry entries, and the curated ReferenceSolution for the sector. No term is
a bare "this is the sector's pattern" bonus: sector influence enters only
through the reference solution, whose conditions_for_deviation can release the
anchor when the assessment genuinely warrants departing from the baseline.

Scoring (each term normalised to 0-1, then weighted; max = 10.0):

    reference_alignment  x 4.0   does this follow the curated baseline, and if
                                 not, is the deviation one the reference itself
                                 sanctions?
    scale_fit            x 2.0   does the chosen stack handle the volume?
    compliance_fit       x 2.0   does it cover the stated constraints?
    complexity_pref      x 2.0   prefer the cheapest build that still fits

Reference alignment dominates deliberately: the registry constrains what is
possible, the reference encodes what is *usually right* for the sector, and
the remaining terms adjust for this specific assessment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from typing import Optional

from schemas.assessment_state import point, AssessmentState, EffortBand
from lib.compliance import ClaimStatus, ComplianceVerdict, evaluate_implementation
from solution.constants import scale_band
from solution.reference_solutions import reference_for
from solution.schema import (
    Capability,
    ComplianceClaim,
    DeviationTrigger,
    ImplementationOption,
    ReferenceSolution,
    SolutionPattern,
)

# Term weights. Kept here, named, and summing to a known maximum so a score can
# always be explained as a breakdown rather than an opaque number.
W_REFERENCE = 4.0
W_SCALE = 2.0
W_COMPLIANCE = 2.0
W_COMPLEXITY = 2.0
MAX_SCORE = W_REFERENCE + W_SCALE + W_COMPLIANCE + W_COMPLEXITY

# Reference-alignment levels (0-1), highest to lowest.
#
# The ordering matters: a deviation condition sanctions changing the
# IMPLEMENTATION KIND (e.g. outgrowing a low-code build), not abandoning the
# architecture. So the baseline pattern that adapts its implementation to the
# fired condition must outrank a foreign pattern that merely happens to use a
# sanctioned kind — otherwise "volume is high" silently becomes grounds for
# switching architecture entirely.
ALIGN_MATCHES_REFERENCE = 1.0      # is the baseline, no deviation in play
ALIGN_REFERENCE_ADAPTED = 0.9      # is the baseline, adapted to the fired condition
ALIGN_SANCTIONED_DEVIATION = 0.7   # not the baseline, but the deviation is sanctioned
ALIGN_REFERENCE_UNADAPTED = 0.6    # is the baseline but ignores the fired condition
ALIGN_UNSANCTIONED = 0.2           # departs from the baseline with no cause

_COMPLEXITY_PREFERENCE = {
    EffortBand.SMALL: 1.0,
    EffortBand.MEDIUM: 0.5,
    EffortBand.LARGE: 0.0,
}

# N5: scale thresholds are defined once in solution/constants.py and imported
# by both this module and the scope model, so they cannot drift apart.


@dataclass
class RankedCandidate:
    pattern: SolutionPattern
    chosen_implementation: str
    score: float
    reasons: list[str]
    risks: list[str]
    breakdown: dict[str, float] = field(default_factory=dict)
    reference_alignment: float = 0.0
    active_deviations: list[str] = field(default_factory=list)
    unevaluated_conditions: list[str] = field(default_factory=list)


def _effective_scale(state: AssessmentState) -> str:
    return scale_band(point(state.monthly_volume))


def _needs_compliance(state: AssessmentState) -> list[str]:
    return list(state.risk.compliance_exposure or [])


def compliance_verdicts(
    implementation_id: str, required: list[str],
) -> dict[str, ComplianceVerdict]:
    """Evidence-registry verdicts for one implementation and each requirement.

    The evidence registry (data/compliance_attestations.json) is the ONLY
    source consulted. Inline registry claims are not used for hard filtering:
    a claim without an attestation behind it must never qualify an
    architecture.
    """
    return {r: evaluate_implementation(implementation_id, r) for r in required}


def covers_compliance_by_evidence(implementation_id: str, required: list[str]) -> bool:
    """SUPPORTED on every requirement, from implementation-specific evidence.

    UNKNOWN never satisfies a hard requirement, and one component's evidence
    never fills another component's gap.
    """
    if not required:
        return True
    return all(v.satisfies
               for v in compliance_verdicts(implementation_id, required).values())


# Retained name for existing internal callers.
_covers_compliance_by_evidence = covers_compliance_by_evidence


def _scale_ok(scale_value: str, needed: str) -> bool:
    """A stack rated for a given volume band handles that band or anything
    smaller; 'any' handles everything."""
    if scale_value == "any":
        return True
    order = {"small": 0, "medium": 1, "large": 2}
    return order.get(scale_value, 0) >= order.get(needed, 0)


scale_ok = _scale_ok


def active_deviations(
    state: AssessmentState, reference: ReferenceSolution
) -> tuple[list, list]:
    """Evaluate the reference's deviation conditions against the assessment.

    Returns (fired, unevaluated). MANUAL conditions are never guessed at — they
    are returned separately so they surface as human judgement calls instead of
    silently failing to fire.
    """
    fired, unevaluated = [], []
    for cond in reference.conditions_for_deviation:
        if cond.trigger == DeviationTrigger.MANUAL:
            unevaluated.append(cond)
        elif cond.trigger == DeviationTrigger.MONTHLY_VOLUME_ABOVE:
            if (point(state.monthly_volume) or 0) > (cond.threshold or 0):
                fired.append(cond)
        elif cond.trigger == DeviationTrigger.COMPLIANCE_PRESENT:
            if _needs_compliance(state):
                fired.append(cond)
        elif cond.trigger == DeviationTrigger.REQUIRED_ACCURACY_ABOVE:
            acc = point(state.required_accuracy)
            if isinstance(acc, (int, float)) and acc > (cond.threshold or 0):
                fired.append(cond)
    return fired, unevaluated


def _reference_alignment(
    pattern: SolutionPattern,
    impl: ImplementationOption,
    reference: Optional[ReferenceSolution],
    fired: list,
) -> tuple[float, str]:
    """Score how well a candidate lines up with the sector's curated baseline."""
    if reference is None:
        return 0.0, "no reference solution registered for this sector"

    is_reference = pattern.id == reference.pattern
    sanctioned_kinds = {k for c in fired for k in c.releases_to_kinds}
    fired_ids = ", ".join(c.id for c in fired)

    if is_reference:
        if not fired:
            return ALIGN_MATCHES_REFERENCE, f"matches reference baseline ({reference.id})"
        if impl.kind in sanctioned_kinds:
            return (ALIGN_REFERENCE_ADAPTED,
                    f"reference baseline ({reference.id}) adapted to {fired_ids}: "
                    f"implementation kind '{impl.kind}'")
        return (ALIGN_REFERENCE_UNADAPTED,
                f"reference baseline ({reference.id}) but implementation kind "
                f"'{impl.kind}' does not answer active condition(s) {fired_ids}")

    if fired and impl.kind in sanctioned_kinds:
        return (ALIGN_SANCTIONED_DEVIATION,
                f"deviates from {reference.id}, sanctioned by {fired_ids} "
                f"(implementation kind '{impl.kind}')")
    return (ALIGN_UNSANCTIONED,
            f"departs from reference baseline ({reference.id}) with no "
            f"sanctioning condition")


def _candidate_provider_impl(
    pattern: SolutionPattern, caps: set[Capability], needed_scale: str,
    compliance: Optional[list[str]] = None,
) -> tuple[str, list[str]]:
    """Pick the implementation for this pattern.

    Preference order, each applied only if it leaves candidates:
      1. covers every required capability  (hard filter)
      2. satisfies the declared compliance constraints
      3. is rated for the assessed volume
      4. least complex

    Compliance and scale come before complexity deliberately. Choosing the
    cheapest build and then reporting that it fails the constraints would be
    scoring a pattern on an implementation nobody would actually pick — the
    pattern may well have a compliant or scale-appropriate option available.
    """
    order = {EffortBand.SMALL: 0, EffortBand.MEDIUM: 1, EffortBand.LARGE: 2}
    covering: list[tuple[ImplementationOption, set[Capability]]] = []
    for impl in pattern.implementations:
        covered: set[Capability] = set(impl.compatibility.supported_capabilities)
        for prov in impl.providers:
            covered |= set(prov.compatibility.supported_capabilities)
        if caps <= covered:
            covering.append((impl, covered))
    if not covering:
        return "", []

    pool = covering
    if compliance:
        compliant = [(i, c) for i, c in pool
                     if _covers_compliance_by_evidence(i.id, compliance)]
        pool = compliant or pool
    scale_fitting = [(i, c) for i, c in pool
                     if _scale_ok(i.compatibility.scale, needed_scale)]
    pool = scale_fitting or pool
    impl, covered = min(pool, key=lambda ic: order[ic[0].compatibility.technical_complexity])
    return impl.id, [c.value for c in covered]


class ExcludedCandidate(BaseModel):
    """A candidate removed by a HARD compliance requirement, with the reason."""
    pattern_id: str
    implementation_id: str
    standard: str
    status: str
    reason: str


class RankingOutcome(BaseModel):
    ranked: list[RankedCandidate] = Field(default_factory=list)
    excluded: list[ExcludedCandidate] = Field(default_factory=list)
    required_standards: list[str] = Field(default_factory=list)
    compliance_gap: bool = False
    compliance_statement: str = ""

    model_config = {"arbitrary_types_allowed": True}


def rank_candidates(
    state: AssessmentState,
    candidates: list[SolutionPattern],
    caps: set[Capability],
) -> RankingOutcome:
    """Rank survivors after applying HARD compliance filtering.

    Section 8: compliance is a constraint, not a ranking preference. If the
    assessment declares a hard requirement, an implementation that cannot
    satisfy it from its own evidence is EXCLUDED, not merely down-weighted.
    If nothing survives, no architecture is forced — the gap is returned with
    every exclusion and its reason.
    """
    required = _needs_compliance(state)
    ranked = filter_and_rank(state, candidates, caps)
    if not required:
        return RankingOutcome(ranked=ranked)

    survivors, excluded = [], []
    for cand in ranked:
        verdicts = compliance_verdicts(cand.chosen_implementation, required)
        failing = {s: v for s, v in verdicts.items() if not v.satisfies}
        if not failing:
            survivors.append(cand)
            continue
        for std, v in failing.items():
            excluded.append(ExcludedCandidate(
                pattern_id=cand.pattern.id,
                implementation_id=cand.chosen_implementation,
                standard=std, status=v.status.value, reason=v.reason))

    if survivors:
        return RankingOutcome(ranked=survivors, excluded=excluded,
                              required_standards=required)
    return RankingOutcome(
        ranked=[], excluded=excluded, required_standards=required,
        compliance_gap=True,
        compliance_statement=(
            f"No candidate architecture can be shown to satisfy {required} from "
            f"implementation-specific evidence. {len(excluded)} candidate/"
            f"requirement combination(s) were excluded. No architecture is "
            f"recommended: lowering the requirement to produce a winner would "
            f"misrepresent the evidence. Either obtain the missing attestations "
            f"or treat the requirement as unmet."))


@dataclass
class RankingContext:
    """Everything the scorer needs that depends on the assessment, not on the
    candidate. Computed once so scoring one candidate is identical whether it
    is reached through the primary ranker or through the alternatives module."""
    needed_scale: str
    compliance: list[str]
    reference: Optional[ReferenceSolution]
    fired: list = field(default_factory=list)
    unevaluated: list = field(default_factory=list)


def ranking_context(state: AssessmentState) -> RankingContext:
    reference = reference_for(state.sector)
    fired, unevaluated = ([], [])
    if reference is not None:
        fired, unevaluated = active_deviations(state, reference)
    return RankingContext(
        needed_scale=_effective_scale(state),
        compliance=_needs_compliance(state),
        reference=reference, fired=fired, unevaluated=unevaluated)


def score_candidate(
    pattern: SolutionPattern,
    impl: ImplementationOption,
    ctx: RankingContext,
) -> RankedCandidate:
    """Score ONE (pattern, implementation) pair.

    Extracted so the alternatives module can evaluate a specific implementation
    with the identical function that selected the primary — an alternative is
    never scored by a parallel rule set, and no second scoring model exists.
    """
    compats = [impl.compatibility] + [p.compatibility for p in impl.providers]
    reasons: list[str] = []
    risks: list[str] = []

    # --- reference alignment ---
    alignment, align_reason = _reference_alignment(pattern, impl, ctx.reference, ctx.fired)
    reasons.append(align_reason)

    # --- scale fit: share of the chosen stack rated for this volume ---
    scale_hits = [c for c in compats if _scale_ok(c.scale, ctx.needed_scale)]
    scale_fit = len(scale_hits) / len(compats)
    if scale_fit < 1.0:
        short = [c.scale for c in compats if not _scale_ok(c.scale, ctx.needed_scale)]
        reasons.append(f"scale: {sorted(set(short))} below required '{ctx.needed_scale}'")
        risks.append(f"part of the stack is not rated for {ctx.needed_scale} volume")

    # --- compliance fit ---
    if ctx.compliance:
        verdicts = compliance_verdicts(impl.id, ctx.compliance)
        satisfied = [s for s, v in verdicts.items() if v.satisfies]
        compliance_fit = len(satisfied) / len(ctx.compliance)
        for std, v in verdicts.items():
            if not v.satisfies:
                reasons.append(f"compliance {std}: {v.status.value} — {v.reason[:150]}")
                risks.append(f"compliance gap: {std} ({v.status.value})")
    else:
        compliance_fit = 1.0

    complexity_pref = _COMPLEXITY_PREFERENCE[impl.compatibility.technical_complexity]

    breakdown = {
        "reference_alignment": round(W_REFERENCE * alignment, 3),
        "scale_fit": round(W_SCALE * scale_fit, 3),
        "compliance_fit": round(W_COMPLIANCE * compliance_fit, 3),
        "complexity_preference": round(W_COMPLEXITY * complexity_pref, 3),
    }
    return RankedCandidate(
        pattern=pattern,
        chosen_implementation=impl.id,
        score=round(sum(breakdown.values()), 3),
        reasons=reasons,
        risks=risks,
        breakdown=breakdown,
        reference_alignment=alignment,
        active_deviations=[c.description for c in ctx.fired],
        unevaluated_conditions=[c.description for c in ctx.unevaluated],
    )


def filter_and_rank(
    state: AssessmentState,
    candidates: list[SolutionPattern],
    caps: set[Capability],
) -> list[RankedCandidate]:
    """Filter out patterns no implementation can satisfy, then score survivors.

    Scoring is done against the ONE implementation actually chosen for the
    pattern (plus its providers), not every implementation the pattern lists —
    otherwise a pattern scores higher simply for offering more options.
    """
    ctx = ranking_context(state)

    ranked: list[RankedCandidate] = []
    for pattern in candidates:
        impl_id, _ = _candidate_provider_impl(pattern, caps, ctx.needed_scale, ctx.compliance)
        if not impl_id:
            continue  # no implementation covers all required capabilities
        impl = next(i for i in pattern.implementations if i.id == impl_id)
        ranked.append(score_candidate(pattern, impl, ctx))

    # Ties break on reference alignment, then on the cheaper build — never on
    # registry declaration order.
    ranked.sort(key=lambda r: (r.score, r.reference_alignment,
                               r.breakdown.get("complexity_preference", 0.0)),
                reverse=True)
    return ranked
