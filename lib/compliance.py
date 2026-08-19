"""Compliance evidence registry and deterministic evaluation.

Loads `data/compliance_attestations.json` (preserved verbatim) and turns it
into per-implementation, per-standard verdicts the ranker can filter on.

Four rules this module exists to enforce:

1. EVIDENCE IS IMPLEMENTATION-SPECIFIC. An OpenAI attestation never qualifies
   Anthropic; Vertex AI never qualifies the standalone Gemini Developer API;
   n8n Cloud never qualifies n8n self-hosted. Scope is not inherited.

2. COMPOSITION IS EVALUATED, NOT THE LABEL. A custom build is its components:
   the code, the model API, the vector store. Every component must satisfy a
   requirement for the implementation to satisfy it.

3. UNKNOWN NEVER BECOMES SUPPORTED. Insufficient evidence cannot satisfy a
   hard requirement, and another provider's evidence cannot fill the gap.

4. SCOPE IS NOT OVERSTATED. "BAA available" is not "HIPAA compliant"; a vendor
   certification is not inherited by a customer deployment. The scope and
   limitation text travels with every verdict.

No LLM is involved anywhere in this module.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

_PATH = Path(__file__).resolve().parent.parent / "data" / "compliance_attestations.json"


class ClaimStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Section 4: generic implementation CATEGORIES must not read NOT_APPLICABLE
# merely because no vendor attestation exists. A requirement can absolutely
# apply to a customer-operated self-hosted deployment or to a specific vector
# database — the absence of a vendor attestation is missing evidence, not
# inapplicability. Overridden at load time; the source file stays untouched.
GENERIC_CATEGORIES = {
    "custom_code_self_hosted": (
        "compliance for custom/self-hosted code depends on how the customer "
        "operates it; no vendor attestation is possible, but the requirement "
        "still applies"),
    "rag_vector_database_infrastructure": (
        "a category spanning many distinct products, each with its own posture; "
        "a concrete vector store would need its own implementation_id and "
        "evidence"),
}

# Standards that a record covering "all_standards" speaks to.
_ALL_STANDARDS = "all_standards"


class Attestation(BaseModel):
    evidence_id: str
    implementation_id: str
    provider: Optional[str] = ""
    standard: str
    claim_status: ClaimStatus
    attestation_type: Optional[str] = ""
    evidence_type: Optional[str] = ""
    title: Optional[str] = ""
    scope: Optional[str] = ""
    source_url: Optional[str] = ""
    source_name: Optional[str] = ""
    retrieved_at: Optional[str] = ""
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    notes: Optional[str] = ""
    # Set when this record was split out of a multi-standard record, or when a
    # generic-category status was overridden. Lineage is never discarded.
    derived_from: Optional[str] = None
    status_override_reason: Optional[str] = None


def normalise_standard(value: str) -> str:
    """Canonical MATCHING key for a standard.

    The raw text is always preserved on the record; only the lookup key is
    canonicalised. A SOC 2 Type II attestation satisfies a "SOC 2" requirement
    (Type II is the stronger report), so the type qualifier is collapsed for
    matching while the record still says which type it was.
    """
    v = (value or "").strip().lower()
    v = v.replace("iso/iec", "iso").replace("soc-", "soc ")
    v = re.sub(r":\s*\d{4}", "", v)             # ISO 27001:2022 -> iso 27001
    v = re.sub(r"\s*\(.*?\)\s*", " ", v)        # drop parenthetical qualifiers
    # Collapse every SOC type qualifier: "SOC 2 Type II", "SOC 2 Type 2" and
    # "SOC 2 Type I & Type II" all satisfy a "SOC 2" requirement. Records are
    # already one-standard-per-record at this point, so trailing text after the
    # SOC number is a qualifier, never a second standard.
    v = re.sub(r"^soc\s*([123])\b.*$", r"soc \1", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def _split_standards(raw: str) -> list[str]:
    """Section 3: one record per standard, without changing meaning or scope.

    "ISO/IEC 27001" is ONE standard whose name contains a slash — splitting it
    would produce a meaningless "ISO" and "IEC 27001" pair, so the token is
    protected before the split and restored afterwards.
    """
    protected = re.sub(r"iso\s*/\s*iec", "ISO_IEC", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\(.*?\)\s*", " ", protected)
    parts = [p.strip().replace("ISO_IEC", "ISO/IEC")
             for p in re.split(r"\s*/\s*|\s*,\s*", cleaned) if p.strip()]
    return parts or [raw.strip()]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


@lru_cache(maxsize=1)
def load_attestations() -> list[Attestation]:
    """Load, normalise multi-standard records, and apply the generic override."""
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    out: list[Attestation] = []
    for rec in raw.get("attestations", []):
        standards = _split_standards(rec["standard"])
        multi = len(standards) > 1
        for std in standards:
            data = dict(rec)
            data["standard"] = std
            if multi:
                data["evidence_id"] = f"{rec['evidence_id']}#{_slug(std)}"
                data["derived_from"] = rec["evidence_id"]
                # Scope and notes carry over unchanged — splitting must not
                # widen or narrow what the original evidence actually said.
            impl = rec["implementation_id"]
            if (impl in GENERIC_CATEGORIES
                    and data["claim_status"] == ClaimStatus.NOT_APPLICABLE.value):
                data["claim_status"] = ClaimStatus.UNKNOWN.value
                data["status_override_reason"] = (
                    f"reclassified NOT_APPLICABLE -> UNKNOWN: "
                    f"{GENERIC_CATEGORIES[impl]}")
            out.append(Attestation.model_validate(data))
    return out


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str], list[Attestation]]:
    idx: dict[tuple[str, str], list[Attestation]] = {}
    for a in load_attestations():
        idx.setdefault((a.implementation_id, normalise_standard(a.standard)), []).append(a)
    return idx


def evidence_for(evidence_impl_id: str, standard: str) -> list[Attestation]:
    """Attestations for one evidence-implementation and one standard.

    A record covering `all_standards` applies to any requested standard for
    that implementation.
    """
    key = normalise_standard(standard)
    direct = _index().get((evidence_impl_id, key), [])
    catch_all = _index().get((evidence_impl_id, _ALL_STANDARDS), [])
    return list(direct) + list(catch_all)


class ComponentVerdict(BaseModel):
    component_id: str
    status: ClaimStatus
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    scope: Optional[str] = ""


class ComplianceVerdict(BaseModel):
    implementation_id: str
    standard: str
    status: ClaimStatus
    components: list[ComponentVerdict] = Field(default_factory=list)
    reason: str = ""

    @property
    def satisfies(self) -> bool:
        """Only SUPPORTED satisfies a hard requirement (section 7)."""
        return self.status == ClaimStatus.SUPPORTED

    @property
    def excluded(self) -> bool:
        """An explicit vendor exclusion makes the implementation incompatible."""
        return self.status == ClaimStatus.NOT_APPLICABLE


def evaluate_component(evidence_impl_id: str, standard: str) -> ComponentVerdict:
    records = evidence_for(evidence_impl_id, standard)
    if not records:
        return ComponentVerdict(
            component_id=evidence_impl_id, status=ClaimStatus.UNKNOWN,
            reason=f"no evidence on file for {evidence_impl_id!r} and {standard!r}")

    # An explicit exclusion dominates: the vendor says the workload is not
    # supported, which is stronger information than any other record.
    excluded = [r for r in records if r.claim_status == ClaimStatus.NOT_APPLICABLE]
    if excluded:
        r = excluded[0]
        return ComponentVerdict(
            component_id=evidence_impl_id, status=ClaimStatus.NOT_APPLICABLE,
            evidence_ids=[r.evidence_id], scope=r.scope,
            reason=f"explicit exclusion [{r.evidence_id}]: {r.notes[:180]}")

    supported = [r for r in records if r.claim_status == ClaimStatus.SUPPORTED]
    if supported:
        r = supported[0]
        return ComponentVerdict(
            component_id=evidence_impl_id, status=ClaimStatus.SUPPORTED,
            evidence_ids=[x.evidence_id for x in supported], scope=r.scope,
            reason=f"{r.attestation_type or 'attestation'} [{r.evidence_id}] "
                   f"from {r.source_name}, retrieved {r.retrieved_at}. "
                   f"SCOPE: {r.scope[:200]}")

    r = records[0]
    return ComponentVerdict(
        component_id=evidence_impl_id, status=ClaimStatus.UNKNOWN,
        evidence_ids=[r.evidence_id], scope=r.scope,
        reason=(r.status_override_reason
                or f"evidence exists but is UNKNOWN [{r.evidence_id}]: {r.notes[:160]}"))


# ---------------------------------------------------------------------------
# Mapping: Solution Registry implementation -> evidence components
# ---------------------------------------------------------------------------
#
# Section 1: every evidence record must resolve to an implementation that
# actually exists in solution/patterns.py. Nothing is created just because it
# appears in the evidence file.
#
# Section 10: a custom/self-hosted build is evaluated as its COMPOSITION — the
# code plus the model API plus the vector store — never on the "custom code"
# label alone.
#
# Section 6: scope is not inherited. The registry's n8n entry is explicitly
# self-hosted ("n8n (self-host low-code)"), so it maps to n8n_self_hosted and
# NEVER to n8n_cloud.

REGISTRY_TO_EVIDENCE: dict[str, list[str]] = {
    # Low-code platforms map to their own vendor record, plus the model API
    # the registry attaches to them.
    "n8n": ["n8n_self_hosted", "openai_api"],
    # Deterministic rules builds of the same platforms. The model API is NOT a
    # component because there is no model in the stack — a rules workflow has
    # nothing to attest for on that axis. This is the compliance consequence of
    # removing the model, and it must not be papered over by copying the
    # AI variant's composition.
    "n8n_rules": ["n8n_self_hosted"],
    "make_rules": ["make"],
    "custom_rules": ["custom_code_self_hosted"],
    "make": ["make", "openai_api"],
    "zapier": ["zapier", "openai_api"],
    # Generic managed service: no specific vendor is named by the registry, so
    # the category itself carries no attestation.
    "managed_ai": ["custom_code_self_hosted", "openai_api"],
    "rag_managed": ["rag_vector_database_infrastructure", "openai_api"],
    # Custom builds: code + model API (+ vector store where the pattern uses one).
    "custom_workflow": ["custom_code_self_hosted", "openai_api"],
    "custom_docpipe": ["custom_code_self_hosted", "openai_api"],
    "custom_rag": ["custom_code_self_hosted", "rag_vector_database_infrastructure",
                   "openai_api"],
    "custom_voice": ["custom_code_self_hosted", "openai_api"],
}

# Registry provider -> evidence implementation. Documented mapping decisions,
# not inheritance: the registry's `llm_api` is named "LLM API (GPT-class)" and
# this project's client is OpenAI, so it resolves to openai_api. It does NOT
# resolve to anthropic_api or any Google product.
PROVIDER_TO_EVIDENCE: dict[str, Optional[str]] = {
    "llm_api": "openai_api",
    "openai_realtime": "openai_api",
    "rag_retrieval": "rag_vector_database_infrastructure",
    "cartesia": None,          # no evidence on file
}


def known_standards() -> list[str]:
    """Every standard the evidence registry actually carries a record for."""
    return sorted({normalise_standard(a.standard) for a in load_attestations()})


def supported_standards(registry_implementation_id: str) -> list[str]:
    """Standards this implementation satisfies from its own evidence.

    The authority for compliance is this registry, never the descriptive
    `Compatibility.compliance` claims in solution/patterns.py — those were
    reset to UNKNOWN across the board and understate implementations that do
    hold attestations.
    """
    return [s for s in known_standards()
            if evaluate_implementation(registry_implementation_id, s).satisfies]


def unmapped_evidence_implementations() -> list[str]:
    """Evidence implementations with no counterpart in the Solution Registry.

    Reported rather than quietly ignored — and deliberately NOT turned into new
    registry entries (section 1).
    """
    mapped = {c for comps in REGISTRY_TO_EVIDENCE.values() for c in comps}
    present = {a.implementation_id for a in load_attestations()}
    return sorted(present - mapped)


def evaluate_implementation(
    registry_implementation_id: str, standard: str,
    extra_components: Optional[list[str]] = None,
) -> ComplianceVerdict:
    """Verdict for one registry implementation against one requirement.

    Composition rule: every component must be SUPPORTED for the implementation
    to be SUPPORTED. Any explicit exclusion makes it incompatible. Anything
    else is UNKNOWN — and UNKNOWN never satisfies a hard requirement.
    """
    components = list(REGISTRY_TO_EVIDENCE.get(registry_implementation_id, []))
    for extra in (extra_components or []):
        if extra not in components:
            components.append(extra)

    if not components:
        return ComplianceVerdict(
            implementation_id=registry_implementation_id, standard=standard,
            status=ClaimStatus.UNKNOWN,
            reason=(f"no evidence mapping for registry implementation "
                    f"{registry_implementation_id!r}; cannot establish compliance"))

    verdicts = [evaluate_component(c, standard) for c in components]

    excluded = [v for v in verdicts if v.status == ClaimStatus.NOT_APPLICABLE]
    if excluded:
        return ComplianceVerdict(
            implementation_id=registry_implementation_id, standard=standard,
            status=ClaimStatus.NOT_APPLICABLE, components=verdicts,
            reason=(f"{standard}: excluded by {excluded[0].component_id} — "
                    f"{excluded[0].reason}"))

    unknown = [v for v in verdicts if v.status == ClaimStatus.UNKNOWN]
    if unknown:
        return ComplianceVerdict(
            implementation_id=registry_implementation_id, standard=standard,
            status=ClaimStatus.UNKNOWN, components=verdicts,
            reason=(f"{standard}: cannot be established — "
                    + "; ".join(f"{v.component_id}: {v.reason[:110]}" for v in unknown)))

    return ComplianceVerdict(
        implementation_id=registry_implementation_id, standard=standard,
        status=ClaimStatus.SUPPORTED, components=verdicts,
        reason=(f"{standard}: every component has supporting evidence — "
                + "; ".join(f"{v.component_id} [{','.join(v.evidence_ids)}]"
                            for v in verdicts)
                + ". NOTE: vendor evidence supports the customer's own compliance "
                  "work; it does not make the deployed architecture compliant."))


def evaluate_requirements(
    registry_implementation_id: str, standards: list[str],
) -> dict[str, ComplianceVerdict]:
    return {s: evaluate_implementation(registry_implementation_id, s) for s in standards}
