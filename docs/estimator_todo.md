# Solution Estimator — Locked Implementation Plan

Approved and locked. `[DEFINE]` inputs are treated as explicit assumptions made during implementation, documented and replaceable with sourced data later.

## P0
- [ ] P0.1 — Task-specific performance model + provenance
- [ ] P0.2 — Defensible effort bands + separate labor-rate source
- [ ] P0.3 — Validate RangeEstimate/task estimate schema

## P1
- [ ] Run capability decomposition tests
- [ ] Fix/validate capability normalization
- [ ] Fix workload-weighted automation
- [ ] Make reference architecture comparison functional
- [ ] Run the 3 adversarial cases
- [ ] Test incomplete AssessmentState
- [ ] Manually trace the entire estimator

## P2
- [ ] Move directly into the economic engine

---

## Architectural guardrail (keep in mind, do not add to estimator)
- Never let the LLM become the architecture selector again.
  - LLM: interpret/decompose + explain only.
  - Registry: constrain candidates.
  - Deterministic code: filter + rank.
  - Reference solution: provide the baseline.
- Any future change that lets the LLM pick a pattern ID directly is a regression.

## Not-in-scope-now
- API endpoint for the estimator (deferred).
- Frontend (deferred, as decided earlier).
