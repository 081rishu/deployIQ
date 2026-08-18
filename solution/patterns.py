"""Hierarchical Solution Registry (the constraint space).

Capabilities -> Solution Patterns -> Implementation Options -> Providers.
Each level carries explicit Compatibility metadata used by filter/rank.
"""

from __future__ import annotations

from solution.schema import (
    Capability,
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
    "custom_code": ImplementationOption(
        id="custom_code", name="Custom code service", kind="custom",
        compatibility=Compatibility(
            supported_capabilities=[c for c in Capability],
            supported_integrations=["any"], scale="any",
            technical_complexity=EffortBand.LARGE,
            strengths=["full control", "any capability"],
            limitations=["highest build cost", "longest timeline"],
        ),
        providers=[_PROVIDERS["llm_api"], _PROVIDERS["rag_retrieval"],
                   _PROVIDERS["openai_realtime"]],
    ),
    "n8n": ImplementationOption(
        id="n8n", name="n8n (self-host low-code)", kind="low_code",
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.ROUTE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["http", "smtp", "db"], scale="medium",
            technical_complexity=EffortBand.SMALL,
            strengths=["fast to build", "lots of connectors"],
            limitations=["hard at high scale", "complex branching awkward"],
        ),
        providers=[_PROVIDERS["llm_api"]],
    ),
    "make": ImplementationOption(
        id="make", name="Make (cloud low-code)", kind="low_code",
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.ROUTE,
                                    Capability.POST_PROCESS,
                                    Capability.HUMAN_ESCALATE, Capability.HUMAN_REVIEW],
            supported_integrations=["http", "smtp", "db"], scale="medium",
            technical_complexity=EffortBand.SMALL,
            strengths=["fast to build", "cloud-managed"],
            limitations=["per-operation pricing", "less control"],
        ),
        providers=[_PROVIDERS["llm_api"]],
    ),
    "zapier": ImplementationOption(
        id="zapier", name="Zapier (SaaS low-code)", kind="low_code",
        compatibility=Compatibility(
            supported_capabilities=[Capability.INGEST, Capability.ROUTE,
                                    Capability.HUMAN_ESCALATE],
            supported_integrations=["saas"], scale="small",
            technical_complexity=EffortBand.SMALL,
            strengths=["zero infra", "fast"],
            limitations=["small scale", "SaaS-only integrations"],
        ),
        providers=[_PROVIDERS["llm_api"]],
    ),
    "managed_ai": ImplementationOption(
        id="managed_ai", name="Managed AI service", kind="managed_service",
        compatibility=Compatibility(
            supported_capabilities=[Capability.EXTRACT, Capability.CLASSIFY,
                                    Capability.VALIDATE,
                                    Capability.HUMAN_REVIEW, Capability.HUMAN_ESCALATE],
            supported_integrations=["api"], scale="large",
            technical_complexity=EffortBand.SMALL,
            strengths=["low build effort", "scales"],
            limitations=["less custom", "vendor lock-in"],
        ),
        providers=[_PROVIDERS["llm_api"]],
    ),
}


# ---- Solution patterns (top) ----
_PATTERNS = {
    "ai_assisted_workflow": SolutionPattern(
        id="ai_assisted_workflow", name="AI-assisted workflow",
        architecture="LLM orchestrates steps; human-in-the-loop for escalation/review",
        implementations=[_IMPLS["n8n"], _IMPLS["make"], _IMPLS["zapier"],
                         _IMPLS["custom_code"]],
    ),
    "rag_knowledge_assistant": SolutionPattern(
        id="rag_knowledge_assistant", name="RAG knowledge assistant",
        architecture="Retrieve relevant org knowledge, generate grounded answers",
        implementations=[_IMPLS["custom_code"], _IMPLS["managed_ai"]],
    ),
    "voice_agent": SolutionPattern(
        id="voice_agent", name="Voice agent",
        architecture="Speech-to-speech assistant with deterministic escalation",
        implementations=[_IMPLS["custom_code"], _IMPLS["managed_ai"]],
    ),
    "document_pipeline": SolutionPattern(
        id="document_pipeline", name="Document processing pipeline",
        architecture="Ingest -> classify/extract -> validate -> post-process",
        implementations=[_IMPLS["custom_code"], _IMPLS["managed_ai"],
                         _IMPLS["n8n"], _IMPLS["make"]],
    ),
}


def all_patterns() -> list[SolutionPattern]:
    return list(_PATTERNS.values())


def pattern(id: str) -> SolutionPattern:
    return _PATTERNS[id]


def patterns_covering(caps: set[Capability]) -> list[SolutionPattern]:
    """Deterministically return patterns whose implementations can cover the
    required capabilities (via provider + implementation compatibility)."""
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
