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
├── src/                        # installable runtime packages
│   ├── api/                    # FastAPI transport adapters
│   ├── calc/                   # frozen economics and scoring domain
│   ├── core/                   # config, logging, middleware, paths
│   ├── interviewer/            # adaptive interview domain
│   ├── lib/                    # registries, loaders, reconciliation
│   ├── llm/                    # provider adapters (language/audio only)
│   ├── pipeline/               # canonical orchestration
│   ├── report/                 # report assembly/presentation boundary
│   ├── schemas/                # shared Pydantic contracts
│   ├── solution/               # estimator, registry and alternatives domain
│   └── app.py                  # Streamlit interviewer harness
├── scripts/                    # executable acceptance suites and manual tools
├── docs/                       # specifications, critiques and operations notes
├── .env.example
├── .gitignore
├── ARCHITECTURE.txt
├── deployIQ_MVP.txt
├── pyproject.toml              # editable/installable project configuration
├── requirements.txt            # compatibility dependency list
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
for f in scripts/*_cases.py; do python -m "scripts.$(basename "${f%.py}")"; done
```

Last full run: 771 checks, 0 failures. The suites
stub the OpenAI client, so they exercise the deterministic layers only.

## Environment variables

- **Required for LLM/STT/TTS calls:** `OPENAI_API_KEY`.
- **Required in production with a separate frontend:** `DEPLOYIQ_ALLOWED_ORIGINS` — comma-separated HTTPS origins; `*` is rejected because credentialed CORS is enabled.
- **Optional model selection:** `OPENAI_MODEL`, `OPENAI_STT_MODEL`, `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE`.
- **Optional platform behavior:** `DEPLOYIQ_ENV`, `DEPLOYIQ_LOG_LEVEL`.
- **Optional process-local platform cost estimates:** `DEPLOYIQ_MODEL_PRICES_JSON` with USD-per-million-token input/output prices. When unset or usage is unavailable, usage is logged without an invented cost.

See `.env.example` for safe examples. Do not commit `.env` or `.env.local`.

## Deployment notes

- Install with `python -m pip install -e ".[dev]"` for source deployments.
- Start the backend with `python -m uvicorn api.main:app --host 0.0.0.0 --port 8000`.
- Readiness is `GET /health`; it returns process readiness and deliberately does not call OpenAI.
- A Vercel frontend uses `https://<backend>/api/...` for REST and `wss://<backend>/ws/interview/voice` for voice.
- JSON logs include request IDs and safe stage/cost metadata. This MVP does not persist telemetry or cost events.

## Getting started

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env        # then fill in OPENAI_API_KEY

.venv/bin/python -m uvicorn api.main:app --reload
# Production-style: python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

API runs on `http://localhost:8000`. There is no product frontend; two test
surfaces exist for driving the interviewer by hand:

```bash
.venv/bin/python -m streamlit run src/app.py # text interview harness
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
python -m scripts.ranking_cases     # tie case + 3 adversarial cases, no LLM
python -m scripts.estimator_cases   # C1-C14 acceptance cases A-I, no LLM
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
python -m scripts.economic_cases    # 21 checks, no LLM
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
python -m scripts.scoring_cases     # 25 checks, no LLM
```

See `docs/scoring_system_todo.md` for known weaknesses — in particular that
elasticity measured on a bounded score is distorted by saturation.

## Benchmark packs

`src/deployiq_assets/data/sector_benchmarks/*.json`, loaded by `lib/benchmarks.py`. Each figure
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
