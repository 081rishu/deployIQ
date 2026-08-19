# DeployIQ — Scoring System
## Proposed Corrective Implementation Specification

### Purpose

This document replaces the current Scoring System TODO with the proposed
implementation plan after review of the existing scoring implementation and
the S1–S11 critique.

The scoring layer is **not an economic engine and not a decision engine**.

Its job is to:

1. transform validated economic/technical/risk quantities into bounded
   explanatory indicators;
2. expose uncertainty and data quality honestly;
3. identify the underlying facts that materially affect the assessment;
4. provide context without producing a recommendation.

The human remains the decision-maker.

---

# 1. Non-Negotiable Boundary

The scoring system must preserve:

```text
Economic Engine
      ↓
Feasibility inputs
      ↓
Risk inputs
      ↓
Scoring
      ↓
Decision Drivers
      ↓
Report
```

The scoring layer MUST NOT:

- choose an architecture;
- alter economic calculations;
- invent missing economic inputs;
- convert a score into "go/no-go";
- produce a recommendation category;
- use an LLM to choose drivers;
- allow a composite score to become a hidden verdict.

The existing boundary is correct and must remain. The validation rule that
driver statements contain no recommendation language should remain. fileciteturn12file0L201-L205

---

# 2. S1 — Replace Score Elasticity for Decision Drivers

## Problem

The current driver ranking uses:

```text
(% change in score)
--------------------
(% change in input)
```

This is mathematically deterministic but analytically misleading because the
scores are bounded and saturating.

The critique demonstrates the problem:

- economic score 98.3 → labor-rate elasticity 0.024;
- economic score 74.3 → labor-rate elasticity 0.529.

The same underlying variable can therefore appear 22× less important purely
because the score is near a ceiling. fileciteturn12file0L66-L88

## Decision

**Decision Drivers must be ranked against underlying economic quantities, not
against the normalized score.**

The primary quantities are:

```text
annual economic benefit
first-year net benefit
payback period
```

where available.

The score remains a presentation indicator.

---

# 3. Decision-Impact Model

For each candidate input `x`, calculate its effect on the underlying
economic quantity.

### For benefit / net benefit

Use relative sensitivity:

```text
impact(x) =
    |ΔQ / Q|
    /
    |Δx / x|
```

where `Q` is the relevant underlying quantity.

### For payback

Because payback can approach zero, become undefined, or cross from positive
to negative benefit, use absolute/threshold-aware analysis rather than
blindly applying ordinary elasticity.

Calculate:

```text
payback_base
payback_low_input
payback_high_input
```

and record:

```text
payback_sensitivity
```

If payback becomes non-positive or undefined under one bound:

```text
payback_status = non_positive_or_indeterminate
```

rather than manufacturing an elasticity.

## Combined driver impact

A driver may affect several quantities.

Use a deterministic weighted impact across the quantities that are actually
available:

```text
driver_impact =
    weighted(
        benefit_sensitivity,
        net_benefit_sensitivity,
        payback_sensitivity
    )
```

The weights must be centralized and versioned.

If only one quantity is valid, use only that quantity.

Do NOT allow missing quantities to become zero.

---

# 4. Why the Score Still Exists

Scores remain useful for compact presentation:

```text
Economic Score: 82
Feasibility:    68
Risk:            74
Composite:       76
```

But these values answer:

> "How does the assessment map onto the chosen analytical indicators?"

They do NOT answer:

> "What matters most?"

Decision Drivers answer the second question.

Therefore:

```text
Score
    = normalized explanatory indicator

Decision Driver
    = underlying factor with measurable decision impact
```

---

# 5. S3 — Economic Sanity and Plausibility Gate

## Problem

The scoring system currently produces a near-perfect economic score for
inputs yielding a 0.1–0.5 month payback.

The critique correctly identifies this as a consistency problem rather than
a success case. fileciteturn12file0L109-L117

## Fix

Introduce a deterministic `economic_sanity` layer BEFORE score normalization.

It does not change the Economic Engine.

It classifies the output.

### Required checks

#### A. Implausibly short payback

If:

```text
payback < configured_sanity_floor
```

then:

```text
economic_sanity = warning
```

The default floor must be an explicit calibration value, not hidden in code.

Do NOT automatically reduce the Economic Score.

Instead expose:

```text
economic_sanity_warning
```

and prevent the report from presenting the score as high-confidence evidence
of economic attractiveness.

#### B. Extreme benefit/cost ratio

If:

```text
benefit / implementation_cost
```

exceeds a configured sanity threshold, flag it.

#### C. Contradictory economics

Examples:

```text
negative annual benefit + positive payback
```

or:

```text
negative net benefit + finite positive payback
```

must be treated as invalid states.

#### D. Range crossing

If:

```text
best case → strong positive economics
worst case → negative economics
```

the score may still be calculated, but:

```text
economic_outcome = range_crossing
```

must be explicit.

## Important

Do NOT arbitrarily cap a 98 score to 80 just because it "looks too good."

That would simply replace one arbitrary rule with another.

The correct response is:

```text
score + sanity flag + uncertainty
```

---

# 6. S2 — Redesign Uncertainty

## Problem

The current `relative_width` mixes:

1. real estimate ranges;
2. synthetic category stepping;
3. arbitrary ±15% assumptions.

These are not comparable quantities. fileciteturn12file0L91-L107

## Decision

**Uncertainty must only be expressed numerically when the input actually has
a numerical uncertainty range.**

---

# 7. Three Types of Uncertainty

Every uncertain input should be classified as:

### A. Numeric range

Example:

```text
automation = 55–70%
```

This has measurable relative width:

```text
(70 - 55) / midpoint
```

### B. Categorical uncertainty

Example:

```text
data readiness = Medium
```

A category is NOT automatically a 67% uncertainty range.

Instead represent:

```text
category = Medium
category_confidence = Medium
```

and use discrete scenario analysis if needed.

### C. Assumption range

Example:

```text
maintenance = 10–20%
provenance = assumption
```

This is numeric but must remain explicitly identified as an assumption.

---

# 8. New Uncertainty Representation

Each uncertain field should expose:

```text
uncertainty_type:
    numeric_range
    categorical
    assumption_range
    none

range:
    min
    max

confidence:
    high / medium / low

provenance:
    user_provided / sourced / estimated / assumed / derived

source_id:
    optional
```

Do not derive a fake numeric range from a category.

---

# 9. Uncertainty Callout

The existing specification asks for:

> the field whose range is widest relative to its impact.

Keep that concept, but redefine it.

For each field:

```text
uncertainty_magnitude
×
economic_driver_impact
```

Only numeric-range inputs participate in the numeric uncertainty index.

Categorical fields participate through:

```text
scenario impact
×
category confidence
```

rather than artificial width.

The final callout should therefore be:

```text
Most decision-sensitive uncertainty:
    automation rate

Why:
    estimated range is 55–70%, and this range materially changes annual
    benefit and payback.
```

If the largest uncertainty is categorical:

```text
Most decision-sensitive unresolved factor:
    data readiness

Reason:
    current evidence places data readiness at Medium confidence; moving to
    High/Low changes feasibility materially.
```

The system must never claim that "Medium has 67% uncertainty" merely because
the enum has five values.

---

# 10. S4 — Honest Score Bounds

## Problem

Score bounds currently vary only some ranged inputs while holding categorical
inputs fixed. This makes a narrow score band look more certain than the
assessment actually is. fileciteturn12file0L119-L126

## Fix

Every score must expose what its bounds actually represent.

Use:

```text
bounds_type:
    numeric_input_envelope
    scenario_envelope
    unavailable
```

### Numeric envelope

Vary all relevant numeric ranges.

### Scenario envelope

For categorical variables, evaluate defined alternative scenarios where the
change is meaningful.

Example:

```text
data readiness:
    Low
    Medium
    High
```

If scenario definitions exist, calculate the score under each.

### Unavailable

If important uncertainty cannot be represented defensibly:

```text
bounds = unavailable
```

Do not produce a deceptively narrow numerical band.

---

# 11. S5 — Reliability-Gap Penalty

## Problem

The current:

```text
RELIABILITY_PENALTY_WEIGHT = 0.5
```

is an arbitrary consequential constant. fileciteturn12file0L128-L133

## Decision

Do not pretend a universal weight is scientifically established.

For the MVP, replace the raw arbitrary penalty with a **calibrated,
versioned policy**.

The risk model should separate:

```text
failure probability
failure impact
reliability gap
```

rather than collapsing them prematurely.

### Preferred formulation

First calculate base risk:

```text
base_risk =
    failure_probability
    ×
    failure_impact
```

Then represent reliability gap as an explicit risk modifier:

```text
reliability_modifier =
    calibrated_function(reliability_gap)
```

The function and parameters live in:

```text
calibration.py
```

and carry:

```text
provenance
rationale
version
```

If no defensible calibration exists, use a conservative categorical modifier
and label it as an MVP assumption.

Do not present the modifier as empirically derived.

---

# 12. S6 — Impact Severity

## Problem

The current:

```text
0.10 / 0.30 / 0.50 / 0.75 / 1.00
```

ladder is a calibration, not an objective measurement. fileciteturn12file0L135-L139

## Decision

Keep the five-level semantic categories:

```text
negligible
minor
moderate
major
severe
```

because the categories are useful for structured collection.

But centralize the numeric mapping in the calibration object:

```text
impact_severity_weights_v1
```

Each value requires:

```text
value
provenance
rationale
version
```

Do not claim the ladder is "scientifically correct."

## Compliance

Compliance exposure remains a hard blocker/flag.

It must NOT be converted into an ordinary severity number merely to make the
formula cleaner.

---

# 13. S7 — Failure Probability Must Account for HITL

## Problem

The current fallback:

```text
failure_probability = 1 - accuracy
```

conflates raw model error with actual business failure.

Human review can catch a significant portion of errors. fileciteturn12file0L141-L147

## Fix

Separate:

```text
raw_error_probability
```

from:

```text
residual_failure_probability
```

### Raw error

If directly sourced/estimated:

```text
raw_error = 1 - relevant_accuracy
```

only when the accuracy metric has the appropriate semantics.

### HITL

Then model the residual failure after review:

```text
residual_failure =
    raw_error
    ×
    residual_escape_fraction
```

where:

```text
residual_escape_fraction
```

depends on:

- HITL mode;
- review coverage;
- reviewer reliability;
- task characteristics.

For the MVP, do NOT invent a universal "human catches 90%" value.

If review effectiveness is not evidenced:

```text
residual_escape_fraction = assumption range
```

with explicit provenance.

### No HITL

```text
residual_failure ≈ raw_error
```

subject to the semantics of the selected metric.

### Human review

```text
residual_failure < raw_error
```

only when the review mechanism actually covers the relevant failure.

---

# 14. S9 — Interview Quality Must Affect Assessment Confidence

## Problem

The interviewer already tracks:

```text
LOW_CONFIDENCE
CONTRADICTORY
attempt count
```

but the scoring system currently ignores them. fileciteturn12file0L155-L160

## Fix

Create a deterministic `assessment_confidence` input.

For every estimator-critical field:

```text
field quality
+
provenance
+
contradiction status
+
resolution status
```

contribute to overall assessment confidence.

### Important

Do NOT subtract arbitrary points per contradiction.

Use the same field-quality/confidence model established by the estimator.

The scoring layer consumes:

```text
assessment_confidence
```

rather than recreating interviewer logic.

### Example

```text
required_accuracy:
    contradictory
    LOW_CONFIDENCE

→ overall confidence cannot be High
```

Even if the numerical score itself remains computable.

---

# 15. 9.7 Overall Assessment Confidence

9.7 is now fully defined.

Overall confidence should combine:

```text
field quality
+
provenance quality
+
range width
+
contradiction status
+
benchmark/evidence coverage
```

Do not use score magnitude as confidence.

A score of 98 can have Low confidence.

A score of 62 can have High confidence.

These are different concepts.

### Suggested output

```text
Assessment Confidence:
    Medium

Reasons:
    - Most economic inputs are user-provided.
    - Automation is an LLM estimate with a 55–70% range.
    - Current-process quality is not independently benchmarked.
    - No critical field contradictions remain.
```

The reasons should be generated deterministically from structured facts.

LLM may only phrase them if desired.

---

# 16. S8 — Composite Score

## Problem

The current:

```text
Economic 40%
Feasibility 30%
Risk 30%
```

is a product-design weighting rather than an empirical truth. fileciteturn12file0L149-L153

## Decision

Keep the composite because it is explicitly contextual.

But:

1. centralize the weights;
2. mark them as `assumption/calibration`;
3. expose the version;
4. avoid excessive precision.

Instead of:

```text
Composite = 87.7
```

prefer:

```text
Composite = 88
```

or:

```text
Composite = 88 / 100
```

The report should describe it as:

> Summary indicator based on the configured scoring weights.

Never:

> Overall decision score.

---

# 17. Score Computability

The existing "computable-or-not" behavior is correct.

A missing critical input must produce:

```text
computable = false
missing_inputs = [...]
```

and:

```text
band = not_computable
```

It must never become:

```text
score = 0
```

because:

```text
unknown ≠ zero
```

The critique confirms this structural decision is already implemented and
should remain. fileciteturn12file0L24-L32

---

# 18. Economic Score

Keep the current piecewise normalization as an MVP presentation mechanism:

```text
payback:
    <= 6 months → 100
    >= 24 months → 0
```

and:

```text
benefit/cost ratio
    saturates at configured upper bound
```

with the current 60/40 weighting.

However:

- centralize thresholds;
- version them;
- label them calibration;
- run sanity checks before presenting a high score.

The normalization is not the driver-ranking mechanism anymore.

---

# 19. Feasibility Score

Keep:

```text
data readiness = 45%
automation achievability = 30%
integration = 25%
```

but distinguish:

```text
score value
```

from:

```text
confidence in score
```

For categorical inputs:

```text
DataReadiness = Medium
```

the score may be deterministic, while confidence reflects how confidently
the interviewer established that category.

Do not create fake numeric uncertainty ranges from the enum.

---

# 20. Risk Score

Risk remains:

```text
higher = safer
```

but the implementation should conceptually be:

```text
raw failure probability
        ↓
HITL-adjusted residual failure probability
        ↓
× failure impact
        ↓
risk exposure
        ↓
risk score
```

Compliance:

```text
hard blocker / explicit flag
```

remains outside ordinary arithmetic.

Reliability gap is a modifier, not a replacement for failure probability.

---

# 21. Decision Drivers — Revised Architecture

Decision Drivers remain the headline analytical output.

But their ranking now comes from:

```text
underlying quantity sensitivity
+
uncertainty
+
data quality
```

not score elasticity.

### Candidate driver

For each candidate field:

```text
field
baseline
range/scenario
economic impact
feasibility impact
risk impact
confidence
provenance
```

### Driver impact

Calculate deterministically.

Example:

```text
automation_rate
    ↓
annual AI operating cost
annual benefit
payback
Economic Score
```

The driver can be ranked because it changes actual economic quantities.

---

# 22. Driver Types

The system should distinguish:

### Business fact

```text
Labor accounts for 72% of the measured current cost.
```

### Model estimate

```text
Expected automation is estimated at 55–70%.
```

### Data-coverage fact

```text
The current assessment contains no sourced rework-cost data.
```

### Uncertainty

```text
Automation range materially changes estimated annual benefit.
```

This prevents S10's problem where a statement about the measurement model can
look like a fact about the business. fileciteturn12file0L162-L167

---

# 23. S11 — Evidence in Driver Statements

## Problem

The benchmark/evidence packs are not currently consulted by driver ranking or
shown in driver statements. fileciteturn12file0L169-L175

## Fix

A driver should carry:

```text
evidence_ids: [...]
```

when its underlying value depends on evidence.

Example:

```text
Expected document STP is estimated at 60–75%.

Evidence context:
- benchmark STP = 32.6%
- benchmark evidence_id = ...
- assessment-specific estimate = ...
```

Do not use the benchmark as an additive economic cost.

The benchmark is contextual evidence.

### Important

Evidence does NOT get a special ranking bonus merely because it is sourced.

Evidence affects:

```text
confidence
comparability
interpretation
```

The driver impact still comes from the underlying business/economic model.

---

# 24. Driver Generation Boundary

Deterministic code selects:

```text
which facts are drivers
```

LLM, if used, may only phrase them.

The LLM must NOT decide:

```text
"automation sounds important, let's mention it"
```

The code decides based on measured impact.

---

# 25. Uncertainty Callout

The uncertainty callout should be selected from:

```text
uncertainty magnitude
×
decision impact
```

not:

```text
largest raw range
```

Example:

```text
Biggest decision-relevant uncertainty:
Automation rate

Estimated range:
55–70%

Why it matters:
This range materially changes annual benefit and payback.
```

If the uncertainty is data quality rather than numerical range:

```text
Biggest unresolved factor:
Current rework cost

Reason:
No user-provided or benchmark-supported value is available, and the field
materially affects the current-cost baseline.
```

This is more honest than inventing a numerical "uncertainty index" for every
field.

---

# 26. Acceptance Test Suite

## S1-A — Score saturation must not change driver ranking

Create two otherwise equivalent cases:

```text
Case A:
Economic score near 100

Case B:
Economic score near middle
```

Vary the same underlying labor-rate input.

Expected:

```text
driver importance is based on underlying economic quantities
```

and does not collapse merely because the score is saturated.

---

## S2-A — Numeric uncertainty

```text
automation = 55–70%
```

Expected:

```text
numeric uncertainty
```

with real range width.

## S2-B — Categorical uncertainty

```text
data readiness = Medium
```

Expected:

```text
categorical
```

not:

```text
67% uncertainty
```

## S2-C — Assumption range

```text
maintenance = 10–20%
provenance = assumption
```

Expected:

```text
numeric uncertainty + assumption provenance
```

---

## S3-A — Implausible payback

```text
payback = 0.1–0.5 months
```

Expected:

```text
economic_sanity_warning
```

not silent:

```text
Economic Score = 98
Confidence = High
```

---

## S3-B — Negative economics

```text
annual benefit < 0
```

Expected:

```text
payback = unavailable
```

and no positive-payback interpretation.

---

## S4-A — Bounds transparency

Expected score output includes:

```text
bounds_type
inputs_varied
inputs_held_fixed
```

or equivalent machine-readable metadata.

---

## S5-A — Reliability calibration

Changing the calibration version must change the modifier deterministically.

The source/rationale/version must remain inspectable.

---

## S6-A — Impact categories

All five categories map through the canonical calibration object.

No duplicated numeric ladder exists.

---

## S7-A — No HITL

Expected:

```text
residual_failure ≈ raw_failure
```

subject to metric semantics.

## S7-B — HITL

Expected:

```text
residual_failure < raw_failure
```

only when review coverage/effectiveness assumptions permit it.

The system must expose the assumption.

---

## S9-A — Contradictory interviewer field

A critical field marked:

```text
CONTRADICTORY
```

must prevent High overall assessment confidence.

---

## S9-B — Clean field

A resolved user-provided field should contribute positively to confidence.

---

## S10-A — Data-coverage wording

Expected:

```text
"Labor represents the entire measured current cost because only labor cost
was supplied."
```

NOT:

```text
"Labor is the whole cost of the process."
```

---

## S11-A — Evidence IDs

A benchmark-backed driver must contain the relevant `evidence_id`.

---

## S11-B — Benchmark remains non-additive

Evidence must not change the economic baseline merely by being attached to
the driver.

---

## Composite-A — Precision

Expected:

```text
Composite = 88
```

rather than:

```text
Composite = 87.734281
```

unless higher precision is explicitly required internally.

---

## Boundary-A — No recommendation

No driver statement may contain:

```text
recommend
should
go
no-go
pilot
adopt
reject
```

or equivalent recommendation language.

---

# 27. Calibration Registry

Create/extend the canonical scoring calibration object.

It should contain:

```text
economic_payback_thresholds
benefit_cost_saturation
feasibility_weights
risk_calibration
impact_severity_mapping
composite_weights
sanity_thresholds
```

Every value requires:

```text
parameter_id
value
unit
provenance
rationale
version
```

The scoring system must not contain scattered magic numbers.

---

# 28. What NOT to Do

Do not:

- replace score elasticity with another arbitrary score formula;
- invent a scientific basis for the 40/30/30 composite;
- treat categorical stepping as probability;
- convert every accuracy gap directly into failure probability;
- assume human review catches a fixed percentage of failures without evidence;
- turn compliance into an ordinary weighted risk term;
- cap scores merely because the result looks unusually high;
- use benchmark values as additive economic costs;
- let evidence automatically increase driver rank;
- let an LLM choose drivers;
- create a recommendation threshold;
- introduce machine learning merely to rank drivers.

---

# 29. Implementation Order

## Phase 1 — Critical analytical correctness

1. Replace score elasticity with underlying-quantity sensitivity.
2. Redesign uncertainty representation.
3. Add economic sanity flags.
4. Add score-bound metadata.

## Phase 2 — Risk correctness

5. Implement HITL-aware residual failure probability.
6. Centralize reliability-gap calibration.
7. Centralize impact severity calibration.
8. Feed interviewer/estimator field quality into overall confidence.

## Phase 3 — Driver quality

9. Rebuild deterministic driver ranking.
10. Separate business facts from data-coverage facts.
11. Attach evidence IDs.
12. Rebuild uncertainty callout using impact × uncertainty.

## Phase 4 — Presentation

13. Reduce composite precision.
14. Add confidence explanation.
15. Preserve recommendation-language guard.
16. Run all scoring acceptance tests.

---

# 30. Definition of Done

The Scoring System is complete when:

- score values remain deterministic;
- missing inputs produce `not_computable`, not zero;
- Economic Score is not used as the driver-ranking quantity;
- driver ranking uses underlying economic quantities;
- score saturation cannot erase important economic drivers;
- numeric ranges are distinguished from categorical uncertainty;
- arbitrary ±15%/±30% uncertainty is removed;
- economic sanity warnings exist;
- current and AI quality metrics remain semantically comparable;
- reliability risk accounts for HITL where defensible;
- reliability and severity calibrations are centralized and versioned;
- interviewer contradictions affect assessment confidence;
- composite weights are explicit calibration, not hidden truth;
- composite precision does not imply unsupported accuracy;
- drivers distinguish business facts from data-coverage limitations;
- evidence IDs are attached where relevant;
- benchmarks remain contextual, not additive;
- no LLM chooses drivers;
- no score produces a recommendation;
- the full scoring test suite passes.

At that point, freeze the scoring layer.

The complete analytical pipeline is then:

```text
AI Interviewer
      ↓
AssessmentState
      ↓
AI Solution Estimator
      ↓
SolutionEstimate
      ↓
Economic Engine
      ↓
Economic quantities + uncertainty
      ↓
Scoring System
      ├── Economic Score
      ├── Feasibility Score
      ├── Risk Score
      ├── Composite Context
      └── Decision Drivers
              ↓
           Report
```

The human remains the final decision-maker.
