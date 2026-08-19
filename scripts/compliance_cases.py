"""Deterministic compliance-filtering tests — cases A-I of the integration spec.

No LLM anywhere: every verdict below comes from the evidence registry and
deterministic code.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Imports resolve from the editable src-layout installation.
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from lib.compliance import (
    ClaimStatus,
    REGISTRY_TO_EVIDENCE,
    evaluate_component,
    evaluate_implementation,
    load_attestations,
    normalise_standard,
    unmapped_evidence_implementations,
)
from schemas.assessment_state import AssessmentState, RiskInputs, Sector
from solution.patterns import patterns_covering
from solution.ranking import rank_candidates
from solution.schema import Capability

failures: list[str] = []
CS_CAPS = {Capability.INGEST, Capability.CLASSIFY, Capability.GENERATE,
           Capability.ROUTE, Capability.HUMAN_ESCALATE}


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


def state_for(standards: list[str]) -> AssessmentState:
    return AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="p",
                           process="ticket triage", monthly_volume=5000,
                           risk=RiskInputs(compliance_exposure=standards))


def case_A_supported() -> None:
    print("\nA — OpenAI API + HIPAA survives on BAA evidence")
    v = evaluate_component("openai_api", "HIPAA")
    print(f"    status={v.status.value}  evidence={v.evidence_ids}")
    print(f"    scope: {v.scope[:110]}")
    check("A", v.status == ClaimStatus.SUPPORTED,
          "openai_api HIPAA evidence is SUPPORTED")
    check("A", v.evidence_ids and "baa" in v.evidence_ids[0].lower(),
          "the supporting record is the BAA-availability evidence")
    check("A", "does not itself certify" in (v.scope or "").lower()
          or "baa availability" in (v.scope or "").lower(),
          "the scope preserves that BAA availability != HIPAA compliance (section 9)")


def case_B_unknown() -> None:
    print("\nB — Make + HIPAA does NOT satisfy a hard requirement (UNKNOWN)")
    v = evaluate_implementation("make", "HIPAA")
    print(f"    status={v.status.value}")
    check("B", v.status == ClaimStatus.UNKNOWN, "Make/HIPAA is UNKNOWN")
    check("B", not v.satisfies, "UNKNOWN cannot satisfy a hard requirement")
    # And Make's own SOC 2 evidence does not leak across to HIPAA.
    check("B", evaluate_implementation("make", "SOC 2").satisfies,
          "Make DOES satisfy SOC 2, proving the UNKNOWN is requirement-specific")


def case_C_explicit_exclusion() -> None:
    print("\nC — Zapier + HIPAA is explicitly excluded by the vendor")
    v = evaluate_implementation("zapier", "HIPAA")
    print(f"    status={v.status.value}")
    print(f"    {v.reason[:150]}")
    check("C", v.status == ClaimStatus.NOT_APPLICABLE,
          "Zapier/HIPAA is an explicit exclusion, not merely unknown")
    check("C", v.excluded, "the exclusion marks the implementation incompatible")
    check("C", evaluate_implementation("zapier", "SOC 2").satisfies,
          "Zapier still satisfies SOC 2 — the exclusion is HIPAA-specific")


def case_D_vendor_isolation() -> None:
    print("\nD — OpenAI evidence does not qualify Anthropic")
    # Both vendors happen to hold their OWN HIPAA evidence, so HIPAA proves
    # nothing about isolation. data_residency is the discriminating case:
    # OpenAI has it, Anthropic does not.
    openai = evaluate_component("openai_api", "data_residency")
    anthropic = evaluate_component("anthropic_api", "data_residency")
    print(f"    openai_api    data_residency -> {openai.status.value}")
    print(f"    anthropic_api data_residency -> {anthropic.status.value}")
    check("D", openai.status == ClaimStatus.SUPPORTED,
          "openai_api has data-residency evidence")
    check("D", anthropic.status == ClaimStatus.UNKNOWN,
          "anthropic_api stays UNKNOWN — OpenAI's evidence does not cross over")

    # And each vendor's SUPPORTED verdict cites its OWN records.
    o_ids = evaluate_component("openai_api", "HIPAA").evidence_ids
    a_ids = evaluate_component("anthropic_api", "HIPAA").evidence_ids
    print(f"    HIPAA evidence ids: openai={o_ids} anthropic={a_ids}")
    check("D", all("openai" in i for i in o_ids) and all("anthropic" in i for i in a_ids),
          "each vendor's verdict cites only its own attestations")


def case_E_product_isolation() -> None:
    print("\nE — Vertex AI evidence does not qualify the Gemini Developer API")
    vertex = evaluate_component("google_vertex_ai_gemini", "SOC 2")
    dev = evaluate_component("google_gemini_developer_api", "SOC 2")
    print(f"    google_vertex_ai_gemini      SOC 2 -> {vertex.status.value}")
    print(f"    google_gemini_developer_api  SOC 2 -> {dev.status.value}")
    check("E", vertex.status == ClaimStatus.SUPPORTED,
          "Vertex AI has SOC 2 evidence")
    check("E", dev.status != ClaimStatus.SUPPORTED,
          "the standalone Developer API is NOT qualified by Vertex evidence")


def case_F_deployment_isolation() -> None:
    print("\nF — n8n Cloud evidence does not qualify n8n self-hosted")
    cloud = evaluate_component("n8n_cloud", "SOC 2")
    self_hosted = evaluate_component("n8n_self_hosted", "SOC 2")
    print(f"    n8n_cloud        SOC 2 -> {cloud.status.value}")
    print(f"    n8n_self_hosted  SOC 2 -> {self_hosted.status.value}")
    check("F", cloud.status == ClaimStatus.SUPPORTED, "n8n Cloud has SOC 2 evidence")
    check("F", self_hosted.status != ClaimStatus.SUPPORTED,
          "self-hosted is NOT qualified by the Cloud attestation")
    check("F", "n8n_self_hosted" in REGISTRY_TO_EVIDENCE["n8n"]
          and "n8n_cloud" not in REGISTRY_TO_EVIDENCE["n8n"],
          "the registry's self-host n8n entry maps to n8n_self_hosted only")


def case_G_generic_self_hosted() -> None:
    print("\nG — custom_code_self_hosted is UNKNOWN, not NOT_APPLICABLE")
    v = evaluate_component("custom_code_self_hosted", "HIPAA")
    override = [a for a in load_attestations()
                if a.implementation_id == "custom_code_self_hosted"][0]
    print(f"    status={v.status.value}")
    print(f"    override: {override.status_override_reason[:120]}")
    check("G", v.status == ClaimStatus.UNKNOWN,
          "a requirement CAN apply to customer-operated code — absence of a "
          "vendor attestation is missing evidence, not inapplicability")
    check("G", override.status_override_reason,
          "the reclassification is recorded, not silent")
    check("G", v.status != ClaimStatus.SUPPORTED,
          "it is never promoted to SUPPORTED")


def case_H_generic_vector_db() -> None:
    print("\nH — rag_vector_database_infrastructure is UNKNOWN, not SUPPORTED")
    v = evaluate_component("rag_vector_database_infrastructure", "SOC 2")
    print(f"    status={v.status.value}")
    check("H", v.status == ClaimStatus.UNKNOWN,
          "a generic vector-DB category carries no attestation")
    check("H", evaluate_implementation("custom_rag", "SOC 2").status
          == ClaimStatus.UNKNOWN,
          "a RAG build inherits the UNKNOWN from its vector store, even though "
          "its LLM component has evidence (composition, section 10)")


def case_I_no_compliant_candidate() -> None:
    print("\nI — a hard requirement nothing satisfies produces a GAP, not a winner")
    out = rank_candidates(state_for(["HIPAA"]), patterns_covering(CS_CAPS), CS_CAPS)
    print(f"    survivors={len(out.ranked)}  excluded={len(out.excluded)}  "
          f"gap={out.compliance_gap}")
    for e in out.excluded[:3]:
        print(f"      {e.implementation_id:<16} {e.standard:<7} {e.status}")
    check("I", out.compliance_gap and not out.ranked,
          "no architecture is recommended when none can satisfy the requirement")
    check("I", out.excluded, "every excluded candidate is preserved with a reason")
    check("I", all(e.reason for e in out.excluded),
          "each exclusion states why")
    check("I", "lowering the requirement" in out.compliance_statement,
          "the statement refuses to weaken the requirement to produce a winner")

    # And a requirement that CAN be met still ranks normally.
    ok = rank_candidates(state_for(["SOC 2"]), patterns_covering(CS_CAPS), CS_CAPS)
    print(f"    SOC 2: survivors={[r.chosen_implementation for r in ok.ranked]}")
    check("I", ok.ranked and not ok.compliance_gap,
          "a satisfiable requirement still yields a ranked winner")


def case_normalisation() -> None:
    print("\nNORM — multi-standard records split without changing meaning")
    ids = {a.evidence_id for a in load_attestations()}
    check("NORM", "aws_bedrock_soc_iso_v1#soc_2" in ids,
          "a combined SOC 1/2/3 + ISO record is split per standard")
    check("NORM", normalise_standard("ISO/IEC 27001:2022") == "iso 27001",
          "ISO/IEC is ONE standard — it is not split into 'ISO' and 'IEC 27001'")
    check("NORM", normalise_standard("SOC 2 Type II") == normalise_standard("SOC 2"),
          "a SOC 2 Type II attestation matches a SOC 2 requirement")
    unmapped = unmapped_evidence_implementations()
    print(f"    unmapped evidence implementations: {unmapped}")
    check("NORM", "n8n_cloud" in unmapped and "anthropic_api" in unmapped,
          "evidence for implementations absent from the registry is reported, "
          "not turned into new registry entries")


if __name__ == "__main__":
    case_A_supported()
    case_B_unknown()
    case_C_explicit_exclusion()
    case_D_vendor_isolation()
    case_E_product_isolation()
    case_F_deployment_isolation()
    case_G_generic_self_hosted()
    case_H_generic_vector_db()
    case_I_no_compliant_candidate()
    case_normalisation()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL COMPLIANCE CASES PASSED")
