# AI Interviewer — Critique & Schema Correction TODO

The interviewer is the only component not yet finessed. Everything downstream
(Solution Estimator, Economic Engine, Scoring System) is now implemented and
frozen, which means the interviewer's contract is no longer a guess: we can
read exactly what the pipeline consumes and compare it to what the interview
actually collects.

This document is the audit and the plan. Nothing here is implemented yet.

Audit method: every field in `AssessmentState` cross-referenced against every
read in `calc/`, `solution/` and `lib/`, plus a completed-interview state run
end-to-end through the pipeline.

---

## STATUS

P0 is implemented per `docs/deployIQ_interviewer_proposed_fix.md`.
Validated by `python3 scripts/interviewer_cases.py`; 373 assertions pass
across all six suites.

**The pipeline now connects end to end.** A completed interview produces a
state the Economic Engine and Scoring System accept:

```
interview READY : True
geography       : India -> INR
current annual  : 5,926,950 INR
scoring         : economic=11.7  feasibility=67.7  confidence=high
```

### Done — P2 (this pass)
- **`worker_role` wired** (fix spec 12). A canonical `ProcessRole` is
  normalised from the user's words and passed to the labor-rate lookup. The
  India specialist rate is now **reachable**: 266-692 INR/hr against the
  generalist's 107-372 — the ~2.4x mis-costing the audit predicted. An
  ambiguous description is flagged rather than guessed, because the two rates
  differ so much.
- **`process_stages` wired, not deleted.** The registry does NOT make
  stage-level input unnecessary — it supplies an implementation KIND, while
  `_stage_plan` hardcoded BUILD for every unstated stage, so the spec 8.5
  buy-vs-build distinction never engaged for any architecture. The default now
  follows what the selected platform actually supplies (low-code buys
  deployment and monitoring; a managed service also buys the model work; a
  custom build buys nothing), and an explicit user statement overrides it.
- **Dead fields removed**: `ai_solution` and `risk.reliability_gap`.
- **`failure_probability` is derived-only** and removed from `RiskInputs`. The
  user is never asked to estimate it; `calc/risk_score.py` derives raw error
  from the architecture's performance evidence and then the HITL-adjusted
  residual. This keeps the interview on observable facts.
- **Completion transparency**: `TurnResult.completion_statement` produces a
  plain sentence — *"Your assessment is complete. We couldn't establish current
  tooling cost, so that was excluded from the analysis rather than estimated."*
  Singular/plural is handled, long lists are truncated, and an UNCERTAIN
  interview says it is incomplete rather than claiming completion.

### Done — P1 (previous pass)
- **§2/§3 WARMUP + ConversationContext** (`interviewer/conversation.py`).
  Opens with a name, one short rapport turn, then "what are you working on?".
  Facts volunteered during warm-up are extracted opportunistically, and a user
  who leads with "we're a BPO in India doing 5,000 tickets" skips the rapport
  entirely. Name and phase live on `ConversationContext`, **never** on
  `AssessmentState` — asserted by test. Deterministic need selection is
  untouched: warm-up decides tone and when to start, never which field is asked.
- **Tier-2 costs** (`annual_tooling_cost`, `monthly_tooling_cost`, `error_rate`,
  `rework_time_per_error_minutes`, `annual_other_direct_cost`). Never mandatory;
  asked only while `MAX_QUESTIONS - TIER2_BUDGET_RESERVE` turns remain, so
  enrichment can never crowd out a Tier-1 clarification. Uncollected Tier-2
  fields are reported at termination via `TurnResult.uncollected_tier2`.
- **Current-process quality** asked as the metric the sector actually tracks —
  support: FCR / escalation / rework; documents: exception rate / first-pass
  yield / STP. Metric NAME and value stored together, and
  `calc/quality.from_collected` maps them semantically: 12-16% exceptions
  becomes an 84-88% NON-EXCEPTION rate, never an "accuracy". A metric with no
  comparable AI-side counterpart yields no comparison rather than a fabricated
  one.
- **Compliance normalisation** to canonical registry keys with the user's
  original wording preserved (`ComplianceRequirement`). The extraction hint
  lists the exact keys the filter matches, tells the LLM not to guess HIPAA
  from "some healthcare privacy requirements", and forbids it from stating
  whether a requirement is satisfied.

### Done — P0 (previous pass)
- **§4 geography** collected as a Tier-1 required field; currency derived from
  it; no geography derives NO currency, never a silent USD default. A
  multi-geography answer is flagged ambiguous rather than resolved silently.
- **§8 validation** — `model_config = {"validate_assignment": True}` and
  `set_value` no longer bypasses the schema. `"a lot"`, `{"foo": "bar"}` and
  `[1, 2]` are all rejected and the previous valid value survives.
- **§9 ranges** — the six range-asked fields are `RangeEstimate`.
  "Between 10,000 and 15,000" stays 10,000-15,000; the midpoint is available
  only through an explicit `point()` derivation. A single number becomes a
  point range, never an invented spread.
- **§5 tiering** — Tier 1 (8) / Tier 2 (6) / Tier 3 (3). Only Tier 1 blocks
  completion; no Tier-3 field can.
- **§18 over-asking** — `risk.failure_impact` demoted from required: audited
  as having no downstream consumer. Required fields stayed at 9 against a
  12-question cap, so geography cost no budget.
- **§20 voice/text** — asserted that `interviewer/voice.py` calls the shared
  engine and reimplements none of the assessment logic.

### Fixed along the way
- **A latent crash in `calc/engine.py`.** The no-task fallback passed a raw
  string where a `HitlMode` was required, so any estimate without a task
  decomposition raised `AttributeError`. Never exercised before because every
  fixture had tasks.
- **`as_range` returned `None` for garbage**, which would have silently
  CLEARED a field rather than rejecting the write. Now raises.
- The ad-hoc scratch sanity script was retired; its null-overwrite check is
  now a first-class case in `scripts/interviewer_cases.py`.

### Note on the frozen Economic Engine
Changing the six fields to `RangeEstimate` required updating 21 call sites,
15 of them in frozen `calc/` modules. These are contract accommodations, not
behaviour changes — every one wraps the field in `point()`, and all 299
pre-existing assertions still pass unchanged.

---

## 0. THE ORIGINAL BREAK (now fixed)

A state that the interviewer considers **complete** is rejected by the
Economic Engine.

Reproduced with a state built exactly as a finished interview leaves it —
every required field resolved, nothing more (mirrors `logs/conversation_*.txt`):

```
interview reaches READY:  True
geography:                None
ECONOMIC ENGINE REFUSES:  labor rate unresolved — no geography on the
                          assessment, and the benchmark packs carry US wage
                          data only.
```

The interviewer declares success and the next stage cannot start. This is the
single highest-priority item: **the pipeline does not currently connect.**

---

## 1. PROPOSED SCHEMA CORRECTIONS

### 1.1 Ranges must be ranges

Five fields are asked for as ranges but stored as scalars:

| field | asked as | schema type |
|---|---|---|
| `monthly_volume` | range | `Optional[float]` |
| `avg_time_per_unit_minutes` | range | `Optional[float]` |
| `current_headcount` | range | `Optional[int]` |
| `fully_loaded_annual_cost` | range | `Optional[float]` |
| `fraction_time_on_process` | range | `Optional[float]` |
| `required_accuracy` | range | `Optional[float]` |

`AssessmentState.set_value()` uses plain `setattr` and the model does not set
`validate_assignment`, so **pydantic never catches the mismatch**. A dict
lands in a float field and travels downstream unvalidated:

```
after set_value : {'min': 0.95, 'max': 0.95}   <- in an Optional[float]
```

`calc/engine.py::_as_range` already carries a defensive coercion with a
comment pointing at this. That workaround should not be the fix.

**Proposal:** every quantity the interview asks for as a range becomes a
`RangeEstimate` (the canonical type already used everywhere else), and
`set_value` validates. Where a scalar is genuinely wanted, stop setting
`ask_range=True`.

```python
monthly_volume: Optional[RangeEstimate] = None
avg_time_per_unit_minutes: Optional[RangeEstimate] = None
current_headcount: Optional[RangeEstimate] = None
fully_loaded_annual_cost: Optional[RangeEstimate] = None
fraction_time_on_process: Optional[RangeEstimate] = None
required_accuracy: Optional[RangeEstimate] = None
```

This also removes a whole class of silent corruption: a range answer
("8 to 12 minutes") currently either loses its spread or lands as a dict.

### 1.2 Fields the pipeline reads that the interview never collects

```python
# Geography — REQUIRED for any economic result (calc/labor.py,
# calc/implementation.py, solution/estimator.py all read it).
geography: Optional[str] = None          # today: never collected

# Current-cost components (spec 8.2). Without these the baseline is
# permanently a labor-only floor, which biases every assessment AGAINST AI.
annual_tooling_cost: Optional[float] = None
monthly_tooling_cost: Optional[float] = None
error_rate: Optional[float] = None
rework_time_per_error_minutes: Optional[float] = None
annual_rework_cost: Optional[float] = None
annual_other_direct_cost: Optional[float] = None
other_direct_cost_description: Optional[str] = None

# Current-process quality (spec 8.6 / E6). Without it the quality comparison
# is permanently ABSENT.
current_quality_metric: Optional[QualityMetricName] = None   # NEW enum
current_quality_value: Optional[RangeEstimate] = None
```

### 1.3 Compliance must be collectable as matchable standards

`risk.compliance_exposure` is `list[str]` gathered from free text, but it now
feeds a **hard deterministic filter** whose evidence registry matches on
normalised keys (`hipaa`, `gdpr`, `soc 2`, `iso 27001`, `pci dss`, ...).

Free text like "we have GDPR requirements" will not normalise to `gdpr`, so a
real constraint can silently fail to filter — the worst possible failure mode
for a compliance gate.

**Proposal:** a `ComplianceRequirement` with a recognised standard enum plus
the user's own words:

```python
class ComplianceRequirement(BaseModel):
    standard: str          # normalised key, validated against the registry
    stated_as: str         # the user's original phrasing, preserved
    hard_requirement: bool = True
```

### 1.4 Schema fields to retire or rework

| field | issue | proposal |
|---|---|---|
| `ai_solution` | dead: never written, never read | remove; `SolutionEstimate` is the real carrier |
| `risk.reliability_gap` | dead: computed in `calc/risk_score.py` instead | remove from state |
| `risk.failure_impact` | collected AND required, but **nothing downstream reads it** | keep as narrative context, drop `required_for_completion` |
| `worker_role` | collected, never read | wire it to labor-rate role resolution (see I12) or stop asking |
| `process_stages` | typed `STRING` in fields.py, `list[ProcessStage]` in schema, never chased | either collect properly or delete the FieldSpec |

---

## 2. CRITIQUES

### I1. Geography is never collected — the pipeline cannot complete
**Severity: blocker.** Read by three modules. The engine refuses without it,
by design (E8 forbids a silent US fallback). Nothing asks the user.

Geography also determines currency, which determines whether provider pricing
can be included at all. An India assessment currently excludes inference cost
entirely.

### I2. `set_value()` bypasses validation
`AssessmentState` has no `model_config = {"validate_assignment": True}`, and
`set_value` uses bare `setattr`. Every type guarantee in the schema is
advisory. This is what allows I3 and the `required_accuracy` drift to persist.

### I3. Range answers are structurally lossy
Six fields ask for a range and store a scalar (table in 1.1). Either the
spread is discarded — losing exactly the uncertainty the product exists to
surface — or a dict lands in a numeric field. Spec 4.4 ("ranges over false
precision") is not actually enforceable with the current types.

### I4. Three of four current-cost components are uncollectable
`calc/current_state.py` reads tooling, rework and other-direct costs. The
interview asks for none of them, so `CostBreakdown` reports them ABSENT on
every run and the baseline is always a floor.

Direction of the bias matters: understating current cost understates savings,
so the system errs **against** AI. Safer than the reverse, but it is a
systematic distortion, and error/rework is frequently the largest
AI-addressable line in document processing.

### I5. Current-process quality is never collected
`calc/quality.py` exists to compare current and AI quality on the same metric
and refuses to assume 100%. The interview collects no quality metric at all,
so the comparison is ABSENT on every assessment and the AI side is the only
side discounted for errors.

The metric must be sector-appropriate and semantically matched — an exception
rate is not an accuracy rate (E6-B). The interview must therefore capture
*which* metric, not just a number.

### I6. `process_stages` is mistyped and never chased
`fields.py` declares it `ValueType.STRING` with `analysis_relevant=False`;
the schema wants `list[ProcessStage]`. `calc/implementation.py` reads it for
buy-vs-build, so **every stage silently defaults to BUILD** — the more
expensive branch — and the spec 8.5 buy/build distinction never engages.

### I7. `risk.failure_probability` is never collected
Read by `calc/risk_score.py`, which falls back to deriving it from the
architecture's performance metrics. That fallback is reasonable, but the field
should either be collectable or removed from the state so the contract is
honest.

### I8. A required question feeds nothing
`risk.failure_impact` is `required_for_completion=True` — it can block READY —
yet no downstream module reads it. Only `failure_impact_severity` is consumed.
Spec 10.5's over-asking guard forbids spending a question on something the
analysis ignores.

### I9. `worker_role` is collected and discarded
Never read. Meanwhile `calc/labor.py` hardcodes `SECTOR_PROCESS_ROLE`, so the
India registry's `customer_support_specialist` rate (₹610k median) is
**unreachable** — every support assessment is costed at the generalist agent
rate (₹247k). A tier-2-heavy process is mis-costed by ~2.5x.

### I10. Two dead schema fields
`ai_solution` and `risk.reliability_gap` are neither written nor read.

### I11. Compliance free text cannot reach the filter
See 1.3. A hard gate whose input may not match its own vocabulary.

### I12. Labor role resolution ignores the collected role
Consequence of I9. The fix is small and the payoff is real: it makes the
second India rate entry usable.

### I13. The interview budget will not absorb the additions
Current: **9 required fields**, `MAX_QUESTIONS = 12`, `MAX_ATTEMPTS_PER_FIELD = 3`.
A single clarification loop on three fields exhausts the cap.

Adding geography, four cost fields and two quality fields would take required
fields to ~16 against a 12-question cap — the interview would terminate
UNCERTAIN before finishing.

This cannot be solved by raising the cap alone; spec 10 is explicit that this
is "not a form". It needs a **tiered** model:

- **Tier 1 (blocking)** — without it no analysis runs: sector, process,
  volume, handling time, geography.
- **Tier 2 (materially improves)** — asked if budget allows, otherwise ABSENT
  and reported: tooling/rework/other costs, current quality.
- **Tier 3 (opportunistic)** — never asked directly; filled only when
  volunteered: worker role, process stages, failure impact narrative.

Combined with the existing multi-fill behaviour (spec 10.4) and benchmark
substitution, Tier 1 should fit in ~6 questions, leaving room for
clarification.

### I14. Nothing tells the user what was left ABSENT
The engine reports absent components carefully, but the interview terminates
without surfacing which Tier-2 fields went uncollected. The user never learns
that supplying a rework figure would materially change the baseline. The
UNCERTAIN stop reason covers unresolved fields, not unasked ones.

---

## 3. WHAT MUST NOT CHANGE

The interviewer's architecture is sound and is not being reopened:

- the four-state machine (INTERVIEWING / CLARIFYING / READY / UNCERTAIN);
- deterministic need selection — the LLM never chooses what to ask or when to
  stop;
- state-driven, not history-driven (spec 10.1);
- one question at a time, multi-fill from a single answer (spec 10.4);
- the over-asking guard (spec 10.5);
- attempts counting asks, not extractions;
- a null extraction never overwriting a collected value;
- structured JSON output only (spec 10.7);
- the voice path sharing the same engine — any fix must land in
  `interviewer/engine.py`, not be duplicated in `interviewer/voice.py`.

---

## 4. PRIORITIES

### P0 — DONE
- [x] I1 collect `geography` (and derive currency from it)
- [x] I2 enable `validate_assignment`; make `set_value` validate
- [x] I3 convert the six range-asked fields to `RangeEstimate`
- [x] I8 demote `risk.failure_impact` from required
- [x] I13 tier model (Tier 1/2/3)

### P1 — the analysis is still structurally incomplete
- [x] §2/§3 WARMUP phase + ConversationContext
- [x] I4 collect tooling / rework / other direct costs (Tier 2)
- [x] I5 collect a sector-appropriate current-quality metric (Tier 2)
- [x] I11 collect compliance as matchable standards + the user's phrasing

### P2 — DONE
- [x] I9 + I12 wire `worker_role` into labor-rate role resolution
- [x] I6 collect `process_stages` properly (wired, not deleted)
- [x] I10 remove `ai_solution` and `risk.reliability_gap`
- [x] I7 `failure_probability` is derived-only
- [x] I14 report un-asked Tier-2 fields at termination (done with the Tier-2 work: `TurnResult.uncollected_tier2`)

---

## 5. VALIDATION TO ADD

`scripts/interviewer_cases.py` does not exist. The only interviewer coverage is
`scripts/conversation_test.py` (scripted transcripts) and an ad-hoc sanity
script. Needed, all runnable with a stubbed LLM:

- a completed interview produces a state the **Economic Engine accepts**
  (the test that would have caught the headline break);
- a range answer survives as a range;
- `set_value` rejects a type mismatch;
- Tier-1 completion fits inside the question cap;
- multi-fill still fills several fields from one answer;
- a compliance answer normalises to a registry-matchable standard;
- un-asked Tier-2 fields are reported at termination;
- the voice path produces the same state as the text path.
