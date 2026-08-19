"""Deterministic validation of solution-pattern ranking.

No LLM involved: capabilities are supplied directly so only the deterministic
filter/rank path runs. Exits non-zero on any failure.

One regression case plus three adversarial cases. The adversarial set was
never written down (docs/estimator_todo.md just says "run the 3 adversarial
cases"), so it is defined here, each case targeting a distinct way a
reference-anchored ranker fails:

  TIE  document-processing tie      — two patterns cover the same capabilities
                                      and used to tie on score, resolved by
                                      registry declaration order.
  A1   over-anchoring               — conditions warrant departing from the
                                      baseline; does the ranker adapt, or
                                      return the low-code baseline regardless?
  A2   constraint blindness         — a compliance constraint nothing covers;
                                      is the gap surfaced as risk, or does
                                      something quietly rank top clean?
  A3   unsanctioned drift           — nothing warrants deviating; can a foreign
                                      pattern still beat the baseline?
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The ranking path needs no LLM; stub the SDK so no key/network is required.
if "openai" not in sys.modules:
    _stub = types.ModuleType("openai")
    _stub.OpenAI = lambda **kw: None  # type: ignore[attr-defined]
    sys.modules["openai"] = _stub

from schemas.assessment_state import AssessmentState, EffortBand, RiskInputs, Sector
from solution.patterns import patterns_covering
from solution.ranking import MAX_SCORE, filter_and_rank
from solution.reference_solutions import reference_for
from solution.schema import Capability

DOC_CAPS = {Capability.INGEST, Capability.EXTRACT, Capability.CLASSIFY,
            Capability.VALIDATE, Capability.HUMAN_REVIEW}
CS_CAPS = {Capability.INGEST, Capability.CLASSIFY, Capability.GENERATE,
           Capability.ROUTE, Capability.HUMAN_ESCALATE}

failures: list[str] = []


def check(case: str, condition: bool, description: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"    [{status}] {description}")
    if not condition:
        failures.append(f"{case}: {description}")


def rank(state: AssessmentState, caps: set[Capability]):
    ranked = filter_and_rank(state, patterns_covering(caps), caps)
    for r in ranked:
        impl = next(i for i in r.pattern.implementations
                    if i.id == r.chosen_implementation)
        bits = " ".join(f"{k.split('_')[0]}={v}" for k, v in r.breakdown.items())
        print(f"    {r.score:>5.2f}/{MAX_SCORE}  {r.pattern.id:<24} "
              f"impl={r.chosen_implementation:<12} "
              f"({impl.compatibility.technical_complexity.value})  {bits}")
    return ranked


def case_tie() -> None:
    print("\nTIE — document processing, 20k invoices/mo")
    print("  before: ai_assisted_workflow and document_pipeline both scored 4.0")
    state = AssessmentState(sector=Sector.DOCUMENT_PROCESSING,
                            problem="automate invoice processing",
                            process="invoice intake and coding",
                            monthly_volume=20000)
    ranked = rank(state, DOC_CAPS)
    ref = reference_for(Sector.DOCUMENT_PROCESSING)
    check("TIE", ranked[0].pattern.id == "document_pipeline",
          f"top candidate is the document pipeline (got {ranked[0].pattern.id})")
    check("TIE", ranked[0].pattern.id == ref.pattern,
          "top candidate is the sector's reference pattern")
    check("TIE", ranked[0].score > ranked[1].score,
          f"tie is broken on score, not declaration order "
          f"({ranked[0].score} vs {ranked[1].score})")
    cs_shaped = next(r for r in ranked if r.pattern.id == "ai_assisted_workflow")
    check("TIE", cs_shaped.reference_alignment < ranked[0].reference_alignment,
          "the support-shaped pattern is penalised on reference alignment")


def case_a1_over_anchoring() -> None:
    print("\nA1 — over-anchoring: customer support at 60k tickets/mo")
    print("  cs_high_volume (>50k) fires and releases to custom/managed_service")
    state = AssessmentState(sector=Sector.CUSTOMER_SUPPORT,
                            problem="automate tier-1 support",
                            process="ticket triage and reply",
                            monthly_volume=60000)
    ranked = rank(state, CS_CAPS)
    top = ranked[0]
    ref = reference_for(Sector.CUSTOMER_SUPPORT)
    impl = next(i for i in top.pattern.implementations if i.id == top.chosen_implementation)
    check("A1", top.pattern.id == ref.pattern,
          "baseline architecture is retained (the condition licenses a different "
          "build, not a different architecture)")
    check("A1", impl.compatibility.scale in ("any", "large"),
          f"chosen implementation is rated for large volume (got scale="
          f"{impl.compatibility.scale})")
    check("A1", top.chosen_implementation != "n8n",
          "did NOT return the low-code baseline build that cannot carry 60k/mo")
    check("A1", impl.compatibility.technical_complexity == EffortBand.LARGE,
          "effort band reflects the custom build, so cost is not understated")
    check("A1", top.active_deviations,
          f"the fired condition is recorded: {top.active_deviations}")
    check("A1", top.reference_alignment < 1.0,
          f"alignment reflects an active deviation (got {top.reference_alignment})")


def case_a2_constraint_blindness() -> None:
    print("\nA2a — no implementation may be selected on an UNBACKED compliance claim")
    # The registry once asserted "n8n supports gdpr", "custom_workflow supports
    # hipaa" and so on with no evidence behind any of it. Those claims are now
    # `unknown` (finesse spec 5), so no candidate can satisfy a constraint until
    # a real attestation exists. The positive path — an evidence-backed claim
    # DOES satisfy — is exercised in estimator_cases.py case M.
    covered = AssessmentState(sector=Sector.CUSTOMER_SUPPORT,
                              problem="automate patient support enquiries",
                              process="ticket triage", monthly_volume=5000,
                              risk=RiskInputs(compliance_exposure=["hipaa"]))
    ranked_c = rank(covered, CS_CAPS)
    top_c = ranked_c[0]
    impl_c = next(i for i in top_c.pattern.implementations
                  if i.id == top_c.chosen_implementation)
    claims = {c.standard: c for c in impl_c.compatibility.compliance}
    hipaa = claims.get("hipaa")
    check("A2a", hipaa is None or not hipaa.satisfies(),
          f"the chosen implementation ({top_c.chosen_implementation}) does not "
          f"claim verified hipaa support")
    check("A2a", top_c.breakdown["compliance_fit"] == 0.0,
          "an unevidenced claim earns nothing on the compliance term")
    check("A2a", any("compliance" in r.lower() for r in top_c.risks),
          "the unmet constraint is flagged as a risk on the winner")

    print("\nA2b — constraint blindness: a constraint NOTHING covers")
    state = AssessmentState(sector=Sector.CUSTOMER_SUPPORT,
                            problem="automate federal support enquiries",
                            process="ticket triage",
                            monthly_volume=5000,
                            risk=RiskInputs(compliance_exposure=["fedramp"]))
    ranked = rank(state, CS_CAPS)
    top = ranked[0]
    check("A2", top.breakdown["compliance_fit"] == 0.0,
          "compliance term scores zero — the gap is priced into the ranking")
    check("A2", any("compliance" in r.lower() for r in top.risks),
          f"a compliance risk is attached to the winner: {top.risks}")
    check("A2", all(r.breakdown["compliance_fit"] == 0.0 for r in ranked),
          "no candidate is allowed to look compliant when none is")
    check("A2", top.score < MAX_SCORE,
          f"nothing scores a clean sheet under an uncovered constraint "
          f"(top={top.score})")


def case_a3_unsanctioned_drift() -> None:
    print("\nA3 — unsanctioned drift: small support workload, no conditions fire")
    state = AssessmentState(sector=Sector.CUSTOMER_SUPPORT,
                            problem="automate tier-1 support",
                            process="ticket triage and reply",
                            monthly_volume=500)
    ranked = rank(state, CS_CAPS)
    ref = reference_for(Sector.CUSTOMER_SUPPORT)
    top = ranked[0]
    check("A3", top.pattern.id == ref.pattern,
          f"reference baseline wins when nothing warrants deviating "
          f"(got {top.pattern.id})")
    check("A3", not top.active_deviations,
          "no deviation conditions are reported as active")
    check("A3", top.reference_alignment == 1.0,
          "alignment is full for the unmodified baseline")
    runner_up = ranked[1]
    check("A3", top.score - runner_up.score >= 2.0,
          f"foreign patterns are decisively behind, not marginally "
          f"({top.score} vs {runner_up.score})")


def case_manual_conditions() -> None:
    print("\nEXTRA — conditions that cannot be evaluated are surfaced, not dropped")
    state = AssessmentState(sector=Sector.DOCUMENT_PROCESSING,
                            problem="automate invoice processing",
                            process="invoice intake", monthly_volume=20000)
    ranked = filter_and_rank(state, patterns_covering(DOC_CAPS), DOC_CAPS)
    unevaluated = ranked[0].unevaluated_conditions
    for c in unevaluated:
        print(f"    - {c}")
    check("EXTRA", len(unevaluated) == 2,
          f"both MANUAL conditions on doc_baseline are surfaced (got {len(unevaluated)})")


if __name__ == "__main__":
    case_tie()
    case_a1_over_anchoring()
    case_a2_constraint_blindness()
    case_a3_unsanctioned_drift()
    case_manual_conditions()

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL RANKING CASES PASSED")
