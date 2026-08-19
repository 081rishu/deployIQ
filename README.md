# AI Deployment Decision Engine — MVP

An adaptive-interview and deterministic-economics tool that helps
organizations evaluate the economic, technical, and operational
viability of an AI deployment. Full product reasoning lives in
`deployID_FULL_PRODUCT.txt` — this README covers
the MVP build only.

## Tech stack

- **Backend:** FastAPI (Python 3.12+, per spec 16), runs in `.venv/`
  — developed on 3.13; the acceptance suites are also validated on 3.12
- **Validation/schema:** Pydantic (server-side, source of truth)
- **LLM:** OpenAI API, called server-side only (from FastAPI
  endpoints)
- **Calc:** pure Python functions under `calc/` — no LLM, no framework
- **State:** passed between client and server each request, not persisted

No database. No auth. No product frontend yet (deferred until needed —
the Streamlit and voice pages are test harnesses, not the product). See
`ARCHITECTURE.txt` for the reasoning behind these calls and the
module-level breakdown.

## Scope

MVP scope: Customer Support and Document Processing. The MVP uses
static sector benchmarks, a single LLM provider, client-side
assessment state, and deterministic calculations. It intentionally
excludes persistent organization data, live market feeds,
multi-provider model comparison, and post-deployment feedback loops.
See `deployID_FULL_PRODUCT.txt` for what's out of
scope and why.

## Data provenance

Every assessment value is tagged as `user_provided`, `sourced`,
`estimated`, `assumed`, or `derived`. This tagging is preserved end to
end, from the interview through to the final report — no number
appears without a traceable origin.

## Directory structure

Reflects what is on disk. Modules named in `ARCHITECTURE.txt` but not yet
built are listed under "Not built yet" below.

```
.
├── api/                        # FastAPI backend
│   ├── main.py                 # app + route wiring
│   ├── interview.py            # interview turn endpoints
│   └── voice.py                # voice interview over WebSocket
│
├── schemas/
│   └── assessment_state.py     # single source-of-truth Pydantic model
│
├── interviewer/                # AI interviewer (spec 10)
│   ├── fields.py               # generalized field/question registry
│   ├── engine.py               # 4-state adaptive engine (need select -> ask)
│   ├── conversation.py         # turn-to-turn conversational context
│   └── voice.py                # STT -> engine -> TTS turn orchestration
│
├── solution/                   # AI solution & architecture estimator (spec 7)
│   ├── schema.py               # estimator + alternatives output types
│   ├── patterns.py             # solution registry (patterns/impls/providers)
│   ├── registry.py             # registry loading/validation
│   ├── capabilities.py         # LLM workflow -> capability decomposition
│   ├── ranking.py              # deterministic filter + rank
│   ├── scope.py                # scope-derived effort/integration bands
│   ├── workload.py             # handling-time -> workload shares
│   ├── effort_bands.py         # effort band -> hours/rate/cost
│   ├── performance.py          # per-architecture performance metrics
│   ├── reference_solutions.py  # curated baseline per sector
│   ├── evidence.py             # anchoring + provenance integrity sweep
│   ├── risks.py                # structured risk controls
│   ├── confidence.py           # field-quality/evidence confidence model
│   ├── calibration.py          # versioned scope calibration params
│   ├── constants.py
│   ├── alternatives.py         # spec 11 — registry-derived, LLM-explained
│   └── estimator.py            # orchestrator
│
├── llm/
│   ├── openai_client.py        # thin wrapper around the OpenAI SDK
│   ├── stt.py                  # speech-to-text
│   └── tts.py                  # text-to-speech
│
├── calc/                       # Economic Engine (8) + Scoring (9) — no LLM
│   ├── models.py               # value types + interval arithmetic
│   ├── labor.py                # 8.1 both labor formulations + consistency check
│   ├── current_state.py        # 8.2 current annual cost
│   ├── ai_state.py             # 8.3/8.4 task AI economics, operating cost
│   ├── implementation.py       # 8.5 staged buy/build costing
│   ├── lifecycle.py            # 8.6/8.7 unit economics, savings, payback
│   ├── benchmark_check.py      # 8.8 cross-check (never additive)
│   ├── inference.py            # per-unit inference pricing from the registry
│   ├── quality.py              # current-vs-AI quality comparison (absent-safe)
│   ├── reliability.py          # reliability-gap consequence
│   ├── sensitivity.py          # 8.10 recalculation interface
│   ├── calibration.py          # versioned economic calibration params
│   ├── engine.py               # orchestrator
│   ├── economic_score.py       # 9.1
│   ├── economic_sanity.py      # plausibility gate feeding 9.1's flags
│   ├── feasibility_score.py    # 9.2
│   ├── risk_score.py           # 9.3
│   ├── composite.py            # 9.4
│   ├── driver_ranking.py       # 9.5 decision drivers + uncertainty callout
│   ├── uncertainty.py          # typed uncertainty (no fake categorical width)
│   ├── assessment_confidence.py# 9.7
│   └── scoring_calibration.py  # versioned scoring calibration params
│
├── data/
│   ├── sector_benchmarks/      # static benchmark packs, one JSON per sector
│   ├── ai_pricing.json         # model/provider per-unit pricing registry
│   ├── labor_rates.json        # process + implementation labor rates
│   ├── compliance_evidence.json
│   └── compliance_attestations.json
│
├── lib/
│   ├── benchmarks.py           # benchmark pack loader + provenance guardrail
│   ├── pricing.py              # pricing registry loader
│   ├── labor_rates.py          # labor-rate registry loader
│   ├── compliance.py           # compliance evidence/attestation registry
│   ├── vendor_attestations.py  # attestation ingest
│   ├── reconciliation.py       # handling-time reconciliation helpers
│   └── logging_config.py       # get_logger() helper
│
├── app.py                      # Streamlit harness for driving the interviewer
├── static/voice.html           # browser client for the voice interview
├── scripts/                    # acceptance suites + manual test harnesses
│   ├── interviewer_cases.py    # spec 10
│   ├── estimator_cases.py      # spec 7
│   ├── ranking_cases.py        # spec 7.6/7.7
│   ├── economic_cases.py       # spec 8
│   ├── scoring_cases.py        # spec 9
│   ├── alternatives_cases.py   # spec 11
│   ├── compliance_cases.py     # compliance evidence registry
│   └── conversation_test.py, ws_test_client.py, ws_readable.py
├── logs/                       # recorded conversation-test transcripts
├── docs/                       # per-layer work plans, critiques and open items
│
├── deployIQ_MVP.txt            # current spec (single file; v1-v6 merged)
├── deployID_FULL_PRODUCT.txt
├── ARCHITECTURE.txt
├── .env.example                # copy to .env and fill in OPENAI_API_KEY
├── requirements.txt
└── README.md
```

Not built yet (specified, no code):

- `api/solution.py` — a standalone estimator-only endpoint.
- The review screen (spec 14, screen 3), where benchmark-vs-user-value choices
  would be resolved.

Built in the API layer:

- `POST /api/assessment/run` — thin adapter over `pipeline.run_assessment(...)`.
  Input: `AssessmentState` plus orchestration controls (`labor_realization`,
  optional narration flag/model/temp, and `report_format=json|markdown|both`).
  Output: pipeline report mode (`full|partial|refused`) and rendered report in
  JSON and/or Markdown from the existing report renderer.
- Existing interview endpoints remain unchanged:
  - `POST /api/interview/start`
  - `POST /api/interview/turn`
- Existing voice WebSocket route remains unchanged:
  - `/ws/interview/voice`

Production frontend configuration:

- Set `DEPLOYIQ_ALLOWED_ORIGINS` to a comma-separated allowlist of HTTPS frontend origins, for example `https://<frontend-host>`.
- The backend rejects `*` in this setting because credentialed CORS must not allow arbitrary origins.
- REST uses `https://<backend>/api/...`; voice uses `wss://<backend>/ws/interview/voice`.

Session/context behavior:

- Assessment analysis is request-scoped and stateless server-side.
- Interview conversation context is client-carried (`ConversationContext`), so
  same context continues the same conversation and different contexts remain
  isolated.

Report semantics:

- `full`, `partial`, and `refused` are analytical outcomes returned as normal
  responses (not coerced into HTTP failures).
- Narration is optional; if unavailable, deterministic report output is
  returned unchanged.
- `refused` reports stay validator-constrained and do not fabricate downstream
  key families (`solution.*`, `ai_operating.*`, `impl.*`, `benefits.*`,
  `scores.*`).

## Validation

Every analytical layer ships an acceptance suite under `scripts/`. They stub
the LLM, need no API key, and are deterministic:

```bash
for f in scripts/*_cases.py; do python3 "$f"; done
```

Last full run: 771 checks, 0 failures. The suites
stub the OpenAI client, so they exercise the deterministic layers only.

## Environment variables

```
OPENAI_API_KEY=your_key_here
DEPLOYIQ_ALLOWED_ORIGINS=https://<frontend-host>
```

## Getting started

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env        # then fill in OPENAI_API_KEY

.venv/bin/python -m uvicorn api.main:app --reload
```

API runs on `http://localhost:8000`. There is no product frontend; two test
surfaces exist for driving the interviewer by hand:

```bash
.venv/bin/python -m streamlit run app.py     # text interview harness
# or, with the API running, open http://localhost:8000/voice
```

## Interviewer (adaptive, 4-state)

The interviewer is a stateless turn handler — no server-side session; the
client ships the `AssessmentState` back each turn. It moves through four
states:

```
INTERVIEWING -> need more information (ask a NEW field)
CLARIFYING   -> existing answer is ambiguous/insufficient (re-ask/deepen)
READY        -> minimum sufficient state reached (stop)
UNCERTAIN    -> cannot obtain reliable info after reasonable attempts
```

Deterministic logic decides what to ask next and when to stop; the LLM only
interprets the latest message and phrases the question. Unresolved needs can
be a missing field, ambiguous answer, low-confidence estimate, contradiction,
or a need to drill deeper — ranked by decision relevance. Fields that don't
feed the analysis (or that a benchmark default can satisfy) are never asked
about.

## Solution ranking

Candidate architectures are ranked deterministically in `solution/ranking.py`.
Sector influence enters through the curated reference solution for the sector,
never as a bare sector bonus. Four weighted terms, each normalised to 0-1
(max 10.0):

```
reference_alignment  x 4.0   follows the sector baseline — or deviates for a
                             reason the baseline itself sanctions
scale_fit            x 2.0   the chosen stack is rated for the volume
compliance_fit       x 2.0   the stack covers the stated constraints
complexity_pref      x 2.0   prefer the cheapest build that still fits
```

Each reference solution carries `conditions_for_deviation` as evaluable
triggers, not prose, so the ranker can leave the baseline when the assessment
warrants it. Conditions that depend on facts the assessment does not capture
are tagged `MANUAL` and surfaced as uncertainties rather than silently ignored.

```bash
python3 scripts/ranking_cases.py     # tie case + 3 adversarial cases, no LLM
python3 scripts/estimator_cases.py   # C1-C14 acceptance cases A-I, no LLM
```

The estimator enforces its boundary structurally: the LLM decomposes and
estimates, but it cannot select a pattern, invent hours or rates, set a
workload share, or have a citation reach the output as evidence — an unbacked
`sourced` claim is downgraded automatically.

## Economic Engine

`calc/` implements spec section 8. Pure Python, no LLM: estimator output
arrives pre-tagged as `estimated` and is treated as an assumption throughout.
The model is task-level, so an automation percentage never becomes a headcount
reduction by arithmetic alone — whether freed capacity turns into money is an
explicit required argument (`LaborRealization`), not a default.

Uncollected cost components are reported ABSENT rather than zero, which makes
the current-cost baseline an explicit floor. Payback is only stated when the
monthly net benefit is genuinely positive.

```bash
python3 scripts/economic_cases.py    # 21 checks, no LLM
```

## Scoring and Decision Drivers

`calc/economic_score.py`, `feasibility_score.py`, `risk_score.py`,
`composite.py` and `driver_ranking.py` implement spec section 9. Scores
explain; they never decide. A score with a missing input is reported as NOT
COMPUTABLE rather than zero, and a compliance blocker is a hard flag that
forces Risk to zero and propagates to the composite instead of being averaged
away by strong economics.

Decision Drivers are ranked by elasticity — (% change in score) / (% change in
input) — computed by deterministic recalculation. The uncertainty callout is
the variable with the highest (relative range width x elasticity): influential
AND poorly known, since neither alone qualifies. The LLM only rephrases the
statements; it never selects which facts appear.

```bash
python3 scripts/scoring_cases.py     # 25 checks, no LLM
```

See `docs/scoring_system_todo.md` for known weaknesses — in particular that
elasticity measured on a bounded score is distorted by saturation.

## Benchmark packs

`data/sector_benchmarks/*.json`, loaded by `lib/benchmarks.py`. Each figure
carries value/unit, provenance, source, URL, date and geography, and is
exposed as a `RangeEstimate` so it enters the analysis with its citation
attached. A figure may only claim `sourced` if it names a retrievable source
with a URL and date — the loader rejects anything else, so an unsourced number
cannot be dressed as a benchmark (spec 4.3).

The two packs are not equally solid, and that is recorded rather than smoothed
over: document processing has 8 of 11 figures read from the primary document,
customer support has 0 of 8.

## Build principle (worth keeping visible while developing)

LLM calls only ever produce: extracted fields, phrased questions,
proposed AI approach + ranges, report prose. Every numerical value used
in scores, cost calculations, driver rankings, or decision analysis
must originate from `calc/`, not directly from an LLM response.
LLM-generated numerical estimates must be represented as explicit
ranges with confidence labels before entering the calculation layer.

Economic / Feasibility / Risk scores (`calc/economic_score.py`,
`feasibility_score.py`, `risk_score.py`) are analytical dimensions, not a
decision mechanism. Watch for the implementation quietly turning into
`score → recommendation` — the intended flow is:

```
facts + calculations
       ↓
economic / feasibility / risk dimensions
       ↓
sensitivity analysis
       ↓
decision drivers
       ↓
human decision
```

The system stops at decision drivers. It does not output a category or
a verdict.
