"""Hierarchical Solution Registry (the constraint space).

Capabilities -> Solution Patterns -> Implementation Options -> Providers.
Each level carries explicit Compatibility metadata used by filter/rank.
"""

from __future__ import annotations

from solution.schema import (
    Capability,
    ComplianceClaim,
    ComplianceStatus,
    ImplementationKind,
    Compatibility,
    ImplementationOption,
    SolutionPattern,
    TechnologyProvider,
)
from schemas.assessment_state import EffortBand


# ---- Providers (leaves) ----
_PROVIDERS = {
    "llm_api": TechnologyProvider(
        id="llm_api", name="LLM API (GPT-class)", category="llm",
        compatibility=Compatibility(
            supported_capabilities=[Capability.CLASSIFY, Capability.EXTRACT,
                                    Capability.GENERATE, Capability.VALIDATE],
            supported_integrations=["http", "sdk"], scale="any",
            technical_complexity=EffortBand.MEDIUM,
            strengths=["flexible", "state-of-art accuracy"],
            limitations=["per-token cost", "latency", "needs prompt/data engineering"],
        ),
    ),
    "rag_retrieval": TechnologyProvider(
        id="rag_retrieval", name="Retrieval-Augmented Generation", category="llm",
        compatibility=Compatibility(
            supported_capabilities=[Capability.SEARCH_RETRIEVE, Capability.GENERATE],
            supported_integrations=["vector_db", "http"], scale="medium",
            technical_complexity=EffortBand.LARGE,
            strengths=["grounded answers on org data"],
            limitations=["needs indexed corpus", "chunking/hybrid-search tuning"],
        ),
    ),
    "cartesia": TechnologyProvider(
        id="cartesia", name="Cartesia (voice TTS/agent)", category="voice",
        compatibility=Compatibility(
            supported_capabilities=[Capability.GENERATE],
            supported_integrations=["http", "sdk"], scale="small", latency="low",
            technical_complexity=EffortBand.SMALL,
            strengths=["low-latency voice", "cheap"],
            limitations=["voice-only", "less general reasoning"],
        ),
    ),
    "openai_realtime": TechnologyProvider(
        id="openai_realtime", name="OpenAI Realtime (voice agent)", category="voice",
        compatibility=Compatibility(
            supported_capabilities=[Capability.GENERATE, Capability.CLASSIFY],
            supported_integrations=["ws"], scale="any", latency="low",
            technical_complexity=EffortBand.MEDIUM,
            strengths=["native speech-to-speech", "low latency"],
            limitations=["higher cost", "account access needed"],
        ),
    ),
}


# ---- Implementation options (middle) ----
_IMPLS = {
    # A single "custom code covers everything" entry made every pattern
    # qualify for every request, which is why filtering could not
    # discriminate. Custom builds are therefore declared PER ARCHITECTURE:
    # "custom code for a document pipeline" is not the same product as
    # "custom code for a voice agent".
    "custom_workflow": ImplementationOption(
        id="custom_workflow", name="Custom workflow service",
        kind=ImplementationKind.CUSTOM_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.CLASSIFY,
                                    Capability.GENERATE, Capability.ROUTE,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW,
                                    Capability.POST_PROCESS],
            supported_integrations=["any"], scale="any", deployment="hybrid",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction"), ComplianceClaim(standard="hipaa", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction"), ComplianceClaim(standard="sox", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction")],
            technical_complexity=EffortBand.LARGE,
            strengths=["full control", "deployable on-prem", "scales"],
            limitations=["highest build cost", "longest timeline"],
        ),
        providers=[_PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["retry middleware", "idempotency keys", "structured logging",
                         "health checks", "fallback handling", "audit trail"],
    ),
    "custom_rag": ImplementationOption(
        id="custom_rag", name="Custom retrieval assistant",
        kind=ImplementationKind.CUSTOM_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.SEARCH_RETRIEVE,
                                    Capability.GENERATE, Capability.CLASSIFY,
                                    Capability.ROUTE, Capability.HUMAN_ESCALATE],
            supported_integrations=["any", "vector_db"], scale="any",
            deployment="hybrid", compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction"), ComplianceClaim(standard="hipaa", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction"), ComplianceClaim(standard="sox", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction")],
            technical_complexity=EffortBand.LARGE,
            strengths=["grounded on private corpora", "deployable on-prem"],
            limitations=["retrieval infrastructure to build and maintain"],
        ),
        providers=[_PROVIDERS["rag_retrieval"], _PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["minimum retrieval-score threshold", "abstain path",
                         "citation of retrieved passage", "index freshness monitoring"],
    ),
    "custom_voice": ImplementationOption(
        id="custom_voice", name="Custom voice agent",
        kind=ImplementationKind.CUSTOM_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.GENERATE,
                                    Capability.CLASSIFY, Capability.ROUTE,
                                    Capability.HUMAN_ESCALATE],
            supported_integrations=["ws", "any"], scale="any", latency="low",
            deployment="cloud", compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="vendor attestation not obtained or verified")],
            technical_complexity=EffortBand.LARGE,
            strengths=["speech-to-speech", "low latency"],
            limitations=["latency budget is unforgiving", "harder to test"],
        ),
        providers=[_PROVIDERS["openai_realtime"], _PROVIDERS["cartesia"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["barge-in handling", "deterministic escalation phrase",
                         "call recording for audit", "latency monitoring"],
    ),
    "custom_docpipe": ImplementationOption(
        id="custom_docpipe", name="Custom document pipeline",
        kind=ImplementationKind.CUSTOM_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.EXTRACT,
                                    Capability.CLASSIFY, Capability.VALIDATE,
                                    Capability.HUMAN_REVIEW, Capability.POST_PROCESS],
            supported_integrations=["any"], scale="any", deployment="hybrid",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction"), ComplianceClaim(standard="hipaa", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction"), ComplianceClaim(standard="sox", status=ComplianceStatus.UNKNOWN, reason="no attestation on file; a custom build can be made compliant but is not compliant by construction")],
            technical_complexity=EffortBand.LARGE,
            strengths=["full control over extraction and validation rules"],
            limitations=["highest build cost", "model maintenance is ongoing"],
        ),
        providers=[_PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["schema validation", "per-field confidence threshold",
                         "low-confidence review queue", "source-system reconciliation"],
    ),
    "n8n": ImplementationOption(
        id="n8n", name="n8n (self-host low-code)", kind=ImplementationKind.LOW_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.ROUTE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["http", "smtp", "db"], scale="medium",
            deployment="hybrid", compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="self-hostable, but no data-protection attestation on file")],
            technical_complexity=EffortBand.SMALL,
            strengths=["fast to build", "lots of connectors", "self-hostable"],
            limitations=["hard at high scale", "complex branching awkward"],
        ),
        providers=[_PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["node retry policy", "error branch", "failure queue",
                         "execution monitoring"],
    ),
    "make": ImplementationOption(
        id="make", name="Make (cloud low-code)", kind=ImplementationKind.LOW_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.ROUTE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["http", "smtp", "db"], scale="medium",
            deployment="cloud", compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="vendor attestation not obtained or verified")],
            technical_complexity=EffortBand.SMALL,
            strengths=["fast to build", "cloud-managed"],
            limitations=["per-operation pricing", "less control", "cloud only"],
        ),
        providers=[_PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["scenario error handler", "auto-retry", "incomplete-execution queue"],
    ),
    "zapier": ImplementationOption(
        id="zapier", name="Zapier (SaaS low-code)", kind=ImplementationKind.LOW_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.ROUTE,
                                    Capability.HUMAN_ESCALATE],
            supported_integrations=["saas"], scale="small", deployment="cloud",
            compliance=[],
            technical_complexity=EffortBand.SMALL,
            strengths=["zero infra", "fast"],
            limitations=["small scale", "SaaS-only integrations",
                         "no compliance attestations modelled"],
        ),
        providers=[_PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["auto-replay", "error notification"],
    ),
    "managed_ai": ImplementationOption(
        id="managed_ai", name="Managed AI service", kind=ImplementationKind.MANAGED_SERVICE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.EXTRACT, Capability.CLASSIFY,
                                    Capability.VALIDATE,
                                    Capability.HUMAN_REVIEW, Capability.HUMAN_ESCALATE],
            supported_integrations=["api"], scale="large", deployment="cloud",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="vendor attestation not obtained or verified"), ComplianceClaim(standard="sox", status=ComplianceStatus.UNKNOWN, reason="vendor attestation not obtained or verified")],
            technical_complexity=EffortBand.SMALL,
            strengths=["low build effort", "scales"],
            limitations=["less custom", "vendor lock-in"],
        ),
        providers=[_PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["confidence threshold routing", "vendor SLA monitoring",
                         "human review queue for low-confidence output"],
    ),
    # ---- Deterministic rules builds -------------------------------------
    # These are the SAME platforms as the entries above with the model taken
    # out of the loop, and that is the whole point: they declare no GENERATE
    # and no EXTRACT, so they qualify only for workflows that genuinely do not
    # need a model. A triage flow that ingests, applies rules, routes and
    # escalates is one; invoice line-item extraction is not.
    #
    # Their compliance composition drops `openai_api` for the same reason —
    # there is no model API in the stack to attest for (lib/compliance.py).
    "n8n_rules": ImplementationOption(
        id="n8n_rules", name="n8n rules workflow (self-host, no model)",
        kind=ImplementationKind.LOW_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.CLASSIFY,
                                    Capability.ROUTE, Capability.VALIDATE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["http", "smtp", "db"], scale="medium",
            deployment="hybrid",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN,
                                        reason="self-hostable, but no data-protection "
                                               "attestation on file")],
            technical_complexity=EffortBand.SMALL,
            strengths=["no model in the loop: output is reproducible and auditable",
                       "no per-token inference cost",
                       "fast to build", "self-hostable"],
            limitations=["cannot read unstructured input or generate language",
                         "every case needs a rule written for it in advance",
                         "rule sets grow brittle as exceptions accumulate",
                         "hard at high scale"],
        ),
        providers=[],
        version=1, last_reviewed="2026-08-19",
        control_catalog=["explicit default branch for unmatched cases",
                         "rule-coverage monitoring", "versioned rule set under review",
                         "replay queue for cases no rule matched"],
    ),
    "make_rules": ImplementationOption(
        id="make_rules", name="Make rules workflow (cloud, no model)",
        kind=ImplementationKind.LOW_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.CLASSIFY,
                                    Capability.ROUTE, Capability.VALIDATE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["http", "smtp", "db"], scale="medium",
            deployment="cloud",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN,
                                        reason="vendor attestation held in the evidence "
                                               "registry; inline claims are descriptive "
                                               "only")],
            technical_complexity=EffortBand.SMALL,
            strengths=["no model in the loop: output is reproducible and auditable",
                       "no per-token inference cost",
                       "cloud-managed, no infrastructure to run"],
            limitations=["cannot read unstructured input or generate language",
                         "every case needs a rule written for it in advance",
                         "per-operation pricing", "cloud only"],
        ),
        providers=[],
        version=1, last_reviewed="2026-08-19",
        control_catalog=["scenario error handler", "explicit default route",
                         "rule-coverage monitoring", "incomplete-execution queue"],
    ),
    "custom_rules": ImplementationOption(
        id="custom_rules", name="Custom rules engine",
        kind=ImplementationKind.CUSTOM_CODE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.CLASSIFY,
                                    Capability.ROUTE, Capability.VALIDATE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["any"], scale="any", deployment="hybrid",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN,
                                        reason="no attestation on file; a custom build "
                                               "can be made compliant but is not "
                                               "compliant by construction")],
            # Deliberately MEDIUM, not LARGE: a rules service is a real build,
            # but there is no model to select, prompt, evaluate or maintain.
            technical_complexity=EffortBand.MEDIUM,
            strengths=["full control over the rule set",
                       "no model in the loop: output is reproducible and auditable",
                       "deployable on-prem", "scales"],
            limitations=["cannot read unstructured input or generate language",
                         "every rule is code to write, test and maintain",
                         "no capability at all for cases outside the rule set"],
        ),
        providers=[],
        version=1, last_reviewed="2026-08-19",
        control_catalog=["explicit default branch for unmatched cases",
                         "rule-coverage monitoring", "rule-change review and audit trail",
                         "replay queue for cases no rule matched"],
    ),
    # Registry/reference coverage (spec section 13): a knowledge-assistant
    # implementation that genuinely covers intake, retrieval, generation and
    # escalation, so acceptance case B can be exercised rather than failing
    # for want of a registry entry.
    "rag_managed": ImplementationOption(
        id="rag_managed", name="Managed retrieval assistant",
        kind=ImplementationKind.MANAGED_SERVICE,
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.SEARCH_RETRIEVE,
                                    Capability.GENERATE, Capability.ROUTE,
                                    Capability.HUMAN_ESCALATE, Capability.CLASSIFY],
            supported_integrations=["api", "http"], scale="any", deployment="cloud",
            compliance=[ComplianceClaim(standard="gdpr", status=ComplianceStatus.UNKNOWN, reason="vendor attestation not obtained or verified")], technical_complexity=EffortBand.MEDIUM,
            strengths=["grounded answers without building retrieval infrastructure"],
            limitations=["corpus must be prepared and kept fresh", "cloud only"],
        ),
        providers=[_PROVIDERS["rag_retrieval"], _PROVIDERS["llm_api"]],
        version=1, last_reviewed="2026-08-18",
        control_catalog=["minimum retrieval-score threshold", "abstain path",
                         "citation of retrieved passage", "escalation on low grounding"],
    ),
}


# ---- Solution patterns (top) ----
_PATTERNS = {
    "ai_assisted_workflow": SolutionPattern(
        id="ai_assisted_workflow", name="AI-assisted workflow",
        architecture="LLM orchestrates steps; human-in-the-loop for escalation/review",
        implementations=[_IMPLS["n8n"], _IMPLS["make"], _IMPLS["zapier"],
                         _IMPLS["custom_workflow"]],
    ),
    "rag_knowledge_assistant": SolutionPattern(
        id="rag_knowledge_assistant", name="RAG knowledge assistant",
        architecture="Retrieve relevant org knowledge, generate grounded answers",
        implementations=[_IMPLS["rag_managed"], _IMPLS["custom_rag"]],
    ),
    "voice_agent": SolutionPattern(
        id="voice_agent", name="Voice agent",
        architecture="Speech-to-speech assistant with deterministic escalation",
        implementations=[_IMPLS["custom_voice"]],
    ),
    # Spec 11.2: a deterministic automation alternative, so DeployIQ can say
    # "you may not need a model for this" when the workflow does not require
    # one. It is not a reference baseline for either sector, so it competes as
    # an ordinary candidate and cannot displace the curated architecture.
    "rules_based_workflow": SolutionPattern(
        id="rules_based_workflow", name="Deterministic rules workflow",
        architecture=("Ingest -> deterministic rule evaluation -> validate/route "
                      "-> human handling for anything no rule matches"),
        implementations=[_IMPLS["n8n_rules"], _IMPLS["make_rules"],
                         _IMPLS["custom_rules"]],
    ),
    "document_pipeline": SolutionPattern(
        id="document_pipeline", name="Document processing pipeline",
        architecture="Ingest -> classify/extract -> validate -> post-process",
        implementations=[_IMPLS["managed_ai"], _IMPLS["custom_docpipe"],
                         _IMPLS["n8n"], _IMPLS["make"]],
    ),
}


def all_patterns() -> list[SolutionPattern]:
    return list(_PATTERNS.values())


def pattern(id: str) -> SolutionPattern:
    return _PATTERNS[id]


def implementation_covers(impl: ImplementationOption, caps: set[Capability]) -> bool:
    """Does THIS implementation (with its providers) cover every capability?"""
    covered = set(impl.compatibility.supported_capabilities)
    for prov in impl.providers:
        covered |= set(prov.compatibility.supported_capabilities)
    return caps <= covered


def patterns_covering(caps: set[Capability]) -> list[SolutionPattern]:
    """Patterns where at least ONE implementation covers every capability.

    Registry hardening: coverage was previously the union across all of a
    pattern's implementations, so a pattern qualified on the strength of a
    sibling it would never actually use — and since custom_code supports every
    capability, nearly every pattern qualified for nearly every request.
    """
    return [p for p in _PATTERNS.values()
            if any(implementation_covers(i, caps) for i in p.implementations)]


def _legacy_patterns_covering(caps: set[Capability]) -> list[SolutionPattern]:
    out = []
    for p in _PATTERNS.values():
        covered: set[Capability] = set()
        for impl in p.implementations:
            covered |= set(impl.compatibility.supported_capabilities)
            for prov in impl.providers:
                covered |= set(prov.compatibility.supported_capabilities)
        if caps <= covered:
            out.append(p)
    return out
