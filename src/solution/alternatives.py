"""Alternatives — spec 11.

What this module answers: *"What else could work, and what would I trade off
by choosing it?"* It is informational. It never touches the primary selection.

Boundaries (spec 11.5), enforced structurally rather than by prompt wording:

  Registry    is the ONLY source of an architecture, provider, capability or
              benchmark. The candidate list is built before the LLM is called
              and the LLM is handed that fixed list; anything it names that is
              not in the list is discarded.
  Code        filters, decides materiality, and derives every band and metric —
              using the SAME functions that produced the primary estimate
              (`ranking.score_candidate`, `scope.effort_scope`,
              `performance.metrics_for`, `risks.controls_for`), so an
              alternative is never evaluated by a parallel rule set.
  LLM         explains and contextualises, in prose that passes `guard()`.

Three things this module deliberately does NOT do:

  * re-rank or modify the primary selection (11.4);
  * calculate a second recommendation score (11.4) — the display order reuses
    the primary ranker's own score, and `is_recommendation` is a constant
    False on the payload;
  * build an economic model per alternative (11.8).

And one thing it refuses to do: pad. If fewer than two credible alternatives
survive, fewer are shown; if none do, the section says so (11.1, 11.7).
"""

from __future__ import annotations

import re
from typing import Optional

from lib import compliance as compliance_registry
from lib.logging_config import get_logger
from schemas.assessment_state import AssessmentState, EffortBand, Provenance
from solution import patterns as patterns_mod
from solution import performance, ranking, risks, scope
from solution.calibration import ALTERNATIVES_CALIBRATION, CALIBRATION
from solution.schema import (
    Alternative,
    AlternativeComparison,
    AlternativesResult,
    AlternativeSource,
    Capability,
    DifferenceKind,
    HumanInvolvement,
    ImplementationKind,
    ImplementationOption,
    PerformanceMetric,
    RejectedAlternative,
    SolutionEstimate,
    SolutionPattern,
)

log = get_logger("solution.alternatives")

# Spec 11 asks for 2-3 credible alternatives. This is a CEILING, never a
# target: nothing is invented to reach it (11.1).
MAX_ALTERNATIVES = 3

# The human capabilities an architecture can declare.
_HUMAN_CAPS = {Capability.HUMAN_REVIEW, Capability.HUMAN_ESCALATE}

# Provider categories that put a MODEL in the loop. An implementation carrying
# none of these is deterministic: same input, same output, no inference cost,
# no model behaviour to evaluate or monitor. Read from the registry's own
# provider categories rather than from an id whitelist, so a new provider is
# classified by what it is declared to be.
_MODEL_PROVIDER_CATEGORIES = {"llm", "voice"}

_COMPLEXITY_ORDER = {EffortBand.SMALL: 0, EffortBand.MEDIUM: 1, EffortBand.LARGE: 2}
_BAND_ORDER = _COMPLEXITY_ORDER

# Spec 11.2 lists categories an alternative *may* fall into. These are the ones
# the curated registry currently has no entry for. Named explicitly so their
# absence reads as a registry gap rather than as a finding that the approach
# does not apply — the honest distinction 11.2's "must not force a category"
# and 11.1's "never fabricate" together require.
CATEGORIES_NOT_IN_REGISTRY = [
    "process redesign / simplification",
    "deterministic rules or workflow automation without an LLM",
    "smaller or specialized ML model",
]

# When the current process is worth showing as a baseline (11.2's "maintaining
# the current process where it is a meaningful baseline"). Held in
# solution/calibration.py with its unit, provenance, rationale and review date
# rather than as a bare literal here, so it is disclosable as the assumption
# it is.
STATUS_QUO_CEILING = ALTERNATIVES_CALIBRATION.status_quo_automation_ceiling


# ---------------------------------------------------------------------------
# LLM guard
# ---------------------------------------------------------------------------

# 11.5: the LLM may not claim benchmark values, costs or effort. Every number
# in this section comes from a registry-backed or code-derived field, so any
# digit in generated prose is by construction not one of them.
_DIGIT = re.compile(r"\d")

# 11.6: surfacing an alternative is a fact, not a nudge. Conditional preference
# ("may be preferable when volumes fall") is explicitly REQUIRED by 11.3, so
# only unconditional/directive phrasing is blocked.
_DIRECTIVE = re.compile(
    r"\b(you should|we recommend|i recommend|we suggest you|we advise|"
    r"is the best|the best choice|the best option|the right choice|"
    r"use this instead|switch to|go with|opt for|must use|"
    r"is recommended|our recommendation|better off)\b",
    re.IGNORECASE,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

MAX_EXPLANATION_CHARS = 700


def guard(prose: str, where: str) -> tuple[str, list[str]]:
    """Strip sentences the LLM was not permitted to produce.

    Sentence-level rather than whole-answer rejection: a single fabricated
    figure should cost the sentence that carries it, not the explanation that
    surrounds it. Every drop is reported, so a scrubbed answer is visible as
    scrubbed instead of silently shortened.
    """
    notes: list[str] = []
    kept: list[str] = []
    for sentence in _SENTENCE.split((prose or "").strip()):
        s = sentence.strip()
        if not s:
            continue
        if _DIGIT.search(s):
            notes.append(f"{where}: dropped a sentence containing a figure the "
                         f"LLM is not permitted to assert ({s[:90]!r})")
            continue
        m = _DIRECTIVE.search(s)
        if m:
            notes.append(f"{where}: dropped recommendation language "
                         f"({m.group(0)!r}) — alternatives are informational")
            continue
        kept.append(s)
    return " ".join(kept)[:MAX_EXPLANATION_CHARS], notes


# ---------------------------------------------------------------------------
# Candidate space
# ---------------------------------------------------------------------------

def _parse_capability(value) -> Optional[Capability]:
    """Strict enum parse, deliberately local.

    solution.capabilities imports the LLM client at module scope; this module
    reaches the LLM only inside `_explain`, and keeping it that way means the
    deterministic half of Alternatives runs with no LLM dependency at all.
    """
    if isinstance(value, Capability):
        return value
    try:
        return Capability(str(value or "").strip().lower())
    except ValueError:
        return None


def _required_capabilities(estimate: SolutionEstimate) -> list[Capability]:
    """The capabilities the primary estimate was built on.

    Taken from the estimate rather than re-decomposed, so alternatives are
    judged against exactly the same requirement the primary satisfied — and so
    no second LLM decomposition can quietly change the requirement.
    """
    raw = (estimate.capability_validation or {}).get("capabilities") or []
    out = []
    for c in raw:
        cap = _parse_capability(c)
        if cap is not None:
            out.append(cap)
    return out


def _covering_implementations(
    caps: set[Capability], compliance: list[str],
) -> tuple[list[tuple[SolutionPattern, ImplementationOption]], list[RejectedAlternative]]:
    """Every (pattern, implementation) that clears the HARD filters.

    Hard filters are exactly the primary path's: full capability coverage
    (patterns.implementation_covers) and evidence-backed compliance
    (ranking.covers_compliance_by_evidence). Scale stays a soft term here, as
    it is in the primary ranker — a stack short of the volume band is surfaced
    as a limitation, not silently deleted.
    """
    pairs, rejected = [], []
    for pattern in patterns_mod.all_patterns():
        for impl in pattern.implementations:
            if not patterns_mod.implementation_covers(impl, caps):
                continue
            if not ranking.covers_compliance_by_evidence(impl.id, compliance):
                rejected.append(RejectedAlternative(
                    pattern_id=pattern.id, implementation_id=impl.id,
                    reason=(f"excluded by the hard compliance filter: {compliance} "
                            f"could not be shown from implementation-specific "
                            f"evidence")))
                continue
            pairs.append((pattern, impl))
    return pairs, rejected


def _has_sufficient_metadata(
    pattern: SolutionPattern, impl: ImplementationOption,
) -> Optional[str]:
    """11.1: enough registry metadata to actually compare. Reason, or None."""
    if not (pattern.architecture or "").strip():
        return "registry has no architecture description for this pattern"
    if not impl.compatibility.strengths:
        return "registry declares no strengths for this implementation"
    if not impl.compatibility.limitations:
        return "registry declares no limitations for this implementation"
    return None


def _materiality_key(pattern_id: str, kind: ImplementationKind) -> tuple[str, str]:
    """What makes two candidates the same offer.

    Pattern plus implementation KIND. A different vendor inside the same
    pattern and the same kind (n8n vs Make) is a procurement choice, not a
    different approach, and presenting it as an alternative would pad the
    section without widening the user's actual options.
    """
    return (pattern_id, kind.value)


# ---------------------------------------------------------------------------
# Comparison construction (11.3) — registry metadata and shared derivations
# ---------------------------------------------------------------------------

def _stack_metadata(impl: ImplementationOption) -> tuple[list[str], list[str]]:
    strengths = list(impl.compatibility.strengths)
    limitations = list(impl.compatibility.limitations)
    for prov in impl.providers:
        strengths += [f"{prov.name}: {s}" for s in prov.compatibility.strengths]
        limitations += [f"{prov.name}: {l}" for l in prov.compatibility.limitations]
    # Preserve order, drop repeats.
    return list(dict.fromkeys(strengths)), list(dict.fromkeys(limitations))


def _human_involvement(
    impl: ImplementationOption, caps: set[Capability],
) -> tuple[HumanInvolvement, list[str]]:
    """Derived from declared capabilities, not asserted in prose.

    Per-task HITL is NOT estimated for alternatives — that would need a second
    LLM decomposition per candidate, which 11.8 puts outside MVP scope. What
    the architecture *declares* is a fact the registry already carries.
    """
    declared = set(impl.compatibility.supported_capabilities)
    for prov in impl.providers:
        declared |= set(prov.compatibility.supported_capabilities)
    human = sorted(c.value for c in (declared & _HUMAN_CAPS & caps))
    basis = [f"the implementation declares '{c}'" for c in human]
    if Capability.HUMAN_REVIEW.value in human:
        return HumanInvolvement.REVIEW_IN_LINE, basis
    if Capability.HUMAN_ESCALATE.value in human:
        return HumanInvolvement.ESCALATION_ONLY, basis
    return HumanInvolvement.NOT_DECLARED, [
        "no human-review or escalation capability is declared for this "
        "implementation among the required capabilities"]


def _when_preferable(
    state: AssessmentState,
    alt_pattern: SolutionPattern, alt_impl: ImplementationOption, alt_band: EffortBand,
    primary_pattern: SolutionPattern, primary_impl: ImplementationOption,
    primary_band: Optional[EffortBand],
    ctx: ranking.RankingContext,
) -> list[str]:
    """Situations in which the alternative may be preferable (11.3).

    Every entry is a difference the registry or a shared derivation can
    demonstrate. An alternative with nothing to say here gets an empty list —
    that is a real answer, and filling it with prose would be exactly the
    invention 11.5 prohibits.
    """
    out: list[str] = []

    # The decisive one where it applies: no model in the loop. This is spec
    # 11.6's own example of a legitimate alternative rationale, and it is a
    # registry fact — the implementation declares no model-bearing provider —
    # not a judgement about whether AI is warranted here.
    if _has_model(primary_impl) and not _has_model(alt_impl):
        out.append("if every decision in the workflow can be expressed as a rule: "
                   "this build puts no model in the loop, so the same input always "
                   "produces the same output, there is no inference cost, and there "
                   "is no model behaviour to evaluate or monitor")

    # Compliance headroom the primary lacks, on standards the user did NOT
    # declare. (Declared standards were a hard filter, so both survivors pass.)
    #
    # Read from the EVIDENCE registry, not from the registry's inline
    # Compatibility.compliance claims: those are descriptive and were reset to
    # UNKNOWN across the board, so comparing them would report headroom that
    # depends on which entry happened to be hand-updated.
    declared = {compliance_registry.normalise_standard(s) for s in ctx.compliance}
    alt_std = set(compliance_registry.supported_standards(alt_impl.id))
    pri_std = set(compliance_registry.supported_standards(primary_impl.id))
    extra = sorted(alt_std - pri_std - declared)
    if extra:
        out.append(f"if {', '.join(extra).upper()} later becomes a requirement, "
                   f"this implementation already carries evidence for it")

    # Deployment model.
    alt_deploy = alt_impl.compatibility.deployment
    if alt_deploy != primary_impl.compatibility.deployment:
        out.append(f"if the workload has to run {alt_deploy.replace('_', '-')} "
                   f"rather than {primary_impl.compatibility.deployment}")

    # Volume headroom, using the same scale ordering the ranker uses.
    if (not ranking.scale_ok(primary_impl.compatibility.scale, ctx.needed_scale)
            and ranking.scale_ok(alt_impl.compatibility.scale, ctx.needed_scale)):
        out.append(f"at the assessed volume band ('{ctx.needed_scale}'), which "
                   f"part of the selected stack is not rated for")
    elif (_BAND_ORDER.get(_scale_rank(alt_impl), 0)
          > _BAND_ORDER.get(_scale_rank(primary_impl), 0)):
        out.append("if volume grows beyond the band the selected build is rated for")

    # Latency.
    if (alt_impl.compatibility.latency == "low"
            and primary_impl.compatibility.latency != "low"):
        out.append("if the interaction has to be real-time")

    # Build size, from the shared scope model — not a cost claim.
    if primary_band is not None and _BAND_ORDER[alt_band] < _BAND_ORDER[primary_band]:
        out.append(f"if delivery speed matters more than control: the derived "
                   f"engineering effort is '{alt_band.value}' against "
                   f"'{primary_band.value}' for the selected build")

    # Implementation model, quoting the calibration's own rationale so the
    # trade-off is auditable rather than asserted.
    if alt_impl.kind != primary_impl.kind:
        param = CALIBRATION.implementation_kind_points.get(alt_impl.kind.value)
        if param is not None:
            out.append(f"if a {alt_impl.kind.value.replace('_', '-')} build suits "
                       f"the team: {param.rationale}")

    # Being the sector baseline the primary departed from is itself a fact.
    ref = ctx.reference
    if ref is not None and alt_pattern.id == ref.pattern and primary_pattern.id != ref.pattern:
        out.append(f"it is the curated sector baseline ({ref.id}), which the "
                   f"selected architecture departed from")
    return out


def _has_model(impl: ImplementationOption) -> bool:
    """Does this implementation put a model in the loop, per the registry?"""
    return any(p.category in _MODEL_PROVIDER_CATEGORIES for p in impl.providers)


def _scale_rank(impl: ImplementationOption) -> EffortBand:
    """Map a scale rating onto the shared band ordering, so 'any' sorts top."""
    return {"small": EffortBand.SMALL, "medium": EffortBand.MEDIUM,
            "large": EffortBand.LARGE, "any": EffortBand.LARGE}.get(
                impl.compatibility.scale, EffortBand.SMALL)


def _uncertainties(
    metrics: list[PerformanceMetric], impl: ImplementationOption, ctx: ranking.RankingContext,
) -> list[str]:
    """11.7: the same evidence discipline as the primary solution.

    An alternative resting on an assumption is shown as resting on one.
    """
    out: list[str] = []
    assumed = [m.metric for m in metrics if m.estimate.provenance != Provenance.SOURCED]
    if assumed:
        out.append(f"expected performance for {', '.join(assumed)} is an "
                   f"assumption for this architecture, not a sourced benchmark")
    if not ranking.scale_ok(impl.compatibility.scale, ctx.needed_scale):
        out.append(f"this implementation is rated '{impl.compatibility.scale}', "
                   f"below the assessed '{ctx.needed_scale}' volume band")
    declared_but_undeclared = sorted(
        set(compliance_registry.known_standards())
        - set(compliance_registry.supported_standards(impl.id)))
    if declared_but_undeclared:
        out.append(f"no evidence on file for {len(declared_but_undeclared)} of the "
                   f"{len(compliance_registry.known_standards())} standards the "
                   f"evidence registry tracks; only a declared requirement is "
                   f"filtered on, so treat the rest as unestablished")
    out.append("no per-task automation estimate and no economic model were "
               "produced for this alternative (MVP scope, spec 11.8)")
    return out


def _build_alternative(
    state: AssessmentState, caps: list[Capability],
    pattern: SolutionPattern, impl: ImplementationOption,
    primary_pattern: SolutionPattern, primary_impl: ImplementationOption,
    primary_band: Optional[EffortBand],
    ctx: ranking.RankingContext,
) -> Alternative:
    strengths, limitations = _stack_metadata(impl)
    # Same scope model, same calibration, same implementation-kind modifier the
    # primary estimate used. HITL modes are omitted rather than guessed.
    effort = scope.effort_scope(state, caps, hitl_modes=None,
                                implementation_kind=impl.kind)
    metrics = performance.metrics_for(pattern.id, state.sector)
    involvement, involvement_basis = _human_involvement(impl, set(caps))

    controls = risks.controls_for(
        capabilities=caps, hitl_modes=[],
        integrations=len([t for t in (state.current_tools or []) if str(t).strip()]),
        compliance_gap=False,
        scale_shortfall=not ranking.scale_ok(impl.compatibility.scale, ctx.needed_scale),
        implementation=impl)

    when_preferable = _when_preferable(
        state, pattern, impl, effort.band,
        primary_pattern, primary_impl, primary_band, ctx)
    uncertainties = _uncertainties(metrics, impl, ctx)
    if not when_preferable:
        # An empty list is an honest answer, but a silent one. Saying so makes
        # a marginal alternative legible as marginal instead of looking like a
        # comparison that simply was not filled in.
        uncertainties.insert(0, (
            "no situation in which this would be preferable to the selected "
            "solution could be established from registry metadata"))

    different_architecture = pattern.id != primary_pattern.id
    return Alternative(
        id=f"{pattern.id}::{impl.id}",
        name=f"{pattern.name} via {impl.name}",
        source=AlternativeSource.REGISTRY,
        pattern_id=pattern.id, implementation_id=impl.id,
        implementation_kind=impl.kind,
        difference_kind=(DifferenceKind.ARCHITECTURE if different_architecture
                         else DifferenceKind.IMPLEMENTATION_MODEL),
        difference_from_primary=(
            f"a different architecture from the selected {primary_pattern.name}"
            if different_architecture else
            f"the same architecture as the selected solution, built as "
            f"{impl.kind.value.replace('_', ' ')} rather than "
            f"{primary_impl.kind.value.replace('_', ' ')}"),
        comparison=AlternativeComparison(
            approach=pattern.architecture,
            strengths=strengths, limitations=limitations,
            implementation_complexity=effort.band,
            implementation_complexity_basis=effort.explain(),
            expected_automation=metrics,
            automation_basis=("architecture-level performance metrics from the "
                              "registry and sector benchmark pack; no per-task "
                              "automation estimate is run for alternatives"),
            human_involvement=involvement, human_involvement_basis=involvement_basis,
            risks=[c.risk for c in controls],
            when_preferable=when_preferable,
        ),
        uncertainties=uncertainties,
    )


# ---------------------------------------------------------------------------
# The current process as a baseline (11.2)
# ---------------------------------------------------------------------------

def _status_quo_applies(estimate: SolutionEstimate, registry_count: int) -> bool:
    """11.2 qualifies this one: "where it is a meaningful baseline".

    Meaningful is read as: the AI case is not yet convincing on its own terms —
    the automation ceiling is low, confidence is low, or the registry could
    offer almost nothing else.
    """
    return (estimate.overall_automation.max < STATUS_QUO_CEILING.value
            or estimate.assessment_confidence == "low"
            or registry_count == 0)


def _status_quo(state: AssessmentState, estimate: SolutionEstimate) -> Alternative:
    """The user's current process, described only from AssessmentState.

    No registry entry is needed or invented: the facts are the user's own. The
    strengths are structural consequences of not building (there is no build to
    fail), not empirical claims about the process.
    """
    limitations = []
    if (state.problem or "").strip():
        limitations.append(f"the problem that prompted this assessment is "
                           f"unchanged: {state.problem.strip()}")
    limitations.append("no capacity is released and no quality change is expected")
    return Alternative(
        id="current_process",
        name="Continue with the current process",
        source=AlternativeSource.CURRENT_PROCESS,
        difference_kind=DifferenceKind.NO_AI_BASELINE,
        difference_from_primary="no AI system is built at all",
        comparison=AlternativeComparison(
            approach=(state.process or "the process as it runs today").strip(),
            strengths=["no implementation effort, and no new failure mode "
                       "introduced into the workflow",
                       "no vendor, model or platform dependency to manage"],
            limitations=limitations,
            implementation_complexity=None,
            implementation_complexity_basis="no implementation is undertaken",
            expected_automation=[],
            automation_basis="current-process performance is whatever the "
                             "assessment recorded; nothing is projected",
            human_involvement=HumanInvolvement.FULLY_HUMAN,
            human_involvement_basis=["the process runs as it does today"],
            risks=["the operational exposure that prompted the assessment "
                   "remains in place"],
            when_preferable=[
                "if the projected automation does not justify the "
                "implementation effort and ongoing operation"],
        ),
        uncertainties=[
            "the current process is compared qualitatively here; its cost is "
            "modelled by the Economic Engine, not by this section",
        ],
    )


# ---------------------------------------------------------------------------
# LLM explanation (11.5)
# ---------------------------------------------------------------------------

def _explain(state: AssessmentState, primary_name: str,
             alts: list[Alternative]) -> list[str]:
    """Ask the model to contextualise a FIXED list. Mutates `alts` in place.

    The model is given ids and never asked to produce one; a key it invents
    matches nothing and is discarded. Everything it returns goes through
    `guard()` before it is stored.
    """
    if not alts:
        return []
    try:
        from llm.openai_client import complete_json
    except Exception as exc:            # pragma: no cover - import guard
        return [f"LLM unavailable, alternatives are shown unexplained ({exc})"]

    system = (
        "You are explaining pre-selected alternative approaches to a business "
        "process owner. The alternatives have already been chosen; your only "
        "job is to say, in plain language, what each one would mean for THIS "
        "process.\n\n"
        "Return ONLY JSON: {\"explanations\": [{\"id\": str, \"text\": str}]}\n\n"
        "Rules:\n"
        "- Use ONLY the ids given to you. Do not add an approach of your own.\n"
        "- Do NOT state any number, percentage, cost, duration or effort "
        "figure. Those are supplied separately and yours would be discarded.\n"
        "- Do NOT recommend, rank, or tell the user what to choose. Describe "
        "what the approach is and the conditions under which it may suit.\n"
        "- Two or three sentences each."
    )
    lines = []
    for a in alts:
        lines.append(
            f"id: {a.id}\nname: {a.name}\napproach: {a.comparison.approach}\n"
            f"difference from the selected solution: {a.difference_from_primary}\n"
            f"strengths: {a.comparison.strengths}\n"
            f"limitations: {a.comparison.limitations}")
    user = (f"Sector: {state.sector.value}\nProcess: {state.process}\n"
            f"Selected solution: {primary_name}\n\nAlternatives:\n\n"
            + "\n\n".join(lines))

    try:
        result = complete_json(system, user)
    except Exception as exc:
        return [f"LLM call failed, alternatives are shown unexplained ({exc})"]

    by_id = {a.id: a for a in alts}
    notes: list[str] = []
    for row in (result.get("explanations") or []):
        if not isinstance(row, dict):
            continue
        alt = by_id.get(str(row.get("id", "")))
        if alt is None:
            notes.append(f"discarded an explanation for {row.get('id')!r}, which "
                         f"is not one of the selected alternatives")
            continue
        alt.explanation, dropped = guard(str(row.get("text", "")), alt.id)
        notes.extend(dropped)
    return notes


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

NO_ALTERNATIVE_STATEMENT = ("No materially different alternative could be "
                            "established from the available evidence and "
                            "constraints.")

ORDERING_BASIS = (
    "Display order reuses the primary ranker's own score for each candidate "
    "(solution/ranking.py). No second score is calculated and the order is not "
    "a ranking of preference — spec 11.4/11.6.")


def derive(
    state: AssessmentState,
    estimate: SolutionEstimate,
    *,
    explain: bool = True,
) -> AlternativesResult:
    """Alternatives for a completed estimate. Never modifies `estimate`."""
    if not estimate.recommended_pattern:
        return AlternativesResult(
            statement=("No alternatives are shown because no primary solution "
                       "was established for this assessment."),
            categories_not_in_registry=list(CATEGORIES_NOT_IN_REGISTRY),
            ordering_basis=ORDERING_BASIS)

    caps = _required_capabilities(estimate)
    if not caps:
        return AlternativesResult(
            statement=("No alternatives are shown because the primary estimate "
                       "recorded no validated capability requirement to "
                       "compare against."),
            categories_not_in_registry=list(CATEGORIES_NOT_IN_REGISTRY),
            ordering_basis=ORDERING_BASIS)

    ctx = ranking.ranking_context(state)
    primary_pattern = patterns_mod.pattern(estimate.recommended_pattern)
    primary_impl = next(
        (i for i in primary_pattern.implementations
         if i.id == estimate.recommended_implementation), None)
    if primary_impl is None:
        return AlternativesResult(
            statement=("No alternatives are shown because the selected "
                       "implementation could not be resolved in the registry."),
            categories_not_in_registry=list(CATEGORIES_NOT_IN_REGISTRY),
            ordering_basis=ORDERING_BASIS)

    primary_band = scope.effort_scope(
        state, caps, hitl_modes=None, implementation_kind=primary_impl.kind).band
    primary_key = _materiality_key(primary_pattern.id, primary_impl.kind)

    pairs, rejected = _covering_implementations(set(caps), ctx.compliance)

    # Group by materiality, so a vendor swap cannot occupy an alternative slot.
    groups: dict[tuple[str, str], list[tuple[SolutionPattern, ImplementationOption]]] = {}
    for pattern, impl in pairs:
        key = _materiality_key(pattern.id, impl.kind)
        if key == primary_key:
            rejected.append(RejectedAlternative(
                pattern_id=pattern.id, implementation_id=impl.id,
                reason=("not materially different: same architecture and same "
                        "implementation model as the selected solution")))
            continue
        groups.setdefault(key, []).append((pattern, impl))

    chosen: list[tuple[SolutionPattern, ImplementationOption]] = []
    for key, members in groups.items():
        # Within one materiality group, take the option the primary ranker's
        # own preference order would take: scale-fitting first, then least
        # complex. The rest are recorded as vendor variants, not alternatives.
        members = sorted(members, key=lambda pi: (
            not ranking.scale_ok(pi[1].compatibility.scale, ctx.needed_scale),
            _COMPLEXITY_ORDER[pi[1].compatibility.technical_complexity]))
        head, rest = members[0], members[1:]
        reason = _has_sufficient_metadata(*head)
        if reason:
            rejected.append(RejectedAlternative(
                pattern_id=head[0].id, implementation_id=head[1].id, reason=reason))
            continue
        chosen.append(head)
        for pattern, impl in rest:
            rejected.append(RejectedAlternative(
                pattern_id=pattern.id, implementation_id=impl.id,
                reason=(f"same approach as {head[1].id}: same architecture and "
                        f"implementation model, different vendor")))

    scored = [(ranking.score_candidate(p, i, ctx).score, p, i) for p, i in chosen]
    scored.sort(key=lambda t: t[0], reverse=True)

    alternatives: list[Alternative] = []
    for score, pattern, impl in scored[:MAX_ALTERNATIVES]:
        alt = _build_alternative(state, caps, pattern, impl,
                                 primary_pattern, primary_impl, primary_band, ctx)
        alt.ranking_score = score
        alternatives.append(alt)
    for score, pattern, impl in scored[MAX_ALTERNATIVES:]:
        rejected.append(RejectedAlternative(
            pattern_id=pattern.id, implementation_id=impl.id,
            reason=(f"credible, but beyond the {MAX_ALTERNATIVES} alternatives "
                    f"spec 11 asks to surface")))

    registry_count = len(alternatives)
    if _status_quo_applies(estimate, registry_count):
        alternatives.append(_status_quo(state, estimate))

    guard_notes = _explain(state, f"{primary_pattern.name} via {primary_impl.name}",
                           [a for a in alternatives
                            if a.source == AlternativeSource.REGISTRY]) if explain else []

    statement = "" if alternatives else NO_ALTERNATIVE_STATEMENT
    log.info("alternatives: %d surfaced, %d rejected", len(alternatives), len(rejected))
    return AlternativesResult(
        alternatives=alternatives, statement=statement, rejected=rejected,
        categories_not_in_registry=list(CATEGORIES_NOT_IN_REGISTRY),
        ordering_basis=ORDERING_BASIS, llm_guard_notes=guard_notes)
