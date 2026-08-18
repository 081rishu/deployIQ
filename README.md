# AI Deployment Decision Engine — MVP

An adaptive-interview and deterministic-economics tool that helps
organizations evaluate the economic, technical, and operational
viability of an AI deployment. Full product reasoning lives in
`deployID_FULL_PRODUCT.txt` — this README covers
the MVP build only.

## Tech stack

- **Backend:** FastAPI (Python 3.13), runs in `.venv/`
- **Validation/schema:** Pydantic (server-side, source of truth)
- **LLM:** OpenAI API, called server-side only (from FastAPI
  endpoints)
- **Calc:** pure Python functions under `calc/` — no LLM, no framework
- **State:** passed between client and server each request, not persisted

No database. No auth. No frontend yet (deferred until needed). See
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

```
.
├── api/                        # FastAPI backend
│   ├── __init__.py
│   ├── main.py                 # app + route wiring
│   ├── interview.py            # interview turn endpoint
│   ├── ai_solution.py          # approach + ranges + effort band
│   └── report.py               # final report prose generation
│
├── schemas/
│   ├── __init__.py
│   └── assessment_state.py     # single source-of-truth Pydantic model
│
├── interviewer/
│   ├── __init__.py
│   ├── fields.py               # generalized field/question registry
│   └── engine.py               # 4-state adaptive engine (need select -> ask)
│
├── llm/
│   ├── __init__.py
│   └── openai_client.py        # thin wrapper around the OpenAI SDK
│
├── deployIQ_MVP.txt                     # current spec (single file; v1-v4 merged)
├── deployID_FULL_PRODUCT.txt
├── ARCHITECTURE.txt
├── .venv/                     # Python 3.13 virtual env
├── .gitignore
├── .env.example              # copy to .env and fill in OPENAI_API_KEY
├── .env                       # OPENAI_API_KEY (not committed)
├── requirements.txt
└── README.md
```

## Environment variables

```
OPENAI_API_KEY=your_key_here
```

## Getting started

```bash
# venv already exists at .venv/
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn api.main:app --reload
```

API runs on `http://localhost:8000`. No frontend yet.

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
