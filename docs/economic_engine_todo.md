# Economic Engine (spec 8) — Implementation Plan & Critique

Implements `calc/` per spec section 8 and ARCHITECTURE 3.4. Pure Python, no LLM
anywhere in the layer. Estimator output (automation ranges, effort band) enters
pre-tagged as `estimated` and is treated as an assumption throughout.

Validate with `python3 scripts/economic_cases.py` (21 checks, no API key).

---

## Done

### Structure
- [x] `calc/models.py` — provenance-carrying value types, interval arithmetic in
      ONE auditable place, `CostLine`/`CostBreakdown` with explicit ABSENT status
- [x] `calc/labor.py` — 8.1 both formulations + consistency check
- [x] `calc/current_state.py` — 8.2
- [x] `calc/ai_state.py` — 8.3/8.4 per-task AI economics, HITL conversion,
      labor realization policy, AI annual operating cost
- [x] `calc/implementation.py` — 8.5 staged buy/build from the estimator's band
- [x] `calc/lifecycle.py` — 8.6/8.7 first-year, unit economics, savings, payback
- [x] `calc/benchmark_check.py` — 8.8 comparison only, never additive
- [x] `calc/sensitivity.py` — 8.10 recalculation interface
- [x] `calc/engine.py` — orchestrator

### Properties actually enforced (not just implemented)
- [x] **Two labor formulations compared, never merged** (8.1). The validation
      data diverges 51.5% and the engine reports it as a finding instead of
      averaging it away.
- [x] **Automation != headcount reduction** (8.3/8.4). `LaborRealization` is a
      required argument with no default. Same inputs: `cost_eliminated` gives
      +476,620 savings, `capacity_retained` gives -89,964 and no payback. Freed
      labor value is still quantified (566,585), just not banked.
- [x] **AI-assisted work is a productivity uplift, not work removal.** Residual
      time per unit = 1/(1+uplift), so assisted labor can never reach zero —
      the Brynjolfsson/Li/Raymond framing cited in 8.3. Assisted residual
      0.53-0.59 vs autonomous 0.12-0.30 on identical inputs.
- [x] **Absent != zero** (8.2). Uncollected components are ABSENT, excluded from
      totals, and listed — so the baseline is explicitly a floor.
- [x] **Payback suppressed when not real** (8.7). Three distinct outcomes:
      a figure, "no positive payback", and "range spans zero so both a ~16-month
      payback and no payback are admissible".
- [x] **Benchmarks compare, never add** (8.8). Verdict + citation, baseline total
      provably unchanged.
- [x] **Sensitivity recalculates but does not rank** (8.10), and says so.

### Defects found by the validation harness and fixed
- [x] The `automation_scale` sensitivity lever only reached
      `overall_automation` (which feeds the inference line), not the per-task
      estimates that actually drive residual labor. Sweeping automation moved
      first-year net benefit by 1,138 — 0.2%. Now 230,475.
- [x] Sub-month payback rendered as "0-0 months".

### Spec/doc changes
- [x] deployIQ_MVP.txt section 8 replaced (8.1-8.11), v5 revision entry added
- [x] 8.9 reconciles the vocabulary drift where draft text listed
      benchmark/evidence/llm_estimate/assumption as if separate from section 6's
      canonical five tags — they map onto the five, which stay canonical
- [x] Both cited studies verified rather than taken on trust: Brynjolfsson/Li/
      Raymond (NBER w31161; 5,179 agents, 14%, 34% for novices) with the note
      that the published QJE version says 15%; LCOAI (Curcio, arXiv:2509.02596)
      with the note that it is a recent single-author proposal and discounts to
      present value, which the MVP's one-year horizon does not
- [x] ARCHITECTURE 3.4 updated to the real module set
- [x] Two new OPEN ITEMS registered in the spec (8.2 uncollected cost
      components, 8.6 "valid output" definition)

---

## Corrective pass — E1-E11 resolved

Implemented per `docs/deployIQ_economic_engine_proposed_fix.md`.
Validated by `python3 scripts/economic_cases.py` (no API key).

| # | Fix | Where | Evidence |
|---|-----|-------|----------|
| E1 | Tooling / rework / other direct costs collected; rework derived from error rate x rework time | `calc/current_state.py` | baseline 347,200 -> 645,662 when supplied; 3 lines ABSENT when not |
| E2 | AI cost follows the SELECTED architecture; versioned pricing registry with real OpenAI + AWS prices | `lib/pricing.py`, `calc/inference.py` | customer support went from ABSENT (AI looked free) to a token-derived figure |
| E3 | Review derived per HITL mode as a calibrated RANGE; maintenance a range | `calc/calibration.py` | review 12,326 / 56,281 / 147,734 by architecture |
| E4 | Interval labelled an envelope; lineage retained on derived costs | `calc/models.py`, `inference_lineage` | — |
| E5 | Divergence CLASSIFIED; workforce is primary, task is secondary, neither auto-selected | `calc/labor.py` | 51% divergence now yields 347,200, not 715,385 |
| E6 | Current quality ABSENT not 100%; exception rate never renamed accuracy | `calc/quality.py` | 14% exceptions -> 86% non-exception rate |
| E7 | Sensitivity bounds from each input's own range | `calc/sensitivity.py` | automation swept 71-87%, effort 80-200 hrs |
| E8 | Geography controls the rate; unknown and India both UNRESOLVED | `calc/labor.py` | no silent US fallback |
| E9 | Unresolved share defaults rejected | `calc/engine.py` | 1/1/1 refuses instead of becoming 0.33/0.33/0.33 |
| E10 | One-year horizon retained and labelled | — | deferred deliberately |
| E11 | Reliability gap costed only when consequence is known | `calc/reliability.py` | ABSENT without rework time; 131,154/yr with it |
| D3 | Shared reconciliation with the estimator | `lib/reconciliation.py` | acceptance test N |

### The headline change
E5 halved the baseline. The engine previously took the task-based figure
whenever both existed; on the validation data that was 715,385 against a
workforce-based 347,200, and annual savings of ~476,000. Taking the workforce
formulation as primary gives savings of ~215,000. **The engine was overstating
the case for AI by roughly 2x on divergent inputs.**

### Real pricing now in the registry
`data/ai_pricing.json` carries live-fetched OpenAI list prices (gpt-5-mini at
$0.25/$2.00 per 1M tokens, gpt-5 at $1.25/$10.00) and AWS Textract page prices,
each with `pricing_id`, effective date, source URL and `last_verified`. Token
USAGE per unit remains an explicit assumption range and is swept in sensitivity
— the price is sourced, the usage is not, and the two are kept apart.

---

## Evidence integration pass — complete

Per `docs/deployIQ_economic_engine_next_steps.md`. Both supplied data files
were checked against primary sources before ingestion, not taken on trust.

### labor_rates.json — integrated as `data/labor_rates.json` v2
- **India now costs end-to-end** where it previously refused outright:
  8,809,478 INR current cost, 3,997,066 INR AI operating, in INR throughout.
- **Process and implementation labor are separate kinds.** Asking for
  `ai_ml_engineer` as PROCESS labor returns UNRESOLVED, and engineering cost
  can never be served from a support-agent rate.
- **Market compensation is never renamed fully-loaded.** The pipeline is
  compensation -> hourly -> explicit employer load -> fully loaded, and the
  multiplier's status (`unresolved`) travels into the output string.
- **No silent fallback**: unknown geography and unlisted geography (Germany)
  both resolve to UNRESOLVED.

Verification performed:
| Entry | Result |
|---|---|
| IN-CS-AGENT | **Retrieved and confirmed** — Payscale, median ₹247,821, n=67, page updated 2025-06-10. Recorded as `primary_document`. |
| IN-CS-SPECIALIST, IN-AIML-ENGINEER | Glassdoor blocks automated retrieval — `search_snippet` |
| IN-AP-CLERK | salaryexpert.com returns HTTP 403 — `search_snippet` |

One correction: the supplied file recorded the agent low bound as ₹176,000;
the page reads **₹171,000**. The registry uses the retrieved value and records
the discrepancy.

### compliance_attestations.json — integrated as two SEPARATE registers
Anthropic's certification list was **retrieved and matches the vendor page
exactly** (SOC 2 Type I & II, ISO 27001:2022, ISO/IEC 42001:2023, HIPAA-ready
with BAA). It is stored as `vendor_published_attestation` and is never
relabelled independent verification.

**But the file describes a different stack than this repository has.** It
states the MVP uses the Anthropic API (`lib/llm/anthropicClient.ts`) and Vercel
hosting for "a single Next.js app". This project uses **OpenAI**
(`llm/openai_client.py`, per ARCHITECTURE.txt) and **FastAPI with no frontend
and no hosting layer**. There is no TypeScript, Next.js or Vercel config in the
repo. Both attestations are therefore recorded with `in_current_stack: false`
and bind nothing.

More importantly, the two registers answer different questions:
- **product_vendor_attestations** — is deployIQ itself built on compliant
  services?
- **registry_implementation_attestations** — may a CUSTOMER's architecture be
  selected on compliance grounds? This is what the estimator gates on, and it
  is still **empty**.

An attestation for our own LLM vendor cannot qualify n8n for a customer's
HIPAA requirement, and `backs_implementation()` enforces that: an attestation
must exist, be bound to that implementation, and cover that standard.

### Currency consistency — a real bug the India work exposed
With India resolving in INR, the engine was adding **USD provider pricing to
an INR labor baseline**. Provider prices are now compared against the
baseline currency; on mismatch the inference line is ABSENT with the reason
stated, and no implicit FX conversion happens.

---

## FROZEN — finalization pass complete

`calc/` is frozen. Validated by `python3 scripts/economic_cases.py` and the
four sibling suites; 263 assertions pass in total.

### Completion criteria (spec section 16)

| | Criterion | Evidence |
|---|---|---|
| x | provenance survives end-to-end | `provenance_lineage` on EconomicResult |
| x | estimated != assumed | task_automation `['estimated']` vs maintenance `['assumed']`; a line fed by both keeps both |
| x | process vs implementation labor separated | `ai_ml_engineer` as PROCESS labor returns UNRESOLVED |
| x | India labor evidence integrated | India CS 5,572,560 INR; doc processing costs end-to-end |
| x | fully-loaded conversion explicit | multiplier status `unresolved`, travels into the output string |
| x | no silent geography fallback | unknown and unlisted both UNRESOLVED |
| x | currency mismatch handled safely | USD inference line ABSENT under an INR baseline |
| x | current quality ABSENT when unsupported | never 100%; exception rate never renamed accuracy |
| x | token price and usage separate | price `sourced`, usage `assumed`, lineage records both |
| x | calibration versioned and auditable | 13 parameters, all with id/version/unit/provenance/rationale/last_reviewed |
| x | stage allocation does not override estimator effort | partition sums to exactly 1.000000 |
| x | all economic regression tests pass | 5 suites green |
| x | compliance integration intact | `compliance_cases.py` unchanged and passing |
| x | no architectural boundary crossed | no LLM import, no scoring, no ranking, no recommendation in the engine |

### Fixed during finalization
- **Duplicate calculation path removed.** `calc/implementation.py` still held
  its own `STAGE_WEIGHTS` and `MAINTENANCE_SHARE_OF_BUILD = 0.15` while
  `calc/calibration.py` carried unused ranges for the same things. Only review
  effort had actually been wired to the calibration. Both now come from the
  single calibration object.
- **Stage allocation was losing effort.** The declared stage midpoints summed
  to 0.905, so partitioning quietly dropped ~9.5% of the build. Shares are now
  normalised to exactly 1.0, with a test asserting it.
- **Calibration records completed** with `unit` and `last_reviewed`, and
  `key` renamed to `calibration_id` per spec section 9. The employer-load
  multiplier now appears in the same audit surface even though it lives with
  the labor data.

---

## Remaining work — NOT part of the engine

These are evidence and product gaps, not engine defects. None reopens `calc/`.

**Evidence**
- [ ] No citable India employer-load multiplier; 1.3-1.6x stays an explicit
      versioned assumption.
- [ ] Three of four India rate entries are `search_snippet` (Glassdoor and
      salaryexpert block automated retrieval).
- [ ] No FX registry, so an INR assessment cannot include USD-priced
      inference. Requires `fx_rate_id`/source/effective date/provenance —
      never a hardcoded rate.
- [ ] No current-process quality data for either sector, so the quality
      comparison stays ABSENT.
- [ ] Token usage per unit remains an assumption with a stated rationale.

**Deferred by decision**
- [ ] Multi-year discounted model. Output is a FIRST-YEAR view and must never
      be described as lifetime ROI or a 3-year business case.
- [ ] Sensitivity-testing the stage allocation (spec calls this optional and
      says not to block on it).

**Next module**
Scoring System, then orchestration / live interviewer / UI / deployment. The
Scoring System consumes these outputs; it does not modify them.

## Boundary (do not cross)
The Economic Engine calculates consequences. It does not rank variables, score,
or recommend. Sensitivity provides recalculation; the Decision Driver module
(spec 9.5) ranks. Any change that puts a verdict, a score, or a variable
ranking in `calc/engine.py` or `calc/sensitivity.py` is a regression.
