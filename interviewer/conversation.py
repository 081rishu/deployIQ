"""Conversation context and phases — fix spec sections 2 and 3.

The interviewer should not read like a form from turn one. It opens naturally,
learns who it is talking to, and moves into discovery once there is something
to discover.

CRITICAL BOUNDARY: conversational metadata lives HERE, never in
`AssessmentState`. A name or a pleasant exchange must not be able to reach an
economic calculation or a score. The analytical state stays exactly as
auditable as it was.

Phases are bookkeeping for tone and transition. They never decide WHICH field
is asked next — that stays with the deterministic need selector.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Phase(str, Enum):
    WARMUP = "warmup"              # greeting, name, what are you working on
    DISCOVERY = "discovery"        # deterministic need selection drives it
    CLARIFICATION = "clarification"
    DONE = "done"


# The warm-up is deliberately short. Two turns of rapport, then work.
MAX_WARMUP_TURNS = 2


class ConversationContext(BaseModel):
    """Everything conversational, and nothing analytical.

    Shipped back and forth with the AssessmentState each turn, exactly like the
    state itself — the interviewer stays a stateless turn handler.
    """
    name: Optional[str] = None
    phase: Phase = Phase.WARMUP
    warmup_turns: int = 0
    warmup_completed: bool = False
    # Short rolling context for tone only. Never parsed for facts.
    recent_turns: list[str] = Field(default_factory=list)

    def note_turn(self, message: str) -> None:
        self.recent_turns.append(message[:200])
        self.recent_turns = self.recent_turns[-4:]

    def should_warm_up(self, facts_known: int) -> bool:
        """Stay in warm-up only while it is still earning its place.

        If the opening answer already carried real assessment facts, the
        warm-up has done its job and discovery starts immediately — a user who
        leads with "we're a BPO in India handling 5,000 tickets" should not
        then be asked how their day is going.
        """
        if self.warmup_completed:
            return False
        if facts_known >= 2:
            return False
        return self.warmup_turns < MAX_WARMUP_TURNS

    def complete_warmup(self) -> None:
        self.warmup_completed = True
        self.phase = Phase.DISCOVERY


def opening_prompt() -> str:
    """The very first thing the interviewer says."""
    return (
        "You are opening a consultative conversation about a business process "
        "someone is considering automating with AI.\n\n"
        "Introduce yourself in ONE short sentence — you are here to understand "
        "their process and what an AI implementation could realistically look "
        "like — and ask their name.\n\n"
        "Warm and direct. No bullet points, no list of what you will cover, no "
        "promise of a report. Return ONLY JSON with keys 'acknowledgment' "
        "(may be empty) and 'question'."
    )


def warmup_prompt(context: ConversationContext, known_summary: str) -> str:
    """Second warm-up turn: acknowledge the person, then turn to the work."""
    who = f"The user's name is {context.name}. " if context.name else ""
    return (
        "You are in the warm-up of a consultative interview about automating a "
        f"business process. {who}\n\n"
        "Acknowledge them briefly and naturally — one clause, not a paragraph — "
        "then ask what they are working on, or what process they are thinking "
        "about automating.\n\n"
        "A short pleasantry is fine. Do NOT start a scripted small-talk "
        "sequence, and do NOT ask more than one question.\n\n"
        f"Already known (do not ask again): {known_summary or 'nothing yet'}\n\n"
        "Return ONLY JSON with keys 'acknowledgment' and 'question'."
    )


def transition_hint(context: ConversationContext, field_label: str) -> str:
    """Tone guidance for the first turn after warm-up."""
    who = f"{context.name} " if context.name else ""
    return (
        f"This is the first question after the warm-up. Briefly reflect back "
        f"what {who}just told you about their process in a short clause, then "
        f"ask about {field_label}. The transition should feel like a "
        f"conversation turning to detail, not a form starting."
    )
