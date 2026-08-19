# DeployIQ — AI Solution Estimator
## Final Finesse / TODO Correction Specification

This document replaces the current "Remaining Work" section of the
Solution Estimator TODO.

The C1–C14 and N1–N11 implementation passes are considered complete.
The objective now is **finesse, consistency, evidence quality, calibration,
cleanup and final verification**.

Do not redesign the estimator architecture.

---

# 1. Current Architectural Contract

The estimator remains:

    AssessmentState
        ↓
    LLM capability decomposition
        ↓
    Structured capability validation
        ↓
    Reference + Registry candidate space
        ↓
    Deterministic compatibility filtering
        ↓
    Deterministic ranking
        ↓
    Selected implementation
        ↓
    Evidence / benchmark grounding
        ↓
    Deterministic effort / integration / workload calculations
        ↓
    SolutionEstimate

Responsibilities remain:

- LLM:
  - interpret/decompose the workflow;
  - provide contextual estimates where evidence is unavailable;
  - explain deterministic results.

- Registry:
  - constrain available solution patterns and implementations;
  - provide compatibility and control metadata.

- Reference Solutions:
  - provide curated baselines;
  - materially influence ranking.

- Deterministic code:
  - validate;
  - filter;
  - rank;
  - calculate;
  - aggregate;
  - assign authoritative provenance.

The LLM must never directly select an architecture/pattern ID.

---

# 2. Priority 0 — Mirror D3 in the Economic Engine

## Problem

The estimator now has a locked D3 policy:

> When resolved and reliable, the user's observed aggregate handling time is
> authoritative. LLM task-level times provide proportions only.

The Economic Engine must use exactly the same policy.

Otherwise the same AssessmentState could produce:

    Estimator:
    observed total = 6 minutes

    Economic Engine:
    task decomposition = 12 minutes

and therefore two different labor baselines.

## Fix

Create one shared reconciliation utility/policy rather than implementing D3
twice.

Conceptual flow:

    observed aggregate
          +
    estimated task decomposition
          ↓
    divergence calculation
          ↓
    normalize task proportions
          ↓
    reconcile task times to observed aggregate

When the aggregate is resolved and reliable:

    task_share_i =
        estimated_task_time_i / Σ estimated_task_time

    reconciled_task_time_i =
        observed_total_time × task_share_i

The reconciled task times must sum exactly to the observed aggregate.

If the aggregate is missing:

- task-derived total may be used;
- provenance must remain derived/estimated;
- confidence must reflect the absence of observed aggregate data.

If there is severe divergence:

- record the divergence;
- reduce confidence;
- if the contradiction prevents a defensible calculation,
  return `needs_more_information`.

## Acceptance Tests

1. Estimator and Economic Engine produce identical baseline handling time.
2. User total = 6 min, task estimates = 4 + 5 + 3:
   - model total = 12;
   - task proportions are normalized;
   - reconciled total = 6.
3. Severe contradiction lowers confidence.
4. Missing aggregate uses task-derived total only when explicitly marked.
5. No module can silently overwrite the observed aggregate.

---

# 3. Priority 0 — Ground Engineering Labor Rates and Effort Bands

## Problem

Effort-band hours and engineering labor rates remain assumptions without
geography.

This is weak because these values eventually feed economic calculations.

## Fix

Keep engineering effort and labor rate as separate quantities.

### Effort

    Small / Medium / Large
        ↓
    predefined hour range

### Labor rate

    geography
    currency
    role/engineering level
    rate range
    provenance
    source/evidence

For the current India-focused MVP, the rate configuration should at minimum
carry:

    geography = India
    currency = INR

Do not silently use a global/default dollar rate.

Every rate must be one of:

    evidence-backed
    derived
    assumption

If an authoritative source cannot be established, keep the value as an
explicit assumption rather than inventing precision.

## Important

Do not merge:

    engineering effort
and
    labor rate

into one opaque cost constant.

The Economic Engine should derive:

    implementation cost =
        engineering effort × applicable labor rate

with the inputs remaining independently auditable.

## Acceptance Tests

- Geography and currency are present.
- No generic `$60/hour` style fallback remains.
- Provenance is attached.
- Changing labor rate does not change engineering effort.
- Changing effort band does not change labor rate.

---

# 4. Priority 1 — Centralize Calibration

## Problem

There are currently 23 calibration parameters, all marked `assumed`.

This is acceptable for an MVP if honest, but the parameters must not be
scattered or presented as universal truths.

## Fix

Keep a single versioned calibration object.

Every parameter should contain:

    value
    provenance
    rationale
    version

Classify each parameter as:

    evidence
    derived
    assumption

Do not force external research onto inherently product-specific parameters.

Examples that may reasonably be externally grounded:

- engineering labor rates;
- implementation effort benchmarks;
- infrastructure/API pricing.

Examples that are more appropriately MVP calibration:

- scope points;
- effort thresholds;
- ranking weights;
- implementation modifiers.

For the latter, record:

    provenance = assumption
    rationale = MVP calibration
    version = 1

Later, these can be calibrated using actual project outcomes.

## Acceptance Tests

- All 23 parameters exist in one place.
- No duplicate calibration literals remain.
- Every parameter has provenance.
- Every assumption has a rationale.
- Calibration version is exposed.

---

# 5. Priority 1 — Harden Compliance Evidence

## Problem

Compliance metadata is populated but currently unsourced.

A registry entry must not claim:

    HIPAA = supported

without evidence.

This is particularly important because compliance can influence architecture
selection.

## Fix

For each compliance claim, use one of:

    supported + evidence_id
    unsupported + evidence_id/reason
    unknown

Prefer `unknown` over an unsupported assertion.

Compliance evidence should be tied to:

    implementation_id
    compliance standard
    scope/context
    source
    date/version
    limitations

Do not infer compliance from general marketing language.

## Acceptance Tests

- Unsourced compliance claim cannot qualify an implementation as compliant.
- `unknown` cannot satisfy a hard compliance requirement.
- Evidence-backed compliance can satisfy the corresponding constraint.
- Compliance behavior is deterministic.

---

# 6. Priority 1 — Evidence Registry Quality

## Problem

The evidence architecture is now structurally sound, but evidence quality and
coverage remain limited.

## Fix

Every evidence record should have a stable ID and sufficient context:

    evidence_id
    metric
    value/range
    unit
    sector
    geography
    population/context
    applicability
    limitations
    source
    publication date

Use `evidence_id` for all internal references.

Never match evidence by rendered citation text.

## Customer Support

Do not fabricate an automation benchmark.

Current behavior is correct if:

- credible productivity evidence exists;
- it remains productivity evidence;
- it is not converted into automation rate without supporting evidence.

If no genuine automation benchmark exists:

    provenance = llm_estimate
    confidence = lower

This is preferable to inventing a benchmark merely to make the two sectors
symmetrical.

---

# 7. Priority 1 — Centralize Scale Thresholds

## Problem

Scale thresholds were duplicated across ranking/scope logic.

## Fix

Keep exactly one canonical definition, e.g.:

    solution/constants.py

or the existing canonical configuration module.

All modules import the same thresholds.

Do not duplicate:

    medium_from
    large_from

in multiple modules.

## Acceptance Test

Change the canonical threshold once.

Verify ranking and scope calculations both change consistently.

---

# 8. Priority 1 — Complete Field-Quality Gating

## Problem

The estimator now considers more fields than the original interviewer
required-field set.

Field quality must cover every field actually consumed by the estimator.

## Fix

Maintain a canonical estimator-critical field definition based on the
estimator's real calculations.

Potential fields include:

    monthly_volume
    avg_handling_time
    worker_count
    current_tools
    data_readiness
    compliance_exposure
    existing_data
    integration facts

The exact list must be derived from actual estimator dependencies.

For each field:

    value
    provenance
    resolution status
    conflict status
    confidence

Then:

    field quality
        ↓
    field criticality
        ↓
    overall confidence/refusal

Rules:

- Low-quality optional field → confidence reduction.
- Low-quality important field → stronger reduction.
- Contradicted critical field → confidence capped / possible refusal.
- Missing critical field → `needs_more_information`.

Do not make every optional field a blocker.

---

# 9. Priority 2 — Improve Confidence Model

## Problem

The new evidence-weighted confidence model is better than the previous
penalty count, but confidence should continue to reflect:

- evidence quality;
- field importance;
- contradictions;
- estimate provenance;
- uncertainty/range width.

## Fix

Retain the evidence-weighted approach.

Conceptually:

    base_confidence =
        Σ(field_importance × evidence_quality)
        /
        Σ(field_importance)

Then apply penalties/floors for:

- wide estimate ranges;
- unresolved critical contradictions;
- missing benchmark evidence;
- assumptions on high-impact fields.

A severe contradiction in a critical field should cap confidence even if the
remaining evidence is strong.

Do not present confidence as a statistical probability unless the underlying
model supports that interpretation.

For the MVP, labels such as:

    High
    Medium
    Low

are sufficient.

---

# 10. Priority 2 — Task-Level Time Sanity Checks

## Problem

D3 reconciles task times against the observed aggregate, but individual
LLM-generated task times are not yet checked for obvious nonsense.

## Fix

Add lightweight validation only.

Check for:

- zero/negative values;
- impossible units;
- extremely small/large task durations;
- task estimates inconsistent with task type where a defensible rule exists;
- extreme aggregate divergence.

Do not build a large predictive task-duration model.

If a task looks suspicious:

    task_estimate_warning = true

and reduce confidence or require clarification where appropriate.

Do not silently invent a corrected value.

---

# 11. Priority 2 — Architecture-Specific Risk Controls

## Problem

Risk controls are now category-specific but can still be generic across
different implementation types.

## Fix

Controls should be associated with the selected implementation wherever
possible.

Example:

    n8n
    → retry
    → error branch
    → failure queue
    → execution monitoring

versus:

    custom_code
    → retry middleware
    → idempotency
    → structured logging
    → health checks

Risk generation should consider:

    risk category
    +
    selected pattern
    +
    selected implementation
    +
    provider/infrastructure
    +
    deployment mode

The LLM can phrase the explanation, but the underlying control catalog
should be registry-backed where possible.

---

# 12. Registry Hardening — Final Pass

The registry should remain small rather than becoming an enormous catalog.

For every implementation, verify:

    implementation_id
    pattern_id
    version
    implementation_kind
    capabilities
    compatibility
    deployment modes
    scale
    latency
    compliance
    strengths
    limitations
    controls
    evidence IDs
    last_reviewed

## Specific checks

### A. Per-implementation capability coverage

Never determine capability coverage by unioning all implementations belonging
to a pattern.

The selected implementation itself must cover the required capabilities.

### B. Custom-code coverage

Do not allow one universal `custom_code` implementation to qualify every
possible workflow.

Per-architecture custom implementations are preferred.

### C. Compliance

Unsupported compliance claims must not qualify a candidate.

### D. Versioning

Every registry entry should have:

    version
    last_reviewed

Do not create a complex automatic review scheduler for the MVP unless needed.

---

# 13. Acceptance Test Suite

After all fixes, rerun the existing A-L and ranking suites and add/retain the
following.

## A — Simple deterministic process

Expected:
- workflow/rules/low-code options are considered;
- unnecessary RAG/agent architecture is not automatically preferred.

## B — Knowledge-heavy support

Expected:
- RAG/knowledge implementation exists in the registry;
- required capabilities are covered;
- human escalation is represented where appropriate.

## C — High-risk workflow

Expected:
- human review/escalation appears;
- controls correspond to the selected implementation.

## D — Missing evidence

Expected:
- no fabricated benchmark;
- provenance remains estimate/assumption;
- confidence reflects the evidence gap.

## E — Invalid capability

Expected:
- schema validation rejects invalid capability;
- retry occurs;
- invalid capability cannot reach ranking.

## F — Workload weighting

Given:

    Task A = 80% workload, 20% automation
    Task B = 20% workload, 90% automation

Expected:

    overall = 0.80 × 0.20 + 0.20 × 0.90
            = 34%

Not 55%.

## G — Scope-sensitive effort

Compare:

    Case 1:
      low-code
      one simple integration
      low complexity

    Case 2:
      custom implementation
      multiple complex integrations
      compliance/deployment requirements

Expected:

    Case 2 > Case 1 in effort

while scope remains the primary driver.

## H — Reference alignment

For a document-processing case whose reference is
`document_pipeline`:

Expected:
- reference alignment affects ranking;
- reference/selected comparison is visible;
- deviations are explained.

## I — Ambiguous critical field

Expected:
- confidence decreases;
- severe ambiguity can produce `needs_more_information`;
- no silent confident assumption.

## J — Aggregate/task contradiction

Given:

    user total = 6 minutes
    model tasks = 4 + 5 + 3 minutes

Expected:

    model total = 12
    divergence recorded
    task proportions normalized
    reconciled task total = 6
    confidence affected

## K — Evidence ID stability

Change rendered citation text but retain the evidence ID.

Expected:
- provenance remains valid.

Change/remove the evidence ID.

Expected:
- evidence-backed provenance becomes invalid.

## L — Scale threshold consistency

Change the canonical scale threshold.

Expected:
- all dependent modules use the same threshold.

## M — Compliance evidence

Candidate with:

    HIPAA = unknown

must not satisfy:

    required HIPAA = true

Candidate with valid registry-backed evidence may satisfy it.

## N — Estimator/Economic Engine baseline consistency

Given the same AssessmentState:

    estimator labor baseline
    ==
    economic engine labor baseline

No divergence is permitted unless explicitly caused by a documented
downstream economic assumption.

---

# 14. Cleanup

Before freezing the estimator:

- Delete obsolete `_confidence` / `_field_quality` implementations.
- Remove duplicate constants.
- Remove duplicate schema definitions.
- Remove dead imports.
- Remove obsolete comments describing the pre-C1/C14 architecture.
- Ensure all calibration values come from the canonical calibration object.
- Ensure all evidence references use stable IDs.
- Ensure tests use the same canonical configuration as production code.

Rename the TODO section:

    "Needs a decision"

to:

    "Locked Design Decisions"

because D1-D3 are already resolved.

---

# 15. Locked Design Decisions

## D1 — Integration Complexity

Do not ask the user for a subjective integration-complexity band.

Derive it from integration facts.

A volunteered user value may remain as a cross-check only.

## D2 — Implementation Kind

Implementation kind is an effort modifier, not the primary driver.

Conceptually:

    base scope
    + implementation modifier
    + integration modifier
    + data/readiness modifier
    + compliance/deployment modifier

Scope remains primary.

## D3 — Conflicting Handling Times

When reliable and resolved:

    observed aggregate time = authoritative baseline

LLM task times:

    proportions / decomposition only

Reconcile task times to the observed aggregate.

Severe contradiction:

    lower confidence
    and/or needs_more_information

This policy must be shared by the estimator and Economic Engine.

---

# 16. Final Implementation Order

## Phase 1 — Cross-module correctness

1. Extract D3 into a shared reconciliation utility.
2. Update Economic Engine to use it.
3. Add estimator/economic baseline consistency tests.

## Phase 2 — Monetary defensibility

4. Add geography/currency to labor rates.
5. Add provenance/source to rates.
6. Separate effort assumptions from labor-rate assumptions.

## Phase 3 — Calibration and evidence

7. Centralize all calibration parameters.
8. Label each as evidence/derived/assumption.
9. Add rationale/version.
10. Harden evidence IDs and compliance evidence.

## Phase 4 — Estimator quality

11. Centralize scale thresholds.
12. Complete estimator-critical field-quality gating.
13. Improve confidence calibration.
14. Add task-level sanity warnings.
15. Add architecture-specific controls.

## Phase 5 — Cleanup

16. Remove stale confidence code.
17. Remove duplicate schemas/constants.
18. Clean obsolete comments/imports.
19. Rename TODO sections.
20. Run the complete acceptance suite.

---

# 17. Definition of Done

The AI Solution Estimator is considered finished when:

- C1–C14 pass.
- N1–N11 pass.
- D1–D3 are implemented consistently.
- Estimator and Economic Engine use the same labor-baseline reconciliation.
- Labor rates have geography/currency and explicit provenance.
- Calibration parameters are centralized and labelled.
- Compliance claims are evidence-backed or explicitly unknown.
- Evidence uses stable IDs.
- Critical field quality affects confidence/refusal.
- Task-level estimates receive basic sanity validation.
- Registry entries have implementation-level capability coverage.
- Risk controls are implementation-aware.
- Existing A-L/ranking/economic/scoring suites pass.
- No duplicate/stale calculation paths remain.
- No LLM output can bypass the deterministic architecture-selection boundary.

At this point, freeze the estimator.

Do not add more architecture, agents or frameworks unless a new acceptance
failure demonstrates a genuine need.
