"""Voice interviewer session — Route B orchestration.

Binds the three legs into a live, turn-based conversation:

    user speech -> STT (llm/stt.py) -> interviewer engine (run_turn)
                -> question text -> TTS (llm/tts.py) -> spoken audio out

The interviewer engine remains the deterministic brain (4-state adaptive).
This module only wires audio in/out around it. It is stateless with respect
to the engine: the AssessmentState lives in the caller/session and is passed
in each turn.
"""

from __future__ import annotations

from typing import Optional

from interviewer.conversation import ConversationContext
from interviewer.engine import TurnResult, run_turn
from lib.logging_config import get_logger
from llm.stt import transcribe_audio
from llm.tts import synthesize
from schemas.assessment_state import AssessmentState

log = get_logger("interviewer.voice")

# Domain vocabulary handed to the transcription model as a decoding hint.
#
# These are terms a general speech model reliably gets wrong — "FCR" becomes
# "F-C-R", "straight-through processing" becomes "straight through
# processing", "n8n" becomes almost anything. Naming them raises the odds the
# transcript says what the user actually said.
#
# Deliberately contains no numbers, no expected answers and no phrasing the
# user might not have used: a transcription prompt biases output, so anything
# beyond terminology risks inventing assessment input. See llm/stt.py.
_COMMON_VOCABULARY = (
    "workflow, process, headcount, monthly volume, handling time, automation, "
    "human in the loop, escalation, integration, compliance, GDPR, HIPAA, "
    "SOC 2, on-premise, accuracy, throughput, backlog"
)
_SECTOR_VOCABULARY = {
    "customer_support": (
        "customer support, support agent, ticket, tickets, first-contact "
        "resolution, FCR, escalation rate, rework rate, Zendesk, helpdesk"
    ),
    "document_processing": (
        "document processing, invoice, invoices, accounts payable, AP clerk, "
        "straight-through processing, STP rate, exception rate, first-pass "
        "yield, three-way match, purchase order, OCR, extraction"
    ),
}


def transcription_vocabulary(sector) -> str:
    """The decoding hint for one sector. Terminology only."""
    key = getattr(sector, "value", str(sector or ""))
    sector_terms = _SECTOR_VOCABULARY.get(key, "")
    return ", ".join(t for t in (sector_terms, _COMMON_VOCABULARY) if t)


def _detect_filename(audio: bytes) -> str:
    """Guess the audio container from magic bytes for OpenAI transcription.

    The WebSocket path carries no filename, so infer it so STT is not rejected
    as "corrupted or unsupported".
    """
    if audio[:3] == b"ID3" or (audio[:2] == b"\xff\xfb"):
        return "audio.mp3"
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return "audio.wav"
    if audio[:4] == b"OggS":
        return "audio.ogg"
    if audio[:4] == b"\x1aE\xdf\xa3":
        return "audio.webm"
    return "audio.mp3"


class VoiceSession:
    """A live voice-interviewer session.

    Usage:
        session = VoiceSession()
        session.start(sector, problem)   # speaks the first question
        answer_bytes = <captured user audio>
        result = session.respond(audio_bytes)  # transcribe -> think -> speak
    """

    def __init__(self, warm_up: bool = True) -> None:
        self.state: Optional[AssessmentState] = None
        # Transport metadata only: the API returns the STT text with its
        # matching websocket turn; it never participates in interview logic.
        self.last_transcript: Optional[str] = None
        # Conversation context lives beside the state for the life of the
        # socket. Voice implements no assessment logic of its own — it holds
        # the same two objects the text path ships back and forth.
        self.context: Optional[ConversationContext] = (
            ConversationContext() if warm_up else None)

    def start(self, sector, problem: str, transcriptions_language: str = "en") -> TurnResult:
        from schemas.assessment_state import AssessmentState as AS

        self.state = AS(sector=sector, problem=problem)
        log.info("session_start sector=%s problem_chars=%d",
                 sector.value if hasattr(sector, "value") else sector, len(problem))
        result = run_turn(self.state, problem, self.context)
        self.context = result.context
        return result

    def resume(self, state: AssessmentState,
               context: Optional[ConversationContext]) -> None:
        """Adopt an interview already begun over REST.

        The interview is started once, by /api/interview/start. A websocket
        that called start() again would run a second, independent first turn —
        a second LLM call, a second AssessmentState, and a duplicate greeting
        for the user. Voice is a transport for turns over the client's state,
        exactly as the REST path is; it owns no interview of its own.

        No turn is run and no LLM is called here: the client already holds the
        first question.
        """
        self.state = state
        self.context = context
        log.info("session_resume turn_count=%d status=%s",
                 state.turn_count, state.status.value)

    def respond(self, audio: bytes, *, language: str = "en", filename: str | None = None) -> TurnResult:
        """Take the user's spoken answer, transcribe it, run a turn, and
        (for the caller) render the reply to audio via TTS.

        If `filename` is omitted, the container is inferred from the bytes.
        """
        if self.state is None:
            raise RuntimeError("VoiceSession.start() must be called first")

        name = filename or _detect_filename(audio)
        log.info("respond audio_bytes=%d filename=%s", len(audio) if isinstance(audio, (bytes, bytearray)) else -1, name)
        # No vocabulary prompt: a live run showed the model returning the
        # prompt itself as the transcript when it could not make out the
        # audio, which put the glossary into the interview as the user's own
        # words. See llm/stt.py. The vocabulary below is kept for reference
        # and for any caller willing to accept that risk explicitly.
        transcript = transcribe_audio(audio, language=language, filename=name)
        self.last_transcript = transcript
        log.info("transcript_received chars=%d", len(transcript))
        result = run_turn(self.state, transcript, self.context)
        self.context = result.context
        return result

    def speech_for(self, result: TurnResult) -> bytes:
        """Synthesize the interviewer's reply text into audio."""
        text = self._reply_text(result)
        log.info("speech_generated chars=%d", len(text))
        return synthesize(text)

    @staticmethod
    def _reply_text(result: TurnResult) -> str:
        if result.question:
            ack = (result.acknowledgment + " ") if result.acknowledgment else ""
            return (ack + result.question).strip()
        if result.stop_reason:
            return result.stop_reason
        return ""


def wav_bytes_to_mp3_placeholder(_data: bytes) -> bytes:
    """Placeholder for audio format conversion if needed.

    OpenAIs transcription accepts webm/mp3/wav directly, so most callers pass
    the raw buffer straight through. Kept separate so format bridging (e.g.
    browser PCM -> compatible container) has one obvious home.
    """
    return _data
