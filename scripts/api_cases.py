"""P7 acceptance tests — API integration over the canonical assessment pipeline."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

# Imports resolve from the editable src-layout installation.
if "openai" not in sys.modules:
    _s = types.ModuleType("openai")
    _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from fastapi.testclient import TestClient

import core
import api.interview as interview_api
import api.main as api_main
import api.voice as voice_api
from calc.ai_state import LaborRealization
from core.config import Settings
from core.costs import record_usage, tracker
from core.observability import run_stage
from core.request_context import get_request_id, reset_request_id, set_request_id
from interviewer.conversation import ConversationContext
from interviewer.engine import TurnResult
import interviewer.voice as voice_session_mod
from pipeline import orchestrate as orch
from report import narrate as narrate_mod
from report.schema import LaborRealizationSource, ReportMode
from schemas.assessment_state import AssessmentState, InterviewStatus, RiskInputs, Sector
from scripts.report_cases import rng, solution, state

failures: list[str] = []
checks = 0


def check(case: str, cond: bool, desc: str) -> None:
    global checks
    checks += 1
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


DOC_CAPS = ["ingest", "extract", "classify", "validate", "human_review"]
DOC_TASKS = [
    {"task": "ingest invoices", "capability": "ingest",
     "automation_min": 90, "automation_max": 98,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "high", "hitl": "autonomous", "rationale": "scriptable intake"},
    {"task": "extract line items", "capability": "extract",
     "automation_min": 30, "automation_max": 45,
     "handling_time_min_minutes": 4, "handling_time_max_minutes": 4,
     "confidence": "medium", "hitl": "human_review", "rationale": "semi-structured"},
    {"task": "validate against PO", "capability": "validate",
     "automation_min": 40, "automation_max": 60,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "low", "hitl": "human_review", "rationale": "three-way match"},
]


def install_llm_stub() -> None:
    def fake(system, user, **kw):
        s = str(system).lower()
        if "decompose" in s:
            return {"capabilities": DOC_CAPS}
        if "estimate automation per workflow task" in s:
            return {"tasks": DOC_TASKS}
        if "explaining pre-selected alternative approaches" in s:
            return {"explanations": []}
        return {}

    import llm.openai_client as oc
    from interviewer import engine as interviewer_engine
    from solution import capabilities as caps_mod

    oc.complete_json = fake
    interviewer_engine.complete_json = fake
    caps_mod.complete_json = fake


def base_payload(**state_kw) -> dict:
    return {
        "state": state(**state_kw).model_dump(mode="json"),
        "labor_realization": LaborRealization.COST_ELIMINATED.value,
        "labor_realization_source": LaborRealizationSource.USER.value,
        "enable_narration": False,
        "report_format": "both",
    }


def post_run(client: TestClient, payload: dict) -> dict:
    res = client.post("/api/assessment/run", json=payload)
    check("http", res.status_code == 200, "assessment run request succeeds")
    return res.json()


def case_A_B_C(client: TestClient) -> None:
    print("\nA/B/C — app imports, valid request reaches pipeline, malformed request")
    check("A", hasattr(api_main, "app"), "FastAPI app imports")
    health = client.get("/health")
    check("A", health.status_code == 200 and health.json() == {"status": "ok"}
          and bool(health.headers.get("X-Request-ID")),
          "health endpoint is ready without an OpenAI call")

    captured = {"called": 0, "state_type": None}
    orig = api_main.orchestrate.run_assessment
    try:
        def fake_run(s: AssessmentState, **kw):
            captured["called"] += 1
            captured["state_type"] = type(s)
            return SimpleNamespace(
                final_report=SimpleNamespace(mode=ReportMode.PARTIAL),
                used_narration=False,
                narration_issues=["stub"],
                bundle=SimpleNamespace(
                    labor_realization=None,
                    labor_realization_source=LaborRealizationSource.UNSET,
                    economic_error=["stub"],
                ),
                rendered=SimpleNamespace(json_doc={"mode": "partial"}, markdown="# stub\n"),
            )

        api_main.orchestrate.run_assessment = fake_run
        payload = {
            "state": state().model_dump(mode="json"),
            "report_format": "json",
        }
        res = client.post("/api/assessment/run", json=payload)
        body = res.json()
        check("B", res.status_code == 200, "valid request is accepted")
        check("B", captured["called"] == 1 and captured["state_type"] is AssessmentState,
              "endpoint delegates to pipeline.run_assessment exactly once")
        check("B", body["mode"] == "partial", "pipeline result is surfaced by API")
    finally:
        api_main.orchestrate.run_assessment = orig

    bad = client.post("/api/assessment/run", json={"state": {"sector": "invalid"}})
    check("C", bad.status_code == 422, "malformed request rejected at API boundary")
    check("C", bool(bad.headers.get("X-Request-ID")),
          "API responses carry a correlation id")

    orig = api_main.orchestrate.run_assessment
    try:
        def crash(*_args, **_kw):
            raise RuntimeError("internal details must not reach clients")

        api_main.orchestrate.run_assessment = crash
        failed = client.post("/api/assessment/run", json={
            "state": state().model_dump(mode="json"),
        })
        check("C", failed.status_code == 500, "unexpected server failure is HTTP 500")
        check("C", failed.json().get("detail") == "Internal server error"
              and "internal details" not in failed.text,
              "unexpected failure is sanitized for clients")
    finally:
        api_main.orchestrate.run_assessment = orig


def case_D_E_F_G_H_I_J(client: TestClient) -> None:
    print("\nD/E/F/G/H/I/J — FULL/PARTIAL/REFUSED + formats + narration fallback + determinism")
    install_llm_stub()

    orig_est = orch.estimator.estimate
    try:
        orch.estimator.estimate = lambda _s: solution()

        full = post_run(client, base_payload())
        check("D", full["mode"] == "full", "FULL response returned")

        p_json = base_payload(); p_json["report_format"] = "json"
        only_json = post_run(client, p_json)
        check("G", only_json["report_json"] is not None and only_json["report_markdown"] is None,
              "JSON report format supported")

        p_md = base_payload(); p_md["report_format"] = "markdown"
        only_md = post_run(client, p_md)
        check("H", isinstance(only_md["report_markdown"], str) and only_md["report_json"] is None,
              "Markdown report format supported")

        orig_narrate = orch.narrate_mod.narrate
        try:
            def fail_closed(report, bundle=None, **kw):
                return narrate_mod.NarrationResult(report, used_narration=False,
                                                   issues=["llm unavailable or malformed: test"])

            orch.narrate_mod.narrate = fail_closed
            narrated_payload = base_payload()
            narrated_payload["enable_narration"] = True
            narrated = post_run(client, narrated_payload)
            baseline = post_run(client, base_payload())
            check("I", not narrated["used_narration"], "narration unavailable uses deterministic fallback")
            check("I", narrated["report_json"] == baseline["report_json"],
                  "fallback preserves deterministic report JSON")
        finally:
            orch.narrate_mod.narrate = orig_narrate

        d1 = post_run(client, base_payload())
        d2 = post_run(client, base_payload())
        check("J", d1 == d2, "repeated deterministic request is identical")

        partial_payload = {
            "state": state().model_dump(mode="json"),
            "labor_realization": None,
            "labor_realization_source": LaborRealizationSource.UNSET.value,
            "report_format": "both",
        }
        partial = post_run(client, partial_payload)
        check("E", partial["mode"] == "partial", "missing labor realization yields PARTIAL")
        check("R", partial["labor_realization"] is None
              and partial["labor_realization_source"] == "unset",
              "no LaborRealization default is invented")

        orch.estimator.estimate = lambda _s: solution(recommended_pattern="", overall_automation=rng(0, 0))
        refused = post_run(client, base_payload())
        check("F", refused["mode"] == "refused", "refused estimator state returns REFUSED")

        sections = refused["report_json"].get("sections", [])
        figure_keys = [f.get("key", "") for s in sections for f in s.get("figures", [])]
        blocked = ("solution.", "ai_operating.", "impl.", "benefits.", "scores.")
        check("K/L/M/N/O", all(not k.startswith(blocked) for k in figure_keys),
              "refused report blocks solution/ai_operating/impl/benefits/scores key families")

        money_keys = [
            f.get("key")
            for s in sections
            for f in s.get("figures", [])
            if f.get("unit") == "money" and f.get("status") == "known"
        ]
        check("P", "problem.loaded_cost" in money_keys,
              "legitimate monetary assessment facts remain allowed in REFUSED")

        geo_payload = base_payload(geography=None)
        geo = post_run(client, geo_payload)
        check("Q", "currency unresolved" in (geo.get("report_markdown") or "").lower(),
              "no geography/currency fallback is applied")
    finally:
        orch.estimator.estimate = orig_est


def case_S_T_U_V_W_AC(client: TestClient) -> None:
    print("\nS/T/U/V/W/AC — compliance, trust boundary, canonical economics, no duplicated pipeline")
    install_llm_stub()

    seen = {"ok": False}
    orig_est = orch.estimator.estimate
    orig_rank = orch.driver_ranking.rank_drivers
    orig_alt = orch.alternatives_mod.derive
    orig_sens = orch.sensitivity_mod.sweep
    orig_run = api_main.orchestrate.run_assessment
    counts = {"est": 0, "rank": 0, "alt": 0, "sens": 0}
    canonical = {"ok": False}

    try:
        def est_inspect(s):
            counts["est"] += 1
            seen["ok"] = "HIPAA" in ((s.risk or RiskInputs()).compliance_exposure or [])
            return solution()

        def rank_count(*a, **k):
            counts["rank"] += 1
            return orig_rank(*a, **k)

        def alt_count(*a, **k):
            counts["alt"] += 1
            return orig_alt(*a, **k)

        def sens_count(*a, **k):
            counts["sens"] += 1
            return orig_sens(*a, **k)

        def run_wrap(*a, **k):
            run = orig_run(*a, **k)
            canonical["ok"] = (run.bundle.economics == run.drivers.scores.result)
            return run

        orch.estimator.estimate = est_inspect
        orch.driver_ranking.rank_drivers = rank_count
        orch.alternatives_mod.derive = alt_count
        orch.sensitivity_mod.sweep = sens_count
        api_main.orchestrate.run_assessment = run_wrap

        payload = base_payload(risk=RiskInputs(failure_impact="wrong payment",
                                               compliance_exposure=["HIPAA"]).model_dump(mode="json"))
        res = post_run(client, payload)
        check("S", seen["ok"], "compliance requirements are preserved into estimator path")
        check("U", canonical["ok"], "ReportInput uses canonical EconomicResult (drivers.scores.result)")
        check("V/W", counts == {"est": 1, "rank": 1, "alt": 1, "sens": 1},
              f"API does not duplicate analytical stages ({counts})")

        direct = orig_run(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        via_api = post_run(client, base_payload())
        check("AC", via_api["report_json"] == direct.rendered.json_doc,
              "pipeline output exposed by API is unchanged")
        check("T", "scores" not in state().model_dump(mode="json"),
              "AssessmentState payload is not polluted with downstream objects")
    finally:
        api_main.orchestrate.run_assessment = orig_run
        orch.estimator.estimate = orig_est
        orch.driver_ranking.rank_drivers = orig_rank
        orch.alternatives_mod.derive = orig_alt
        orch.sensitivity_mod.sweep = orig_sens


def case_X_Y_Z_AA_AB(client: TestClient) -> None:
    print("\nX/Y/Z/AA/AB — interview routes + context semantics + voice route availability")

    orig_run_turn = interview_api.run_turn
    try:
        def fake_run_turn(st, message, context=None):
            if context is None:
                context = ConversationContext(name="anon")
            context.note_turn(message)
            return TurnResult(
                state=st,
                context=context,
                stop=False,
                status=InterviewStatus.INTERVIEWING,
                question=f"next:{context.name or 'anon'}",
                acknowledgment="ok",
            )

        interview_api.run_turn = fake_run_turn

        start = client.post("/api/interview/start", json={
            "sector": "document_processing",
            "problem": "Automate AP",
            "warm_up": True,
        })
        check("X", start.status_code == 200, "existing /api/interview/start still works")

        body1 = start.json()
        turn = client.post("/api/interview/turn", json={
            "state": body1["state"],
            "context": body1["context"],
            "message": "next msg",
        })
        check("Y", turn.status_code == 200, "existing /api/interview/turn still works")

        body2 = turn.json()
        check("Z", len(body2["context"].get("recent_turns", [])) >= 2,
              "ConversationContext survives successive turns")

        c1 = ConversationContext(name="A").model_dump(mode="json")
        c2 = ConversationContext(name="B").model_dump(mode="json")
        t1 = client.post("/api/interview/turn", json={"state": body1["state"], "context": c1, "message": "m1"}).json()
        t2 = client.post("/api/interview/turn", json={"state": body1["state"], "context": c2, "message": "m2"}).json()
        check("AA", t1["question"] != t2["question"],
              "different contexts remain isolated")
    finally:
        interview_api.run_turn = orig_run_turn

    ws_paths = {getattr(r, "path", "") for r in api_main.app.routes}
    check("AB", "/ws/interview/voice" in ws_paths, "existing voice websocket route remains available")


class _FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        return self.messages.pop(0)

    async def send_json(self, value):
        self.sent.append(value)

    async def close(self):
        self.closed = True


def case_voice_transport_and_cors() -> None:
    print("\nVOICE/CORS — state/context/transcript wire contract and allowed origins")
    orig_transcribe = voice_session_mod.transcribe_audio
    orig_run_turn = voice_session_mod.run_turn
    orig_audio = voice_api._base64_audio
    try:
        def fake_transcribe(audio, **_kw):
            return ("terminal transcript" if audio == b"terminal"
                    else "first transcript")

        def fake_run_turn(st, message, context=None):
            context = context or ConversationContext()
            context.note_turn(message)
            terminal = message == "terminal transcript"
            status = InterviewStatus.READY if terminal else InterviewStatus.INTERVIEWING
            st.status = status
            st.complete = terminal
            return TurnResult(
                state=st, context=context, status=status, stop=terminal,
                stop_reason="complete" if terminal else None,
                question=None if terminal else "next question",
                acknowledgment="ack",
            )

        voice_session_mod.transcribe_audio = fake_transcribe
        voice_session_mod.run_turn = fake_run_turn
        voice_api._base64_audio = lambda _text: "mock-audio"

        ws = _FakeWebSocket([
            {"type": "websocket.receive", "bytes": b"before"},
            {"type": "websocket.receive", "text": json.dumps({"action": "ping"})},
            {"type": "websocket.receive", "text": json.dumps({"action": "unknown"})},
            {"type": "websocket.receive", "text": json.dumps({
                "action": "start", "sector": "document_processing", "problem": "invoice intake"})},
            {"type": "websocket.receive", "bytes": b"first"},
            {"type": "websocket.receive", "bytes": b"terminal"},
            {"type": "websocket.disconnect"},
        ])
        asyncio.run(voice_api.voice_interview(ws))

        errors = [m for m in ws.sent if m.get("type") == "error"]
        turns = [m for m in ws.sent if m.get("type") == "turn"]
        ready = next(m for m in ws.sent if m.get("type") == "ready")
        check("voice-A", turns[0]["transcript"] == "first transcript",
              "successful voice turn returns actual STT transcript")
        check("voice-B", isinstance(turns[0].get("state"), dict),
              "voice turn exposes AssessmentState")
        check("voice-C", isinstance(turns[0].get("context"), dict)
              and bool(turns[0].get("request_id")),
              "voice turn exposes ConversationContext and a connection id")
        check("voice-D", turns[1]["transcript"] == "terminal transcript"
              and turns[1]["stop"] and isinstance(turns[1]["state"], dict)
              and isinstance(turns[1]["context"], dict),
              "terminal turn carries state/context required for assessment submission")
        check("voice-E", any(m.get("type") == "pong" for m in ws.sent),
              "ping/pong behavior remains intact")
        check("voice-F", any(m.get("message") == "start first" for m in errors),
              "audio-before-start retains error")
        check("voice-G", any(m.get("message") == "unknown action" for m in errors),
              "unknown action retains error")
        check("voice-H", ready.get("state") and ready.get("context") is not None,
              "ready payload exposes initial state/context")
    finally:
        voice_session_mod.transcribe_audio = orig_transcribe
        voice_session_mod.run_turn = orig_run_turn
        voice_api._base64_audio = orig_audio

    original_session = voice_api.VoiceSession
    try:
        class BrokenSession:
            def start(self, *_args, **_kwargs):
                raise RuntimeError("voice internals must not reach clients")

        voice_api.VoiceSession = BrokenSession
        failed_ws = _FakeWebSocket([
            {"type": "websocket.receive", "text": json.dumps({
                "action": "start", "sector": "document_processing"})},
            {"type": "websocket.disconnect"},
        ])
        asyncio.run(voice_api.voice_interview(failed_ws))
        safe_error = next(m for m in failed_ws.sent if m.get("type") == "error")
        check("voice-I", safe_error.get("message") == "Voice interview unavailable"
              and "internals" not in str(safe_error)
              and bool(safe_error.get("request_id")),
              "unexpected WebSocket failure is safe and observable")
    finally:
        voice_api.VoiceSession = original_session

    old = os.environ.get("DEPLOYIQ_ALLOWED_ORIGINS")
    try:
        os.environ["DEPLOYIQ_ALLOWED_ORIGINS"] = "https://frontend.example, https://preview.example"
        configured = importlib.reload(api_main)
        cors_client = TestClient(configured.app)
        allowed = cors_client.options("/api/assessment/run", headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
        })
        denied = cors_client.options("/api/assessment/run", headers={
            "Origin": "https://arbitrary.example",
            "Access-Control-Request-Method": "POST",
        })
        check("cors-I", allowed.headers.get("access-control-allow-origin") == "https://frontend.example",
              "CORS allows configured origin for POST")
        check("cors-J", denied.headers.get("access-control-allow-origin") != "https://arbitrary.example",
              "CORS does not allow arbitrary origin")
        os.environ["DEPLOYIQ_ALLOWED_ORIGINS"] = "*"
        wildcard_rejected = False
        try:
            Settings.from_env()
        except RuntimeError:
            wildcard_rejected = True
        check("cors-J", wildcard_rejected,
              "credentialed CORS configuration rejects wildcard origin")
    finally:
        if old is None:
            os.environ.pop("DEPLOYIQ_ALLOWED_ORIGINS", None)
        else:
            os.environ["DEPLOYIQ_ALLOWED_ORIGINS"] = old
        importlib.reload(api_main)


def case_platform_core() -> None:
    print("\nPLATFORM — core package/config/request context")
    check("platform", bool(core.__doc__), "core package imports")
    configured = Settings.from_env()
    check("platform", isinstance(configured.allowed_origins, tuple),
          "environment configuration produces an immutable origin allowlist")
    token = set_request_id("test-request-id")
    try:
        check("platform", get_request_id() == "test-request-id",
              "request context retains the active correlation id")
    finally:
        reset_request_id(token)
    check("platform", get_request_id() is None,
          "request context resets after request completion")


def case_operational_core() -> None:
    print("\nOPERATIONS — stage telemetry, usage extraction, and safe metadata")
    old_prices = os.environ.get("DEPLOYIQ_MODEL_PRICES_JSON")
    token = set_request_id("cost-request-id")
    try:
        os.environ["DEPLOYIQ_MODEL_PRICES_JSON"] = (
            '{"test-model":{"input_per_1m_usd":2.0,"output_per_1m_usd":4.0}}'
        )
        tracker.clear()
        event = record_usage(
            purpose="chat_json", model="test-model",
            usage={"prompt_tokens": 500_000, "completion_tokens": 250_000},
        )
        missing = record_usage(purpose="audio_transcription", model="stt", usage=None)
        check("ops", event.request_id == "cost-request-id"
              and event.total_tokens == 750_000
              and event.estimated_usd == 2.0,
              "usage event carries request id, tokens, and configured cost")
        check("ops", missing.total_tokens is None and missing.estimated_usd is None,
              "missing provider usage is recorded without invented cost")
    finally:
        reset_request_id(token)
        if old_prices is None:
            os.environ.pop("DEPLOYIQ_MODEL_PRICES_JSON", None)
        else:
            os.environ["DEPLOYIQ_MODEL_PRICES_JSON"] = old_prices

    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    logger = logging.getLogger("deployiq.stage")
    handler = Capture()
    logger.addHandler(handler)
    try:
        check("ops", run_stage("unit_stage", lambda: "ok") == "ok",
              "stage wrapper preserves operation result")
        try:
            run_stage("unit_failure", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        except RuntimeError:
            pass
        check("ops", any("stage_started name=unit_stage" in item for item in captured)
              and any("stage_completed name=unit_stage" in item for item in captured)
              and any("stage_failed name=unit_failure" in item for item in captured),
              "stage telemetry records start, completion, and failure without payload content")
    finally:
        logger.removeHandler(handler)


def case_voice_resume() -> None:
    """The socket adopts the REST interview instead of starting a second one."""
    print("\nVOICE/RESUME — websocket adopts the interview /api/interview/start began")
    orig_run_turn = voice_session_mod.run_turn
    orig_audio = voice_api._base64_audio
    turns_run: list[str] = []
    try:
        def counting_run_turn(st, message, context=None):
            turns_run.append(message)
            return TurnResult(state=st, context=context or ConversationContext(),
                              status=InterviewStatus.INTERVIEWING, stop=False,
                              question="next question", acknowledgment="ack")

        voice_session_mod.run_turn = counting_run_turn
        voice_api._base64_audio = lambda text: f"audio:{text}"

        started = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="ticket backlog")
        started.turn_count = 1
        ws = _FakeWebSocket([
            {"type": "websocket.receive", "text": json.dumps({
                "action": "resume",
                "state": started.model_dump(mode="json"),
                "context": ConversationContext().model_dump(mode="json"),
                "speak": "Hello there. What is your name?"})},
            {"type": "websocket.disconnect"},
        ])
        asyncio.run(voice_api.voice_interview(ws))
        ready = next(m for m in ws.sent if m.get("type") == "ready")

        check("resume-A", turns_run == [],
              "resume runs NO interview turn — the client already holds turn one")
        check("resume-B", ready.get("question") is None
              and ready.get("acknowledgment") is None,
              "the ready frame carries no question/acknowledgment, so the client "
              "cannot render the greeting a second time")
        check("resume-C", ready["state"]["turn_count"] == 1,
              "the client's own AssessmentState is adopted, not a fresh one")
        check("resume-D", ready.get("audio") == "audio:Hello there. What is your name?",
              "`speak` is synthesized so the first question is still voiced")

        # Without `speak` there is nothing to say and no TTS call is made.
        ws2 = _FakeWebSocket([
            {"type": "websocket.receive", "text": json.dumps({
                "action": "resume", "state": started.model_dump(mode="json"),
                "context": None})},
            {"type": "websocket.disconnect"},
        ])
        asyncio.run(voice_api.voice_interview(ws2))
        ready2 = next(m for m in ws2.sent if m.get("type") == "ready")
        check("resume-E", "audio" not in ready2,
              "no `speak` means no synthesized audio rather than empty audio")
    finally:
        voice_session_mod.run_turn = orig_run_turn
        voice_api._base64_audio = orig_audio


def case_voice_turn_failure_is_survivable() -> None:
    """A failed turn must not end the interview."""
    print("\nVOICE/RESILIENCE — one failed turn does not destroy the session")
    orig_transcribe = voice_session_mod.transcribe_audio
    orig_run_turn = voice_session_mod.run_turn
    orig_audio = voice_api._base64_audio
    try:
        calls = {"n": 0}

        class _FakeRateLimit(Exception):
            pass
        _FakeRateLimit.__name__ = "RateLimitError"

        def flaky_transcribe(audio, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _FakeRateLimit("You have no credits remaining. Add credits at "
                                     "https://platform.openai.com/settings/billing")
            return "eight thousand tickets"

        def fake_run_turn(st, message, context=None):
            return TurnResult(state=st, context=context or ConversationContext(),
                              status=InterviewStatus.INTERVIEWING, stop=False,
                              question="next question", acknowledgment="ack")

        voice_session_mod.transcribe_audio = flaky_transcribe
        voice_session_mod.run_turn = fake_run_turn
        voice_api._base64_audio = lambda _t: "mock-audio"

        started = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="backlog")
        ws = _FakeWebSocket([
            {"type": "websocket.receive", "text": json.dumps({
                "action": "resume", "state": started.model_dump(mode="json"),
                "context": None})},
            {"type": "websocket.receive", "bytes": b"first-attempt"},   # fails
            {"type": "websocket.receive", "bytes": b"second-attempt"},  # succeeds
            {"type": "websocket.disconnect"},
        ])
        asyncio.run(voice_api.voice_interview(ws))

        turn_errors = [m for m in ws.sent if m.get("type") == "turn_error"]
        turns = [m for m in ws.sent if m.get("type") == "turn"]
        fatal = [m for m in ws.sent if m.get("type") == "error"]

        check("resilience-A", len(turn_errors) == 1 and turn_errors[0].get("recoverable") is True,
              "the failed turn is reported as recoverable, not as a dead session")
        check("resilience-B", len(turns) == 1 and turns[0]["transcript"] == "eight thousand tickets",
              "the NEXT answer on the same socket still works — the interview survived")
        check("resilience-C", not fatal,
              "no fatal error frame is sent for a single failed turn")
        # Read defensively: when the isolation regresses there is no
        # turn_error at all, and the remaining checks should report that
        # rather than crash the suite on an index.
        msg = turn_errors[0]["message"] if turn_errors else ""
        check("resilience-D", bool(msg) and "credits" not in msg
              and "platform.openai.com" not in msg,
              "account and billing detail stays in the log, not on the wire")
        check("resilience-E", "continue by typing" in msg,
              "the user is told what they can still do")
    finally:
        voice_session_mod.transcribe_audio = orig_transcribe
        voice_session_mod.run_turn = orig_run_turn
        voice_api._base64_audio = orig_audio


def case_speech_optional() -> None:
    """A turn survives losing its voice."""
    print("\nVOICE/DEGRADE — speech synthesis is optional, the turn is not")
    orig_transcribe = voice_session_mod.transcribe_audio
    orig_run_turn = voice_session_mod.run_turn
    orig_audio = voice_api._base64_audio
    try:
        voice_session_mod.transcribe_audio = lambda audio, **_k: "eight thousand tickets"
        voice_session_mod.run_turn = lambda st, m, c=None: TurnResult(
            state=st, context=c or ConversationContext(),
            status=InterviewStatus.INTERVIEWING, stop=False,
            question="next question", acknowledgment="ack")

        def no_speech(_text):
            raise RuntimeError("model_not_found: this provider serves no TTS")
        voice_api._base64_audio = no_speech

        started = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="backlog")
        ws = _FakeWebSocket([
            {"type": "websocket.receive", "text": json.dumps({
                "action": "resume", "state": started.model_dump(mode="json"),
                "context": None})},
            {"type": "websocket.receive", "bytes": b"audio"},
            {"type": "websocket.disconnect"},
        ])
        asyncio.run(voice_api.voice_interview(ws))
        turns = [m for m in ws.sent if m.get("type") == "turn"]
        errors = [m for m in ws.sent if m.get("type") in ("error", "turn_error")]

        check("degrade-A", len(turns) == 1,
              "the turn is still delivered when speech synthesis fails")
        check("degrade-B", turns and turns[0].get("question") == "next question",
              "the question survives — it is on screen whether or not it is spoken")
        check("degrade-C", turns and "audio" not in turns[0]
              and turns[0].get("speech_unavailable") is True,
              "the missing voice is declared rather than sent as empty audio")
        check("degrade-D", not errors,
              "losing speech is not reported to the user as a failed turn")
    finally:
        voice_session_mod.transcribe_audio = orig_transcribe
        voice_session_mod.run_turn = orig_run_turn
        voice_api._base64_audio = orig_audio


def case_provider_pool() -> None:
    """Multiple keys across multiple providers: spread, fail over, don't burn."""
    print("\nPROVIDER POOL — key rotation and failover across Groq/Gemini")
    import llm.provider as prov

    pool_json = json.dumps({"LLM": [
        {"base_url": "https://api.groq.com/openai/v1",
         "model": "llama-3.3-70b-versatile", "keys": ["gsk_one", "gsk_two"]},
        {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
         "model": "gemini-2.0-flash", "keys": ["AIza_one"]}]})
    prior = os.environ.get("DEPLOYIQ_PROVIDER_POOL")
    os.environ["DEPLOYIQ_PROVIDER_POOL"] = pool_json
    prov.reset()
    try:
        eps = prov.pool("LLM").endpoints
        check("pool-A", len(eps) == 3,
              "every key across both providers becomes an endpoint")
        check("pool-B", [e.model for e in eps].count("llama-3.3-70b-versatile") == 2
              and "gemini-2.0-flash" in [e.model for e in eps],
              "the model travels with the endpoint — the pool spans providers")
        check("pool-C", all("gsk_one" not in e.label and "AIza_one" not in e.label
                            for e in eps),
              "an endpoint label carries a host and a fingerprint, never the key")

        # Work spreads instead of hammering the first key until it dies.
        firsts = {prov.pool("LLM").ordered()[0].label for _ in range(3)}
        check("pool-D", len(firsts) == 3, "successive calls start on different keys")

        # Exhausted endpoints are skipped and the next one answers.
        class _RateLimit(Exception):
            pass
        _RateLimit.__name__ = "RateLimitError"
        seen: list[str] = []

        def spend_two(client, model):
            seen.append(model or "")
            if len(seen) <= 2:
                raise _RateLimit("rate limited")
            return "answered"

        prov.reset()
        check("pool-E", prov.execute("LLM", spend_two) == "answered",
              "a call survives two exhausted keys by failing over to a third")
        check("pool-F", len(seen) == 3, "each endpoint was tried exactly once")

        # A malformed request must NOT be retried around the pool.
        prov.reset()
        tried: list[int] = []

        def bad_request(client, model):
            tried.append(1)
            raise ValueError("invalid request payload")

        try:
            prov.execute("LLM", bad_request)
        except ValueError:
            pass
        check("pool-G", len(tried) == 1,
              "a bad request fails immediately rather than burning every key on it")

        # When everything is spent the error says so, and names no key.
        prov.reset()
        def always_spent(client, model):
            raise _RateLimit("rate limited")
        try:
            prov.execute("LLM", always_spent)
            exhausted_msg = ""
        except prov.ProvidersExhausted as exc:
            exhausted_msg = str(exc)
        check("pool-H", "exhausted" in exhausted_msg
              and "gsk_one" not in exhausted_msg,
              "exhaustion is reported as exhaustion, without leaking a key")
    finally:
        if prior is None:
            os.environ.pop("DEPLOYIQ_PROVIDER_POOL", None)
        else:
            os.environ["DEPLOYIQ_PROVIDER_POOL"] = prior
        prov.reset()


def main() -> None:
    client = TestClient(api_main.app)
    case_A_B_C(client)
    case_D_E_F_G_H_I_J(client)
    case_S_T_U_V_W_AC(client)
    case_X_Y_Z_AA_AB(client)
    case_voice_transport_and_cors()
    case_voice_resume()
    case_voice_turn_failure_is_survivable()
    case_speech_optional()
    case_provider_pool()
    case_platform_core()
    case_operational_core()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S) / {checks} assertions:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"ALL P7 API CASES PASSED ({checks} assertions)")


if __name__ == "__main__":
    main()
