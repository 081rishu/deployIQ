# Final Report (spec 13) — Audit, Critique & Proposed Architecture

Status: PROPOSAL. Nothing implemented. No frozen layer modified.

Audited against the code as of 2026-08-19: `schemas/assessment_state.py`,
`solution/*.py`, `calc/*.py`, `lib/*.py`, `api/*.py`, `deployIQ_MVP.txt` §13.

---

## 1. Current Section 13 assessment

§13 is 20 lines: a 14-item section list, a provenance rule, and a
"what the analysis could NOT do" rule. As a statement of intent it is
correct and unusually disciplined. As a build specification it is
incomplete in five specific ways:

1. **It names sections, not a data contract.** Nothing says what object the
   report consumes, what a "figure" is, or what a section does when its input
   is absent. Every hard problem in this layer is an absence problem.
2. **It assumes the happy path.** The pipeline has at least five terminal
   states that produce no primary solution or no economics at all
   (`estimator._refusal`, `compliance_gap`, `EconomicInputError` x3). §13 has
   no shape for them.
3. **It under-specifies the LLM boundary.** "Every number traces back to a
   calc module" constrains numbers. It does not constrain claims — and the
   Executive Summary's risk is claims, not numbers.
4. **It references a provenance capability the pipeline does not currently
   carry end-to-end** ("figures whose verification tier is below primary").
   See §2.A below — verification tier is lost at the first arithmetic op.
5. **Its ordering is self-contradictory.** "Leads with Decision Drivers" vs.
   a list whose item 1 is Executive Summary. Both are defensible; the spec
   must pick one.

Everything else in §13 is right and should survive unchanged.

---

## 2. Problems / risks found

Grouped by the audit categories requested. Each is anchored to code.

### A. Provenance loss (CONCRETE DEFECT)

`calc/models.py` `add/sub/mul/div/scale/complement` construct a new
`RangeEstimate` with `provenance=DERIVED`, a prose `source`, and **no
`source_id`**. `RangeEstimate.source_id` is the stable evidence key that
`lib/benchmarks.py` (`by_evidence_id`), `lib/pricing.py` (`record`),
`lib/labor_rates.py` and `lib/compliance.py` all key on.

Consequence: by the time a figure reaches `EconomicResult.current_annual_total`
or `first_year.annual_cost_savings`, the evidence ids of its inputs are gone.
The report therefore **cannot** satisfy §13's own requirement to flag
"figures whose verification tier is below primary (4.3)" for any derived
figure — which is every headline number.

Partial mitigations already in the engine: `provenance_lineage` (kinds only,
per named group), `inference_pricing_ids`, `inference_lineage`.
These cover provenance *kind*, not evidence *identity* or verification tier.

Fix (report layer only, no calc change): a deterministic **evidence index**
built by walking the source objects (`AssessmentState`, `SolutionEstimate`,
`EconomicResult` leaf `CostLine.amount`s, benchmark pack figures actually
loaded for the sector, calibration audit tables) and collecting every
`source_id` present on a *leaf* value. Derived figures then cite their
**input set**, not a single id: "derived from [ap_metrics_2025_ci],
[labor_us_ap_clerk], calibration v1". That is honest and buildable without
reopening `calc/`.

Second-best alternative (requires a calc change, therefore NOT proposed
now): have the interval ops union input `source_id`s into a new
`source_ids: list[str]` field. Cleaner, but reopens a frozen layer. Recorded
as an open decision (§18.2).

### B. Numerical inconsistency — two different sensitivity computations

There are **two** sweeps in the codebase and they are not the same:

| | `calc/driver_ranking.py` | `calc/sensitivity.py` |
|---|---|---|
| variables | automation, implementation effort, review fraction, labor rate, data readiness, integration complexity, data-coverage facts | automation, implementation effort, review fraction, labor rate |
| metric(s) | `annual_benefit`, `first_year_net_benefit`, `payback` (blended, weighted) | one metric, default `first_year_net_benefit` |
| output | elasticity + uncertainty index, ranked | absolute swing per variable, unranked |
| bounds | each variable's own bounds (`candidate_variables`) | each variable's own bounds (`build_variables`) |

They agree on bounds and disagree on everything else, by design. A report
that prints Decision Drivers (from the first) next to a Sensitivity section
(from the second) will show a reader two orderings of the same variables with
no explanation. **This is the single most likely place the report will look
wrong to a careful reader.**

Required: the Sensitivity section must state its metric explicitly, must be
labelled as *magnitude at bounds* (not importance), and must carry a one-line
statement that ordering by importance lives in Decision Drivers and uses a
different, deliberately different, measure.

Second inconsistency: `rank_drivers` re-runs the whole engine (1 + 2 per
variable). If the report separately calls `sensitivity.sweep`, the engine
runs again. Nothing pins one canonical `EconomicResult`. With identical
inputs the results are identical today, but the invariant is unenforced. The
report must consume **one frozen bundle**, never call an engine.

### C. Duplicate calculation surface

The report will be tempted to compute: percentages of totals ("labor is 72%
of current cost"), month/annum conversions, currency scaling (lakh/crore),
per-unit derivations, "X% cheaper". Each is arithmetic on frozen outputs.

Rule proposed: the report may perform **presentation arithmetic only**, from
an explicit whitelist (share-of-total, unit conversion of an existing
figure, rounding, currency formatting), each implemented once in
`report/format.py`, each carrying the derivation in the figure's own
`derivation` string. Anything not on the whitelist is a calc change and does
not belong here. Note `driver_ranking.py:270` already produces the
"labor represents the entire measured current cost" statement in code — the
report must reuse it, not recompute it.

### D. LLM hallucination opportunities

Today exactly one guard exists: `solution/alternatives.guard()` — it drops any
sentence containing a digit and any sentence matching `_DIRECTIVE`. It is the
right model and it is currently applied to one section only.

Unguarded surfaces the report introduces: executive summary prose, driver
phrasing, problem/process restatement, risk narrative, assumptions narrative,
validation-item phrasing. Each is an opportunity to (a) invent a figure,
(b) restate a figure wrongly, (c) assert causation, (d) assert a
recommendation, (e) assert an absent fact as present ("the team currently
spends nothing on tooling").

(e) is the subtle one and the guard does not catch it: a digit-free sentence
can still convert ABSENT into zero. Mitigation in §14.

### E. Recommendation / nudge leakage

Ranked places it will leak, most likely first:

1. **Executive summary verb choice.** "The proposed solution *delivers* a
   payback of 9-14 months" is a claim; "the model *calculates* 9-14 months
   under these assumptions" is a report. The whole section lives or dies on
   this.
2. **Composite Readiness Score in a summary position.** `calc/composite.py`
   explicitly says it "does not decide anything". Placed as a headline number
   next to a band word ("moderate-high"), it becomes a verdict regardless of
   the disclaimer under it.
3. **Alternatives ordering.** `AlternativesResult.ordering_basis` already
   says the order is not preference. Rendering it as a numbered list 1/2/3
   under the primary solution re-creates the ranking the module refused to
   make.
4. **Section 9 "Expected Benefits".** The spec's own section title is
   asymmetric — there is no "Expected Costs" section at the same level
   (8 is AI Operating Cost, which is not the same framing). Recommend the
   section render as a two-sided "Modelled Economic Change" while keeping the
   spec's title, and lead with the savings range *including* its negative
   bound when `sub()` straddles zero.
5. **Confidence adjacency.** Placing "Assessment confidence: High" beside a
   strong economic score reads as endorsement. `calc/assessment_confidence.py`
   already states confidence != score magnitude; the report must state it too,
   in the same visual block, not in a footnote.

### F. Missing uncertainty

`calc/models.py` documents that interval arithmetic gives a **guaranteed
envelope, not a confidence interval** — inputs are assumed to move together,
so the width is the widest defensible answer. A report that prints
"₹4.2-6.8L" with no label will be read as a confidence interval by every
business reader.

Required: every range rendered carries a range-semantics label, once per
report and once per headline figure — "bounds, not a confidence interval;
inputs are assumed to move together, so this is the widest defensible span".

Also missing today unless made explicit: `Score.bounds_type`
(`NUMERIC_INPUT_ENVELOPE` vs `SCENARIO_ENVELOPE`), `inputs_held_fixed`.
A score band computed while categorical inputs were held fixed looks more
certain than it is; the field exists precisely to say so and must be rendered.

### G. Absent presented as zero

The engine is rigorous here (`LineStatus.ABSENT`,
`CostBreakdown.completeness_note()`, `absent_components`,
`Score.not_computable`, `payback_months=None` with three distinct statements,
`UnitEconomics.note`, `quality_comparison.comparable=False`). **The risk is
entirely in the presentation layer**: a table renderer that prints `—` or
blank, or worse `0`, and a total row that reads as complete.

Required, structurally: the report's figure primitive has a three-state
status (`known` / `absent` / `not_computable`), and the renderer is
type-driven — an absent figure cannot render as a number because it carries
no number. Every total that has absent siblings renders with the word
"floor" or "partial" adjacent, sourced from `completeness_note()`.

### H. Scores as decisions

Handled by: excluding Composite from the summary layer (proposed), rendering
every score with its `missing_inputs`, `flags`, `bounds_type` and calibration
version, and by the ordering rule in §4 (drivers precede scores).

`calc/economic_score.py` sets `sanity.presentable_as_strong=False` and
prefixes the note with `SANITY WARNING/INVALID`. **The report must gate on
this flag**: an economics figure or score flagged not-presentable-as-strong
may not appear in the summary layer without its flag text in the same block.
Nothing in §13 currently says this.

### I. Decision Drivers reinterpreted

`DriverImpact.statement` is generated in code (`driver_ranking.py:223-311`).
The LLM's permitted role is *rephrasing that sentence*. Risks: reordering,
merging two drivers into one sentence, adding a causal link between them,
dropping the provenance clause. Mitigation: fragment-level generation, one
driver at a time, with the code statement as the only input, plus a
similarity/containment check (§14).

Note also `DriverType.DATA_COVERAGE` drivers get `impact=0.0` by construction
and are appended *after* the ranked list (`ranked[:top_n] + coverage[:1]`).
The report must not present them as the lowest-impact driver — they are a
different kind of statement (that is what `DriverType` exists for) and need a
visually separate treatment.

### J. Alternatives as recommendations

`AlternativesResult` already carries `is_recommendation=False` and
`economics_included=False` as constants specifically so a downstream layer
cannot misframe it. The report must render both as visible statements, must
render `statement` when the list is empty, and must surface
`categories_not_in_registry` as a registry gap (not as "that approach doesn't
apply"). `rejected` belongs in the audit appendix, not the business layer.

### K. Missing evidence hidden

Sources that must reach the report and have no channel today:
`BenchmarkPack.health()` (per-sector sourced/assumed/primary-verified counts),
§4.4's explicit statement that customer_support is materially weaker than
document_processing, `solution.compliance_verdicts` / `compliance_exclusions`,
`reference_comparison.unevaluated_conditions`,
`estimate.provenance_warnings`, `result.warnings`,
`alternatives.llm_guard_notes`.

Several of these are *findings*, not footnotes. A customer-support assessment
whose pack has 2 sourced figures of 8, 0 primary-verified, must say so above
the fold, not in an appendix.

### L. Unsupported executive summary claims

The specific failure modes, all of which a competent LLM will produce
unprompted: totalling absent components; describing a first-year view as ROI
or a business case (explicitly forbidden in `docs/economic_engine_todo.md`);
describing the unit-cost normalisation as LCOAI (explicitly forbidden in
MVP §8.6); attributing the 14%/15% Brynjolfsson figure to this company
(forbidden in §8.3/8.9); calling the automation range an expected outcome;
implying headcount reduction under `CAPACITY_RETAINED`.

### M. Sections requiring data that is not available

| §13 section | Availability | Gap |
|---|---|---|
| 3 Current Process | `state.process` free text only | No structured process model exists; task decomposition lives in `SolutionEstimate.task_automation`, which is the *AI-state* view. Do not present it as the documented current process. |
| 4 Current Cost | good | Currency (below). Three of four components ABSENT by default. |
| 7 Implementation Reqs | `implementation.CostBreakdown` + stage partition (calibration) | `state.process_stages` is collected by nobody; stages are calibration-partitioned, which must be disclosed as an assumption, not a plan. |
| 9 Expected Benefits | cost savings only | Productivity/quality/capacity are explicitly excluded (§8.7). Must say so in-section. |
| 12 External Sources | packs + pricing + labor rates + compliance | No federated resolver exists across the four registries. Report-layer plumbing needed. |
| 13 Sensitivity | `SensitivityReport` | No threshold/crossing form (§11 here). |

**Currency (CONCRETE DEFECT).** Money is rendered by the report, and there is
no authoritative currency on the result: `EconomicResult` carries
`labor_rate_geography` but not currency; `LaborRate.currency` is `None`
exactly when the user supplied their own fully-loaded cost;
`AssessmentState.currency` derives from a 5-entry `GEOGRAPHY_CURRENCY` map and
is `None` for any other geography; `calc/inference.py` can raise a
`currency_mismatch` warning meaning the inference line was **excluded**, so
the AI operating total is a floor in a *different* way than the current-cost
total. The report needs an explicit, deterministic currency resolution rule
and an "unresolved currency" rendering. It must not adopt the brief's
`₹4.2-6.8L` lakh formatting for a non-India assessment.

### N. Sensitivity that cannot be honestly explained

`VariableImpact.direction` is computed as `hi > lo` on the *metric*, not on
the input — for `implementation_scale`, more effort lowers net benefit, so
`direction="decreases"` describes the metric, not the variable. Rendered as
"Implementation effort: decreases" it is ambiguous. The report must render
"metric moves from A to B as [variable] moves across [bounds]", never a bare
direction word.

`failed` impacts (an `EconomicInputError` inside a sweep) currently render
`swing=0.0` with `direction="not computable"`. A renderer sorting by swing
will bury them. They must render as an explicit "could not be evaluated"
row with the reason.

`report.skipped` entries ("no defensible range — not swept rather than
assigned an invented one") are a *virtue* of the system and should be shown,
not dropped.

### O. Ordering / UX

The §13 list mixes three audiences in one sequence: decision (1, 6, 14),
analysis (4, 5, 7, 8, 9, 10, 13), audit (11, 12). Reading top to bottom, a
business user hits the AI operating cost breakdown before they learn what the
biggest uncertainty is. Proposal in §4 keeps all 14 sections and all their
titles, but groups them into three layers with an explicit boundary.

### P. Excessive technical detail

Things that must **not** appear in the business layer: elasticity values,
`uncertainty_index`, `per_quantity` dicts, `bounds_type` enum names, pattern
and implementation ids, capability enum values, calibration parameter tables,
`ranking_score`, rejected alternatives, `provenance_lineage` dicts. All of
them belong in the audit appendix, which should be complete and unapologetic.

### Q. Auditability

Nothing today would let a second person reproduce a report. Required: a
**report manifest** — sector pack version, `CALIBRATION_VERSION`,
`SCORING_CALIBRATION_VERSION`, solution calibration version, registry
pattern/implementation ids and `last_reviewed`, LLM model id, labor
realization policy, and the full figure ledger (every rendered figure ->
its id, provenance, source ids, derivation). Plus every guard action taken
on LLM output.

### R. API / orchestration

There is no orchestrator. `api/main.py` exposes interview + voice only.
`README.md:114` lists `api/ai_solution.py` and `api/report.py` as future.
`ARCHITECTURE.txt` 3.6 already specifies the report endpoint correctly
("inserts calculated numbers directly into the template rather than letting
the LLM restate them").

Also unresolved: `LaborRealization` is a **required argument with no default**
(deliberately, per §8.4) and no screen collects it. The report's headline
number is a function of it. See §18.3.

### S. Whole-report LLM vs constrained fragments

**Constrained fragments. Not negotiable, for three reasons:**
(1) a whole-report generation cannot be guarded — you can verify a sentence
contains no digit, you cannot verify a 2,000-word document asserts nothing
unsupported; (2) provenance tags must be attached to figures at render time,
which requires the renderer to own the figure, not the model; (3)
auditability requires the deterministic part of the report to be byte-stable
across runs, which whole-document generation destroys.

---

## 3. What should remain unchanged

- All 14 section titles and their content intent.
- "Every figure visibly tagged with its provenance" as the core trust rule.
- The five-tag vocabulary. No sixth tag. `verification` stays a separate axis
  (it already is, in `lib/benchmarks.py`), never a provenance value.
- "The report must carry what the analysis could NOT do."
- Decision Drivers lead the substance.
- No recommendation, no verdict, no threshold-derived category.
- Alternatives are informational; no per-alternative economics.
- Every frozen layer. This proposal changes no file under `calc/`,
  `solution/`, `schemas/` or `lib/`.

---

## 4. Proposed report architecture

    [orchestrator]  runs pipeline once, freezes one bundle
            |
            v
    ReportInput (frozen, immutable)
            |
      +-----+---------------------------+
      |                                 |
    assemble.py                    evidence.py
    (deterministic, no LLM)      (federated id -> citation
      |                            + verification resolver)
      v
    ReportModel  <-- typed figures, statements, absences, manifest
      |
      +--> validate.py   (guardrails; fails closed)
      |
      +--> narrate.py    (constrained LLM fragments; guarded; optional)
      |
      +--> render_markdown.py / render_html.py / JSON

Five rules the architecture enforces structurally:

1. **`assemble.py` never imports `llm/` and never imports an engine.** It
   takes already-computed objects. (Mirrors how `calc/` was kept LLM-free.)
2. **`narrate.py` never sees a number.** Fragments are generated from the
   code-written statement text and categorical context only; figures are
   injected by the renderer afterwards. A model that never sees a figure
   cannot restate it wrongly.
3. **The `ReportModel` is complete and renderable with zero LLM output.** The
   narrative is an enhancement layer. If the LLM is unavailable, the report
   still ships, with code statements — exactly the pattern
   `solution/alternatives.py` already uses.
4. **Absence is a type, not a formatting case.**
5. **Nothing in the report layer may import `calc.engine.run`,
   `driver_ranking.rank_drivers`, `sensitivity.sweep` or
   `solution.estimator.estimate`.** Enforceable by an import test.

### Report layers (keeps all §13 sections, fixes ordering)

**Layer 1 — Decision (business reader, ~1 page)**
- §1 Executive Summary (constrained; see §9)
- Decision Drivers + uncertainty callout  *(leads the substance)*
- §14 What to Validate Next
- Assessment confidence + what the analysis could not do

**Layer 2 — Analysis**
- §2 Problem Definition · §3 Current Process · §4 Current Cost
- §5 Proposed AI Solution · §7 Implementation Reqs · §8 AI Operating Cost
- §9 Expected Benefits · §10 Risks and Reliability
- §6 Alternative Solutions · §13 Sensitivity Analysis
- Scores (economic / feasibility / risk), presented as indicators

**Layer 3 — Audit appendix**
- §11 Assumptions (calibration audit tables, all three registries)
- §12 External Sources (evidence ledger + pack health + §4.4 caveat)
- Manifest, figure ledger, guard log, rejected alternatives, warnings

This resolves the §13 ordering contradiction: the Executive Summary keeps
position 1 as a *frame* ("what was assessed, what was found, what is
uncertain"), and Decision Drivers are the first *substantive* content.

---

## 5. Report data contract / schema

Proposed `report/schema.py` (new file, additive only).

```python
class FigureStatus(str, Enum):
    KNOWN = "known"
    ABSENT = "absent"              # never collected -> excluded from totals
    NOT_COMPUTABLE = "not_computable"   # inputs missing -> named

class RangeSemantics(str, Enum):
    ENVELOPE = "envelope"          # interval arithmetic, inputs move together
    SCENARIO = "scenario"          # discrete scenario bounds
    POINT = "point"                # min == max
    CATEGORY = "category"

class Figure(BaseModel):
    key: str
    label: str
    status: FigureStatus
    value_min: Optional[float]
    value_max: Optional[float]
    unit: str                      # currency | percent | months | hours | count
    currency: Optional[str]        # None -> renders "currency unresolved"
    provenance: Optional[Provenance]          # the five tags, only these
    provenance_mix: list[Provenance]          # for derived figures
    confidence: Optional[str]
    range_semantics: RangeSemantics
    source_ids: list[str]                     # leaf evidence ids
    citations: list[Citation]                 # resolved, with verification
    derivation: str                           # the calc `source` string, verbatim
    absence_reason: str                       # required when not KNOWN
    flags: list[str]                          # sanity / blocker / warning
    origin_module: str                        # calc.lifecycle, solution.scope, ...

class Citation(BaseModel):
    evidence_id: str
    source: str
    source_url: Optional[str]
    as_of: str
    geography: str
    verification: Literal["primary_document","search_snippet","unverified"]
    provenance: Provenance
    registry: Literal["benchmark","pricing","labor_rate","compliance","calibration"]

class Statement(BaseModel):
    text: str
    origin: Literal["code","llm"]
    source_statement: Optional[str]   # the code text an LLM fragment rephrases
    guard_notes: list[str]

class Gap(BaseModel):
    kind: Literal["absent_cost","unevaluated_condition","not_computable_score",
                  "below_primary_verification","unresolved_field",
                  "registry_gap","excluded_component"]
    label: str
    detail: str
    consequence: str        # what this does to the numbers, in words

class ReportSection(BaseModel):
    key: str; number: int; title: str; layer: Literal[1,2,3]
    statements: list[Statement]
    figures: list[Figure]
    tables: list[ReportTable]
    gaps: list[Gap]
    notes: list[str]

class ReportManifest(BaseModel):
    generated_at: str
    sector: Sector
    pack_version: str
    pack_health: dict[str, int]
    economic_calibration_version: int
    scoring_calibration_version: int
    solution_calibration_version: int
    registry_pattern_id: str
    registry_implementation_id: str
    registry_last_reviewed: str
    labor_realization: LaborRealization
    labor_realization_source: Literal["user","unset"]
    currency: Optional[str]
    currency_basis: str
    llm_model: Optional[str]
    llm_used_for: list[str]
    guard_actions: list[str]
    figure_ledger: list[Figure]

class Report(BaseModel):
    mode: Literal["full","partial","refused"]
    refusal_reason: str = ""
    sections: list[ReportSection]
    manifest: ReportManifest
```

`ReportInput` is the frozen bundle: `AssessmentState`, `SolutionEstimate`,
`EconomicResult`, `ScoreBundle`, `DecisionDrivers`, `AlternativesResult`,
`SensitivityReport`, `AssessmentConfidence`, `LaborRealization`.

**Three report modes**, because the pipeline has terminal states:

- `refused` — `SolutionEstimate.recommended_pattern == ""` (missing critical
  fields, capability decomposition failure, no covering pattern) or
  `compliance_gap=True`. Renders §2, §3, §4 (if economics ran), the gap, the
  exclusions, and §14. **No proposed solution section, no benefits section.**
- `partial` — economics raised `EconomicInputError`, or scores are
  not-computable. Renders everything available, with the non-computable
  sections present and explicitly empty.
- `full`.

---

## 6. Deterministic vs LLM responsibilities

**Deterministic (all of it):** section presence and order; every figure and
its provenance, citations, verification, confidence, range semantics; every
total and completeness note; all gaps; driver order and driver facts; score
values, bands, missing inputs, flags; sensitivity rows; alternatives content
and order; validation-item selection and order; the manifest; every fallback
sentence when the LLM is absent.

**LLM (fragments only, each independently guarded):**

| Fragment | Input given | Max | Must not |
|---|---|---|---|
| driver phrasing | one code `statement`, one at a time | 1 sentence | add facts, merge drivers, drop qualifiers |
| exec summary narrative | code-written skeleton sentences | 5 sentences | contain a digit; use a decision verb |
| problem/process restatement | `state.problem`, `state.process` | 3 sentences | add scope not stated |
| solution explanation | `fit_explanations`, pattern name/architecture | 3 sentences | assert performance |
| alternatives explanation | already implemented, `alternatives.guard()` | 700 chars | (as built) |
| risk narrative | `risk_controls` (category + control text) | 4 sentences | quantify |
| validation-item phrasing | one deterministic item at a time | 1 sentence | add items |

**The LLM never sees a numeric figure in any prompt** for the report layer.
(This is stricter than `alternatives._explain`, which passes strengths and
limitations text only — consistent with it.)

---

## 7. Provenance design

- Vocabulary unchanged: `user_provided | sourced | estimated | assumed |
  derived`. No new tag.
- Rendering: `LABEL · origin · confidence`, e.g.
  `ESTIMATED · Solution Estimator · medium confidence`,
  `DERIVED · Economic Engine · from user-provided volume + sourced AP rate`.
- **A `derived` figure additionally shows its provenance mix**, because
  `derived` alone hides whether it came from measured facts or assumptions.
  `EconomicResult.provenance_lineage` supplies this for the groups it covers;
  the evidence index supplies the rest.
- **Verification tier is rendered wherever a `sourced` citation appears**, and
  a figure whose citation set contains anything below `primary_document` is
  listed in the §12 gap list (this is §13's stated requirement, now
  satisfiable via the evidence index).
- `assumed` figures link to the calibration audit row that defines them
  (`calc.calibration.audit_table()`, `calc.scoring_calibration.audit_table()`,
  `solution.calibration.all_calibration_params()`), including version and
  rationale.
- Provenance is never inferred by the report. If a figure arrives without a
  tag, the report renders `provenance unknown` and files a Gap — it does not
  guess.

---

## 8. Uncertainty / missing-data presentation

Five distinct things, five distinct renderings — never collapsed:

| Meaning | Source | Rendering |
|---|---|---|
| Known range | `RangeEstimate` min<max | `4.2M – 6.8M` + semantics label |
| Point value | min==max | single figure, no fake spread |
| Absent | `LineStatus.ABSENT` | `not collected` + why + "total is a floor" |
| Not computable | `Score.computable=False` | `not computable` + named missing inputs |
| Undefined | `payback_months=None` | the engine's own `payback_statement`, verbatim |

Additional required renderings:
- range semantics disclaimer, once per report and on each headline figure;
- `Score.bounds_type` + `inputs_held_fixed` beside every score band;
- a **"What this assessment could not establish"** block in Layer 1,
  assembled from all `Gap`s, deduplicated, ordered by consequence;
- currency unresolved renders as a named gap, not a silent symbol choice.

---

## 9. Executive Summary design

Highest-risk section. Design it as a **slot-filled template**, not a prompt.

Fixed slots, in order, each deterministically populated:

1. **What was assessed** — sector, process (user's words), volume/headcount as
   user-provided figures, geography. Provenance: `user_provided`.
2. **What the analysis produced** — the primary pattern/architecture name, the
   automation range with confidence, and the sentence "this is the
   architecture the registry selected under the stated constraints; it is not
   a recommendation to build it."
3. **What matters most here** — the top 3 `DriverImpact.statement`s, verbatim
   or LLM-rephrased, in the module's own order.
4. **Modelled economics** — current annual cost (with "floor" qualifier when
   components are absent), AI annual operating cost, annual cost-savings
   range including a negative bound if it straddles zero, and the engine's own
   `payback_statement` verbatim. Plus, verbatim: "cost savings only;
   productivity, revenue, quality and capacity benefits are not included",
   and "first-year view; not ROI, not a multi-year business case."
5. **Labor realization policy** — which policy was applied and the engine's
   `realization_statement`, verbatim.
6. **Confidence** — level + 2-3 reasons from `AssessmentConfidence.reasons`,
   plus the fixed sentence "confidence describes how well-grounded the
   analysis is, not whether the opportunity is good."
7. **Biggest uncertainty** — `uncertainty_statement`, verbatim.
8. **Constraints and blockers** — compliance blockers, sanity flags, currency
   gaps, pack-weakness caveat (§4.4) when sector is customer_support.

Hard rules:
- No composite score in this section. (Recommendation; see §18.5.)
- No score value in this section at all — scores are Layer 2.
- Every figure carries its provenance chip inline.
- Banned verb/phrase list enforced by `validate.py`, not by prompt: recommend,
  should, best, viable, opportunity, ROI, proven, will deliver, will reduce,
  pilot, go/no-go, worth it, justified, compelling.
- Required-phrase list: the report is refused if slots 4, 5, 6 lack their
  fixed qualifier sentences.
- The LLM's only role: rewriting slots 1-3 and 8 into flowing prose, with no
  digits, from code text. Slots 4, 5, 7 are verbatim engine output.

---

## 10. Section-by-section design

**§2 Problem Definition** — `state.problem` (user_provided, quoted),
LLM restatement optional. Gaps: unresolved fields from `field_resolution`.

**§3 Current Process** — `state.process` quoted; volume, handling time,
headcount, fraction of time, role (canonical + user's words), quality metric
*by name* with value or explicit absence. Explicitly **not** the AI task
decomposition. Include `labor_consistency` verdict when both formulations
existed — divergence is a finding (§8.1) and belongs here, not in an appendix.

**§4 Current Cost** — the `CostBreakdown` table with ABSENT rows visible;
total labelled with `completeness_note()`; `baseline_basis`; the benchmark
cross-check rendered in a **visually separate** block with the fixed sentence
"benchmarks compare, they are never added". Currency chip on every row.

**§5 Proposed AI Solution** — pattern/architecture (registry), per-task
automation table (task, capability, range, confidence, HITL mode,
workload share + its provenance), overall automation, performance metrics with
citations, `effort_basis`, `integration_basis` (including the
"interview recorded X, derived band is Y" disclosure), reference comparison
(match/alignment/active deviations) and — required by §13 —
`unevaluated_conditions` as gaps. Plus `provenance_warnings` and
`time_reconciliation`.

**§6 Alternative Solutions** — see §12 below.

**§7 Implementation Reqs** — stage table from `implementation.CostBreakdown`
with buy/build and provenance; effort band + hours + rate as three separate
provenance-tagged figures (§7.4); the fixed disclosure that stage partition is
a calibration assumption with its version, not a project plan.

**§8 AI Operating Cost** — `ai_operating` breakdown with ABSENT rows;
inference line with `inference_pricing_ids` citations and token-usage
assumption; maintenance as calibration; **currency-mismatch exclusion
rendered as a gap with its consequence** ("inference excluded — this total is
a floor").

**§9 Expected Benefits** — annual cost savings (may straddle zero, shown as
such), first-year net benefit, monthly net benefit, payback statement
verbatim, unit economics on valid output with its note, freed-capacity value
under `CAPACITY_RETAINED` labelled explicitly as capacity and **never** in a
savings row, quality comparison or its absence. Fixed exclusion sentence.

**§10 Risks and Reliability** — `risk_controls` grouped by category, risk
score with sub-scores and flags, compliance blocker rendered first and
un-averaged, `reliability` consequence block, reliability-gap flag,
`key_uncertainties`.

**§11 Assumptions** — three calibration audit tables (economic, scoring,
solution/scope) with versions and rationales; every `assumed` figure used;
labor-rate basis statement; the fixed statement that none of these are
empirical industry data.

**§12 External Sources** — evidence ledger from the resolver, grouped by
registry, with verification tier; `BenchmarkPack.health()`; the §4.4
sector-strength caveat verbatim when applicable; a list of every figure whose
verification is below `primary_document`.

**§13 Sensitivity** — see §11 below.

**§14 What to Validate Next** — see §13 below.

---

## 11. Sensitivity presentation

Render `SensitivityReport` as: metric name and baseline stated up front; one
row per variable — *bounds in the variable's own units* (`VariableImpact.bounds`
already carries e.g. "71-87% (estimator range)"), metric at low bound, metric
at high bound, swing, provenance, source; `skipped` rows shown with their
reason; `failed` rows shown as "could not be evaluated" with the reason.

Fixed framing sentences (deterministic):
- "Bounds are each input's own range, not a uniform perturbation."
  (`SensitivityReport.note`, verbatim)
- "This section shows how much each input moves the number. Which inputs
  matter most is a different question, answered by Decision Drivers using a
  different measure — the two orderings are not expected to match."

Direction is never rendered as a bare word (§2.N).

**Threshold / crossing form (§12 of the MVP, still open).** Recommended
design if approved — and it is **new deterministic calculation, so it belongs
in `calc/`, not in the report layer**, which means it is a scoped exception to
the freeze and needs your approval (§18.2):

- inputs: one existing `Overrides` lever, one existing metric, one reference
  point (12 months payback; zero for savings and first-year net benefit);
- method: evaluate the metric across the variable's **own declared bounds**
  (no extrapolation beyond them, ever); if the metric does not cross the
  reference point within those bounds, report **no crossing** and stop;
  if it crosses, confirm monotonicity on a fixed sample grid, then bisect to
  a fixed tolerance; report the crossing as a **band**, not a point;
- output: `Crossing{variable, metric, reference_point, low, high, monotonic,
  within_bounds, statement}`;
- phrasing, fixed in code: *"At approximately 68-72% automation, the
  calculated payback crosses the 12-month reference point."* Never "the
  decision changes", never "becomes viable", never "the threshold for
  approval".
- refuse to report when: non-monotonic on the grid, no crossing inside the
  variable's own bounds, the metric is undefined at either bound, or the
  underlying figure is flagged by `economic_sanity`.

---

## 12. Alternatives presentation

Consume `AlternativesResult` unchanged. Render per alternative: name,
approach, `difference_from_primary` + `difference_kind` (as words), strengths,
limitations, implementation complexity band + basis, registry/benchmark-backed
performance metrics with citations, human involvement + basis, risks,
`when_preferable` (or an explicit "no deterministic condition favouring this
alternative was identified" when empty), uncertainties, guarded explanation.

Fixed framing, rendered visibly, not as metadata:
- `ordering_basis` verbatim — order is not preference;
- "no economics were modelled for alternatives" (`economics_included=False`);
- "this section is informational, not a recommendation"
  (`is_recommendation=False`);
- `statement` alone when the list is empty;
- `categories_not_in_registry` rendered as *registry coverage gaps*.

Presentation rules: no numbered list, no ordering language, no comparison
table with a "winner" column, no side-by-side cost column (there is no cost).
`rejected` and `ranking_score` go to Layer 3 only. `llm_guard_notes` go to the
guard log.

---

## 13. What to Validate Next — deterministic generation

Candidate items, each derived from a specific upstream output — nothing
invented:

| Trigger | Item |
|---|---|
| `DecisionDrivers.uncertainty_callout` | measure/narrow that variable |
| Top drivers with `provenance in (estimated, assumed)` | validate that input |
| `CostLine.status == ABSENT` | collect that cost component |
| `Score.missing_inputs` | supply that input |
| `field_resolution` status != RESOLVED, or `attempts >= MAX` | confirm that fact |
| `SolutionEstimate.needs_more_information` | resolve it |
| `reference_comparison.unevaluated_conditions` | evaluate that condition |
| `labor_consistency.status == DIVERGENT` | reconcile the two labor views |
| citations below `primary_document` | obtain the primary source |
| `quality_comparison.comparable == False` | measure current-process quality |
| `compliance_verdicts` unknown/unsupported | obtain the attestation |
| `LaborRealization` unset or unconfirmed | confirm the capacity policy |

**Ranking (deterministic, no new formula):** items inherit the `impact` of the
driver variable they would resolve, where one exists; items with no
corresponding driver rank below those that have one; ties break by gap kind in
a fixed order (blocker > absent cost > unresolved field > evidence tier).
Cap at 5-7 in Layer 1; the rest go to Layer 3.

Each item carries: what to measure, why it matters (the consequence, in the
words of the upstream module), and what it would change. LLM may rephrase one
item at a time and may not add, merge, reorder or drop items.

---

## 14. Report validation / guardrails

`report/validate.py`, run before render, **fails closed**.

Structural:
1. every `Figure` with `status != KNOWN` has a non-empty `absence_reason`
   and no numeric value;
2. every `KNOWN` figure has a provenance tag and a non-empty `derivation`;
3. every money figure has a currency, or a currency gap is filed;
4. no total is rendered whose breakdown has absent lines without a
   completeness note;
5. every `sourced` citation resolves in a registry; unresolvable ids fail;
6. section order and layer assignment match the spec list;
7. mode consistency: `refused` reports contain no solution/benefits section.

Anti-recommendation:
8. banned-phrase scan across **all** rendered prose (extend
   `alternatives._DIRECTIVE` with the exec-summary list from §9);
9. no digit in any `Statement` with `origin == "llm"` (reuse
   `alternatives._DIGIT`);
10. every LLM driver fragment must preserve the code statement's key tokens
    (numbers are absent by construction; check the label term and any
    qualifier word such as "estimated", "reported", "assumed" survives) —
    otherwise fall back to the code statement;
11. no comparative superlative in alternatives prose;
12. composite score absent from Layer 1.

Absence-integrity (catches §2.D(e)):
13. no rendered sentence may assert a value for a figure whose status is
    `ABSENT` — enforced by only ever rendering figures through the figure
    renderer, plus a scan for the absent figure's label appearing in LLM prose;
14. required qualifier sentences present (cost-savings-only, first-year-only,
    benchmarks-not-added, confidence-not-quality, ordering-not-preference,
    range-semantics).

Sanity gating:
15. any figure or score flagged `presentable_as_strong=False` carries its
    flag text in the same block;
16. compliance blockers appear in Layer 1 regardless of any score.

Every violation is either a hard failure (report not emitted) or a downgrade
to the deterministic fallback for that fragment. Never a silent fix.

---

## 15. API / orchestration implications

**New: `pipeline/orchestrate.py`** (or `api/analysis.py`) — the missing piece.
Runs, once, in order: `estimator.estimate` → `driver_ranking.rank_drivers`
(which internally runs the engine and all scores) → `alternatives.derive` →
`sensitivity.sweep` → assembles `ReportInput`. Rules:
- exactly one `LaborRealization` for the whole run, recorded in the manifest;
- `EconomicInputError` and estimator refusals are caught and converted into
  report `mode`, never into a fabricated result;
- the `EconomicResult` inside `DecisionDrivers.scores.result` is *the*
  canonical result; nothing recomputes one.

**Endpoints** (ARCHITECTURE 3.2/3.6):
- `POST /api/solution` → `SolutionEstimate`
- `POST /api/analysis` → `ReportInput` (state + estimate + economics + scores
  + drivers + alternatives + sensitivity)
- `POST /api/report` → `Report` (JSON), with `?format=markdown|html`
- report generation is idempotent given `ReportInput` **for the deterministic
  half**; the narrative half is regenerable and cached in the response.

State stays client-carried (no DB), so `ReportInput` must round-trip through
JSON — every type in it is already Pydantic, except `DriverVariable`'s
callables, which do not appear in `DecisionDrivers` output. Verify on build.

Cost/latency: one LLM call per report (batched fragments), not one per
section.

---

## 16. Acceptance tests — `scripts/report_cases.py`

Same convention as the existing suites: no API key, LLM stubbed, deterministic.

1. absent cost component renders as absent, never `0`, and the total carries
   "floor";
2. not-computable score renders with named missing inputs, never `0`;
3. `payback_months=None` renders the engine's statement verbatim, all three
   variants;
4. savings range straddling zero renders both bounds, unclipped;
5. `CAPACITY_RETAINED` report contains no savings claim and shows freed
   capacity as capacity;
6. benchmark cross-check never appears inside a total;
7. compliance blocker appears in Layer 1 even with economic score > 95;
8. `compliance_gap` estimate produces a `refused` report with no solution and
   no benefits section;
9. `EconomicInputError` produces a `partial` report, not an exception;
10. driver order in the report == `DecisionDrivers.drivers` order;
11. LLM fragment containing a digit is dropped and falls back to the code
    statement;
12. LLM fragment containing a banned phrase is dropped;
13. every rendered money figure has a currency, or a currency gap exists;
14. every `sourced` citation resolves in a registry;
15. figures below `primary_document` verification appear in the §12 gap list;
16. customer_support report carries the §4.4 pack-weakness caveat;
17. alternatives section contains `is_recommendation=False` framing, no
    ordering language, no numbered list;
18. empty alternatives renders `statement`, not an empty section;
19. exec summary contains no score value and no composite;
20. required qualifier sentences all present;
21. report with the LLM entirely unavailable is complete and valid;
22. deterministic half is byte-identical across two runs on one `ReportInput`;
23. no report module imports `calc.engine`, `driver_ranking`, `sensitivity`
    or `solution.estimator` (import-graph assertion);
24. manifest records all four version numbers and the realization policy;
25. sensitivity `skipped` and `failed` rows are rendered, not dropped.

---

## 17. Implementation phases

**P0 — spec reconciliation (no code).** Fix the §5 field list, §11 status,
README:113, and the §13 ordering contradiction. Decide §18 items.

**P1 — schema + evidence resolver.** `report/schema.py`,
`report/evidence.py` (federates benchmarks / pricing / labor rates /
compliance / calibration → `Citation`). Tests 14, 15.

**P2 — deterministic assembly.** `report/assemble.py`, all 14 sections,
zero LLM. Tests 1-10, 13, 16-20, 22-25.

**P3 — validation.** `report/validate.py`. Tests 11-13, 20, 23.

**P4 — constrained narrative.** `report/narrate.py`, one batched LLM call,
guard reused from the alternatives pattern. Tests 11, 12, 21.

**P5 — renderers.** Markdown first (print/save-as-PDF per §18 of the MVP),
HTML second.

**P6 — orchestration + endpoints.** `pipeline/orchestrate.py`,
`api/solution.py`, `api/analysis.py`, `api/report.py`.

**P7 — (only if approved) threshold crossings** in `calc/sensitivity.py`,
plus its own acceptance cases.

---

## 18. Decisions — APPROVED 2026-08-19

All ten are resolved. The design above stands as approved, with these bindings:

1. **Ordering** — Executive Summary stays layer-1 framing; Decision Drivers are
   the first substantive content immediately after it.
2. **Threshold crossings** — DEFERRED. `calc/sensitivity.py` is not modified and
   `calc/thresholds.py` is not created. The report renders the existing
   `SensitivityReport` only. Recorded in MVP §12.
3. **`LaborRealization`** — the API/orchestration layer REQUIRES an explicit
   choice. The report may present both policies when the input is unresolved,
   and must never silently choose one.
4. **Currency** — geography is required upstream. If currency is nevertheless
   unresolved the report exposes a visible currency gap and invents neither a
   symbol nor a formatting convention.
5. **Composite** — excluded from the Executive Summary and from Layer 1
   entirely. It may appear in Layer 2 as an indicator with its caveats.
6. **Output** — Markdown and JSON. No HTML in the MVP.
7. **Structure** — one Report model, three presentation layers.
8. **Alternatives** — meaningful registry coverage gaps may surface in Layer 2
   (they describe a real limitation of the assessment); individual rejected
   candidates stay Layer 3. Alternatives never become recommendations.
9. **Spec reconciliation** — P0 may correct MVP §5, §11, §13 and stale README
   status. Documentation only.
10. **Derived-figure provenance** — report-layer evidence index. `calc/models.py`
    is not reopened to propagate source ids.

### P0 findings (2026-08-19) — verified against running code

Baseline: all 7 acceptance suites pass, 435 checks, 0 failures, before and
after the documentation changes.

**F1 — zero-impact drivers are a real, legitimate output.** On the standard
document-processing fixture `DecisionDrivers.drivers` is:

    automation_rate        model_estimate   0.9724
    implementation_effort  model_estimate   0.2632
    review_fraction        model_estimate   0.2484
    data_readiness         business_fact    0.0
    integration_complexity business_fact    0.0
    cost_coverage          data_coverage    0.0

`data_readiness` and `integration_complexity` score 0.0 because drivers are
ranked against the UNBOUNDED ECONOMIC quantities only (annual benefit,
first-year net benefit, payback) and those two inputs feed the feasibility
score, which is not among them. This is correct behaviour, not a defect.

Consequence for §9 slot 3 and §10: "the top 3 drivers" is the wrong render
rule. Layer 1 must present drivers in the module's own order while visually
separating three kinds — drivers that move the economics (impact > 0), factual
inputs that do not move the economics (impact == 0, `business_fact`), and
`DATA_COVERAGE` statements — and must never describe a 0.0-impact item as
"what matters most". No re-ranking; a presentation partition only.

**F2 — the sanity path is the common path, not an edge case.** The standard
fixture trips two flags on the economic score (`implausible_payback`,
`extreme_benefit_cost`) and yields a sub-month payback statement. Guardrail 15
is therefore load-bearing from the first report: the Executive Summary's
economics slot must render sanity flags adjacent by default.

**F3 — currency has exactly one source.** Confirmed at runtime:
`EconomicResult` has no currency field; `state.currency` resolved to `USD` for
`geography="US"`. So the report's rule is: currency comes from
`AssessmentState.currency` alone; `None` produces a currency gap. There is no
second source to reconcile against.

**F4 — `ReportInput` round-trips.** `DecisionDrivers.model_dump(mode="json")`
serialises cleanly (28 KB on the standard fixture); the `Mutator` callables
live on `DriverVariable`, which never reaches `DecisionDrivers` output. No
blocker for client-carried state.

**F5 — confirmed defects from the audit, unchanged.** Interval ops drop
`source_id` (`add()` and `mul()` both return `source_id=None`), so the evidence
index is required; the estimator refusal path yields
`recommended_pattern=""`, `overall_automation=0.0-0.0` and 9
needs-more-information entries, so `refused` mode is required; the standard
fixture reports 10 absent cost components across three breakdowns.

### Deferred documentation items (not in the approved P0 list)

Flagged, not edited, because they fall outside decision 9's four items:

* The MVP's OPEN list (revision-history tail) still carries items the frozen
  code resolved — 8.1 says "the engine currently picks task-based silently",
  but `labor.check_consistency` now makes workforce-based primary, keeps task
  as the secondary scenario and reports a material divergence as a finding;
  8.4's "which default" is resolved by there being no default at all; 9.7's
  "calculation still open" is implemented in `calc/assessment_confidence.py`;
  S1, S2, C1 and C2 are all implemented.
* `README.md` and `ARCHITECTURE.txt` state the build runs in a `.venv` on
  Python 3.13. No `.venv` exists in the working tree and the suites were run on
  system Python 3.12.3.

## 18b. Original open decisions (superseded by the approvals above)

1. **Exec summary vs Decision Drivers ordering.** Proposal: Executive Summary
   stays §1 as a frame; Decision Drivers are the first substantive block
   inside Layer 1, immediately after it. Confirm or reverse.

2. **Threshold crossings — build now?** This is genuinely new deterministic
   calculation and belongs in `calc/sensitivity.py`, i.e. a scoped exception
   to the freeze. Options: (a) approve as P7 with the refuse-unless-valid
   rules in §11; (b) defer, report ships without it; (c) approve but place it
   in a new `calc/thresholds.py` so `sensitivity.py` stays untouched.
   Recommendation: (c).

3. **`LaborRealization`.** No screen collects it and the headline number
   depends on it. Options: (a) report refuses without an explicit choice;
   (b) report renders **both** policies side by side as two scenarios;
   (c) default to `COST_ELIMINATED` with a loud disclosure. §8.4 forbids a
   silent default, and (c) is a disclosed one. Recommendation: (b) for the
   report, (a) for the API.

4. **Currency when unresolved.** Options: (a) refuse to render money at all;
   (b) render unitless numbers with a prominent "currency unresolved" gap;
   (c) require geography before analysis. Recommendation: (c) upstream, (b)
   as the report's behaviour when it still happens.

5. **Composite Readiness Score in the Executive Summary.** Recommendation:
   exclude entirely from Layer 1 — it is the single easiest thing in the
   system to misread as a verdict. Confirm, or specify the caveat wording you
   want if it stays.

6. **Output format for the MVP.** Markdown + JSON only, or HTML too?
   MVP §18 rules out a PDF pipeline; print-to-PDF implies HTML eventually.

7. **Business report vs audit report — one document or two?** Proposal: one
   document, three layers, Layer 3 collapsible. Alternative: two artefacts
   from one `Report` model.

8. **Rejected alternatives and registry gaps** — Layer 3 only (proposed), or
   visible in Layer 2? They are honest and unflattering; the argument for
   Layer 2 is that a registry gap is a real limitation of the assessment.

9. **Spec edits.** May I update `deployIQ_MVP.txt` §5 (field list drift),
   §11 (status), §13 (ordering + the report contract), and `README.md:113`
   as part of P0? These are corrections to descriptions of frozen code, not
   changes to the code.

10. **Report-layer `Figure.source_ids` for derived values.** Proposal in §2.A
    is an evidence index in the report layer (no calc change). The cleaner
    fix — union `source_id`s inside `calc/models.py` interval ops — reopens a
    frozen file. Confirm the report-layer approach, or authorise the calc
    change.
