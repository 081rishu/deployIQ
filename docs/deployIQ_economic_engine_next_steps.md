# DeployIQ — Economic Engine Finalization
## Next Steps After E1–E11

### Objective

The Economic Engine (`calc/`) is architecturally complete and the E1–E11
corrective pass is implemented.

The next work is **evidence and provenance finalization**, not another
economic-engine redesign.

Two existing data files should be incorporated into this pass:

- `labor_rates.json`
- `compliance_attestations.json`

---

# 1. Integrate `labor_rates.json`

## Purpose

Use `labor_rates.json` as the India labor evidence registry.

It contains separate role categories for:

- customer support process labor;
- customer support specialist labor;
- accounts payable/document-processing labor;
- AI/ML engineering implementation labor.

The registry explicitly covers India and has no silent fallback behavior.

## Required implementation

### 1.1 Preserve the distinction between process labor and implementation labor

Process labor:

```text
customer_support_agent
customer_support_specialist
accounts_payable_clerk
```

is used for:

```text
current-process labor baseline
```

Implementation labor:

```text
ai_ml_engineer
```

is used for:

```text
buy-vs-build engineering cost
```

These must not be interchangeable.

### 1.2 Preserve source semantics

The values in `labor_rates.json` are market compensation figures, not
automatically fully-loaded employer costs.

Do NOT rename or reinterpret them as fully-loaded costs.

The costing pipeline remains:

```text
market compensation
        ↓
hourly compensation
        ↓
explicit employer-load adjustment
        ↓
fully-loaded labor cost
```

### 1.3 Fully-loaded multiplier

Do not invent a multiplier.

If a sourced fully-loaded multiplier is unavailable:

```text
multiplier = unresolved / explicit assumption
```

and preserve that status in the output.

The current `labor_rates.json` already marks this as TBD; retain that honesty.

### 1.4 Role resolution

The Economic Engine should select the appropriate labor-rate entry from:

```text
geography
+
process role
+
implementation role
```

If no matching rate exists:

```text
UNRESOLVED
```

Do not silently substitute another role or geography.

### 1.5 Provenance

Every labor rate must retain:

```text
rate_id
source
retrieved/as_of
geography
currency
role
provenance
```

If the user overrides the sourced value:

```text
provenance = user_provided
```

The source value itself must remain auditable.

---

# 2. Integrate `compliance_attestations.json`

## Purpose

Use `compliance_attestations.json` as evidence for the Solution Registry's
vendor compliance metadata.

This is primarily a **Solution Estimator / Registry** integration, not an
Economic Engine calculation.

## Required implementation

### 2.1 Evidence-backed compliance

A compliance claim should only become:

```text
SUPPORTED
```

when it has a corresponding:

```text
evidence_id
```

from the attestation registry.

Otherwise:

```text
UNKNOWN
```

Do not retain previously hardcoded vendor compliance claims without evidence.

### 2.2 Preserve attestation scope

The file contains vendor-published attestations.

Do not convert:

```text
vendor attestation
```

into:

```text
independently verified compliance
```

The registry should preserve the evidence type and source.

### 2.3 Vendors actually used

Use the entries for the MVP's actual vendor stack.

Do not add compliance claims for vendors merely mentioned as pricing or
benchmark references.

The existing scope distinction must remain.

### 2.4 Source metadata

Preserve:

```text
vendor
certification/attestation
source
source URL
retrieved date
trust portal
notes/scope
```

---

# 3. Economic Engine Provenance Cleanup

## Problem

Estimator outputs may be treated as assumptions internally, but their original
provenance must not be destroyed.

For example:

```text
automation_rate
60–75%
provenance = estimated
source = solution_estimator
```

must remain distinguishable from:

```text
maintenance_rate
10–20%
provenance = assumed
source = calibration_v1
```

Both are uncertain, but they are not the same type of evidence.

## Required fix

Preserve provenance through:

```text
AssessmentState
    ↓
SolutionEstimate
    ↓
EconomicInput
    ↓
CostLine
    ↓
EconomicResult
```

Derived values should record their inputs/lineage where already supported by
the current implementation.

---

# 4. Current-Process Quality Evidence

The Economic Engine correctly leaves current quality unavailable rather than
assuming 100%.

Next step:

Add evidence/input where available for the two MVP sectors.

### Customer support

Potential operational metrics:

```text
first-contact resolution
escalation rate
rework rate
```

### Document processing

Potential metrics:

```text
exception rate
first-pass yield
straight-through-processing rate
```

Do not rename one metric into another.

For example:

```text
exception rate ≠ accuracy
```

If no defensible current-process metric exists:

```text
ABSENT
```

is the correct MVP behavior.

---

# 5. AI Usage Assumptions

The current customer-support token usage assumptions may remain for the MVP.

Keep the distinction:

```text
provider price
    = sourced

token usage
    = estimated/assumed
```

The usage range must remain configurable and included in sensitivity.

Do not replace the usage assumption with a generic industry number unless
there is genuinely applicable evidence.

---

# 6. Calibration Finalization

The following remain explicit MVP calibrations:

```text
HITL/review ranges
maintenance ranges
implementation-stage allocation
fully-loaded labor adjustment
```

For each, preserve:

```text
calibration_id
version
value/range
provenance
rationale
last_reviewed
```

Do not present these as empirical facts unless evidence is added.

---

# 7. Stage Allocation

The estimator supplies the total implementation effort band.

Stage allocation should partition that effort rather than independently
creating another implementation estimate.

Stages remain:

```text
Data collection / labeling
Model selection / prompting / fine-tuning
Integration
Testing / QA
Deployment
Monitoring setup
Ongoing maintenance
```

Optional next refinement:

- sensitivity-test stage allocation.

This is not required to reopen the Economic Engine architecture.

---

# 8. Geography Rules

Keep the current no-silent-fallback behavior.

```text
India → India rate registry
US → US rate registry
Unknown → unresolved
```

Do not borrow a US rate for India or vice versa.

Currency consistency remains mandatory.

---

# 9. Acceptance Tests

Add/maintain tests for:

### Labor

- India customer support uses the customer-support labor entry.
- India document processing uses AP clerk labor.
- AI implementation uses AI/ML engineer labor.
- Process labor and engineering labor cannot be swapped.
- Missing role → `UNRESOLVED`.
- Missing geography → `UNRESOLVED`.
- Source rate remains auditable after user override.
- Fully-loaded multiplier is not silently invented.

### Compliance

- Evidence-backed vendor claim → `SUPPORTED`.
- Claim without evidence ID → `UNKNOWN`.
- Vendor attestation is not labelled independent verification.
- Benchmark-only vendors do not receive MVP vendor attestations.

### Provenance

- `estimated` remains `estimated`.
- `assumed` remains `assumed`.
- `sourced` remains sourced.
- Derived costs preserve lineage.

### Quality

- Missing current quality → `ABSENT`.
- Exception rate is not renamed accuracy.
- Supplied quality evidence is used only for the matching metric.

### Economics

Existing guarantees remain:

- workforce/task labor divergence is surfaced;
- automation is not treated as headcount reduction;
- absent costs are not zero;
- payback is suppressed when invalid;
- benchmarks are non-additive;
- sensitivity recalculates but does not rank.

---

# 10. Freeze Conditions

After these fixes, freeze the Economic Engine unless a test exposes a
mathematical or accounting defect.

Do NOT add:

- recommendation logic;
- score calculation;
- Decision Driver ranking;
- LLM calls;
- new agent frameworks;
- multi-year financial modeling for the MVP;
- arbitrary benchmarks to fill missing values.

The next major module after this is the Scoring System.

---

# Definition of Done

The Economic Engine is ready to freeze when:

- `labor_rates.json` is integrated;
- process and implementation labor are separated;
- fully-loaded cost is not confused with market compensation;
- India costing works when the required adjustment is defensible;
- missing rates remain unresolved rather than silently substituted;
- `compliance_attestations.json` is connected to registry evidence;
- unsupported compliance claims remain unknown;
- estimator provenance survives into economic outputs;
- current-process quality remains absent when unsupported;
- AI usage assumptions remain explicit;
- calibrations remain versioned and auditable;
- all existing economic acceptance tests pass.

At that point:

```text
AI Interviewer
      ↓
AssessmentState
      ↓
Solution Estimator
      ↓
Economic Engine
      ↓
Scoring System
```

can proceed without further architectural changes to `calc/`.
