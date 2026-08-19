"""deployIQ's OWN vendor attestations — a separate question.

This register answers "is the tool itself built on compliant services?".

It is NOT the evidence the Solution Estimator filters on. That lives in
`lib/compliance.py` (data/compliance_attestations.json) and describes the
platforms a CUSTOMER would build on. Conflating the two would let deployIQ's
own vendor certifications silently qualify an unrelated implementation for a
customer's HIPAA requirement.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

_PATH = Path(__file__).resolve().parent.parent / "data" / "compliance_evidence.json"


class Attestation(BaseModel):
    evidence_id: str
    vendor: str
    used_for: str = ""
    in_current_stack: bool = False
    certifications: list[str] = Field(default_factory=list)
    evidence_type: Literal["vendor_published_attestation",
                           "independent_audit_report"] = "vendor_published_attestation"
    source: str
    source_url: str = ""
    trust_portal: str = ""
    retrieved: str = ""
    verification: Literal["primary_document", "search_snippet", "unverified"]
    notes: str = ""
    limitations: str = ""
    binds_implementations: list[str] = Field(default_factory=list)

    def covers(self, standard: str) -> bool:
        s = standard.strip().lower()
        return any(s in c.lower() for c in self.certifications)

    @property
    def is_independent(self) -> bool:
        return self.evidence_type == "independent_audit_report"


class VendorRegistry(BaseModel):
    version: int
    description: str = ""
    product_vendor_attestations: list[Attestation] = Field(default_factory=list)
    registry_implementation_attestations: list[Attestation] = Field(default_factory=list)
    registry_gap_note: str = ""


@lru_cache(maxsize=1)
def load_registry() -> VendorRegistry:
    return VendorRegistry.model_validate(json.loads(_PATH.read_text(encoding="utf-8")))


def attestation(evidence_id: str) -> Optional[Attestation]:
    reg = load_registry()
    for a in reg.registry_implementation_attestations + reg.product_vendor_attestations:
        if a.evidence_id == evidence_id:
            return a
    return None


def backs_implementation(evidence_id: str, implementation_id: str,
                         standard: str) -> tuple[bool, str]:
    """A product-vendor attestation may back a registry claim only if it is
    explicitly bound to that implementation and covers that standard."""
    att = attestation(evidence_id)
    if att is None:
        return False, f"evidence_id {evidence_id!r} is not in the registry"
    if implementation_id not in att.binds_implementations:
        return False, (
            f"attestation {evidence_id!r} ({att.vendor}) is not bound to "
            f"implementation {implementation_id!r}. A vendor attestation only "
            f"backs a claim for an implementation that actually uses that vendor.")
    if not att.covers(standard):
        return False, (f"attestation {evidence_id!r} does not cover {standard!r}")
    return True, (f"{standard} backed by {att.vendor} "
                  f"{att.evidence_type.replace('_', ' ')} [{evidence_id}]")


def product_stack_status() -> list[dict]:
    return [{"vendor": a.vendor, "in_current_stack": a.in_current_stack,
             "certifications": a.certifications, "evidence_type": a.evidence_type,
             "verification": a.verification, "source_url": a.source_url}
            for a in load_registry().product_vendor_attestations]
