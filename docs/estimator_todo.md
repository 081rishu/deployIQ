# Solution Estimator — Current TODO

Implements spec section 7. Boundary: the LLM decomposes and estimates;
the Registry constrains; Reference Solutions provide the baseline;
deterministic code filters, ranks, scopes and calculates.

Validate (no API key — the LLM is stubbed):

    python3 scripts/estimator_cases.py    # acceptance cases A-L, plus direct
                                          # C1/C4/C5/C9 and N1/N6 checks
    python3 scripts/ranking_cases.py      # tie case + adversarial A1/A2a/A2b/A3

134 checks pass across those two suites and the economic/scoring suites.

---

## Resolved

C1-C14 are implemented per `docs/deployIQ_solution_estimator_critique_fixes.md`.
Detail lives in that document and in the test file; kept here as a one-line
record only:

- **C1** unbacked `sourced` claims are downgraded and reported — `solution/evidence.py`
- **C2** effort band derived from scope factors — `solution/scope.py`
- **C3** hours and rate separate, cost derived from both — `solution/effort_bands.py`
- **C4** automation anchored against benchmark evidence, divergence flagged — `solution/evidence.py`
- **C5** workload shares derived from handling time — `solution/workload.py`
- **C6** workload-weighted interval bounds, labelled an interval — `solution/estimator.py`
- **C7** reference alignment as a weighted ranking term — `solution/ranking.py`
- **C8** decomposition validated against reference capabilities — `solution/capabilities.py`
- **C9** strict enum parse with constrained retry; substring matching removed — `solution/capabilities.py`
- **C10** interview quality gates confidence and refusal — `solution/estimator.py`
- **C11** canonical `RangeEstimate`/`Provenance` only — `schemas/assessment_state.py`
- **C12** confidence with reasons; operating-cost drivers exposed — `solution/schema.py`
- **C13** risks mapped to real controls per category — `solution/risks.py`
- **C14** integration complexity derived from scope — `solution/scope.py`

Two deliberate deviations from the fixes document, both reasoned in full there:
the canonical five-tag provenance vocabulary was kept rather than
re-introducing benchmark/evidence/llm_estimate/assumption; and C3's
`min x max` cost envelope was implemented as written, with the combination
recorded in the value's `source` so it stays auditable.

---

## Post-fix pass — N1-N11 resolved

Implemented per `docs/deployIQ_solution_estimator_postfix_spec.md`.
Validated by `scripts/estimator_cases.py` (acceptance cases A-L) and
`scripts/ranking_cases.py`.

- **N1** implementation kind is an effort modifier; scope stays primary (D2) — `solution/scope.py`
- **N2** subjective integration-complexity question dropped; `current_tools` required instead (D1) — `interviewer/fields.py`
- **N3** documented: task times are an estimated decomposition, not observations
- **N4** per-task times reconciled against the observed total; observed wins (D3) — `solution/workload.py`
- **N5** one canonical scale threshold — `solution/constants.py`
- **N6** all scope weights in a versioned calibration object, each `assumed` with a rationale — `solution/calibration.py`
- **N7** evidence fields (population / applicability / limitations) added to the packs
- **N8** structural `evidence_id` matching replaces citation-string comparison — `solution/evidence.py`
- **N9** evidence-weighted confidence replaces the penalty count — `solution/confidence.py`
- **N10** field quality assessed across every field the estimator consumes
- **N11** controls drawn from the selected implementation's registry catalog — `solution/risks.py`

Registry hardening (spec sections 12-13) landed with it:

- The single universal `custom_code` entry — which claimed every capability and
  so re-qualified every pattern — was split into per-architecture custom
  implementations. Knowledge-support capabilities now resolve to
  `rag_knowledge_assistant` alone; document capabilities drop from four
  patterns to two.
- Coverage is judged per implementation, not across a pattern's union.
- Compliance metadata populated, and compliance now drives implementation
  SELECTION as well as scoring, so a pattern is no longer scored on a
  non-compliant build when it has a compliant one available.
- `implementation_kind` is a declared enum; `version`, `last_reviewed` and a
  control catalog added per entry.
- A `rag_managed` implementation was added so acceptance case B can be
  exercised (registry coverage first, never bending ranking to force it).

### Caught during this pass
- Changing `kind` to an enum silently broke the reference deviation
  conditions, which still said `"custom"` against a canonical `"custom_code"`.
  A1 failed loudly and the conditions now reference the enum values.
- Weighted confidence alone still let one flat contradiction on a
  mid-importance field read as "high"; a contradiction anywhere the estimator
  reads now caps confidence at medium.

---

## Locked Design Decisions

All three are now LOCKED and implemented:

- **D1** stop asking for the subjective band; derive it from integration facts.
  A volunteered value is kept as a cross-check and the conflict is reported.
- **D2** implementation kind is a modifier, not the primary driver
  (low_code 0.0 / managed_service 0.75 / custom_code 2.0 against a base scope
  that typically runs 3-8).
- **D3** the user's observed aggregate wins when resolved and reliable; the
  model supplies proportions only. **This policy must now be mirrored in the
  Economic Engine** — spec 8.1 has the same open question about diverging
  labor formulations, and the two modules must not derive different baselines
  from the same assessment.

---

## Finesse pass — complete

Implemented per `docs/deployIQ_ai_estimator_finesse_todo.md`.

- **D3 extracted to `lib/reconciliation.py`** and adopted by BOTH the estimator
  and the Economic Engine, so one AssessmentState cannot produce two labor
  baselines. Acceptance test N asserts they agree, including under a 12-vs-6
  contradiction where the observed aggregate wins in both.
- **Labor rates** moved to `data/labor_rates.json` with geography, currency,
  role, provenance and a stable `rate_id`. Effort and rate stay independent —
  changing geography changes cost but not hours. A currency guard detects an
  INR rate paired with a USD baseline instead of absorbing it.
- **Compliance claims are evidence-backed or `unknown`.** Every previously
  asserted claim ("n8n supports gdpr", "custom_workflow supports hipaa") was my
  assertion, not evidence, and is now `unknown`. `data/compliance_evidence.json`
  exists and is honestly empty. Only a SUPPORTED claim with an `evidence_id`
  can satisfy a constraint.
- **Task-time sanity checks** flag non-positive, implausibly short and
  implausibly long durations. Nothing is silently repaired.
- **Cleanup:** superseded `_confidence` / `_field_quality` deleted from
  `estimator.py` (396 lines, down from 480), dead imports removed, the
  "Needs a decision" section renamed to "Locked Design Decisions".

Acceptance suite now covers A-N plus rate and sanity checks; ranking, economic
and scoring suites all pass.

### Behaviour deliberately changed
Ranking case A2a previously asserted that a HIPAA requirement selected a
"compliant" implementation. That only passed because the registry asserted
compliance without evidence. With claims now `unknown`, nothing satisfies
HIPAA, the gap is flagged, and A2a asserts the corrected behaviour. The
positive path (evidence-backed claim satisfies) moved to case M.

---

## Remaining work

**Evidence — the binding constraint now**
- [ ] `data/compliance_evidence.json` is empty. Until a real attestation is
      recorded, no architecture can be selected on compliance grounds, and any
      assessment with a compliance constraint reports a gap. That is correct,
      but it means the compliance term cannot currently discriminate.
- [ ] Both labor rates are `assumed`/`unverified`. The India rate exists so an
      India assessment is not silently costed in USD, but **the sector packs
      carry US wage data in USD**, so an India assessment currently has no
      matching wage evidence — the currency guard will fire. Sourcing India
      wage data is a prerequisite for that geography.
- [ ] Customer support still has no automation anchor. Correct per the finesse
      spec (a productivity uplift is not an automation rate), and it stays
      inert until genuine evidence exists.

**Calibration**
- [ ] 23 scope parameters remain `assumed`, versioned with rationales. Per the
      finesse spec these are appropriately MVP calibration, not external
      research targets — but they should be re-fitted once real project
      outcomes exist.
- [ ] Effort band hours are still assumptions.
- [ ] The anchor divergence (25 points) and time divergence (25/60/150%)
      thresholds are MVP calibrations.

**Registry**
- [ ] Four patterns, seven implementations. Deliberately small.
- [ ] `last_reviewed` is set once; nothing enforces re-review.

## Architectural guardrail (do not cross)
- The LLM never selects an architecture or pattern ID.
- The LLM never invents authoritative benchmarks or citations.
- The LLM never invents engineering hours or labor rates.
- The LLM never determines workload shares.
- Deterministic code performs filtering, ranking and calculation.
- Reference solutions must materially influence evaluation.
- Evidence and assumptions must remain distinguishable.
- Missing or ambiguous critical information must not silently become a
  confident estimate.
- Note: spec 7.4 permits the LLM to select an effort BAND. That is not
  architecture selection — but the deterministic scope model in
  `solution/scope.py` is preferred because it is testable.

## Not in scope now
- API endpoint for the estimator (deferred).
- Frontend (deferred).





### my speculation
So labor-rate TODO should now be:
Keep the current fully-loaded rate model intact temporarily.
Add an evidence-backed compensation layer rather than overwriting it.
Add India-specific salary benchmarks with stable source_ids.
Derive hourly compensation from annual compensation transparently.
Define the employer-load/overhead factor separately.
Produce the final fully-loaded rate from those components.
Add role/experience selection based on the estimated implementation.
Make geography an explicit assessment input.
Remove universal US fallback once geography is available.
Keep the currency mismatch guard.

This is considerably more defensible than simply changing your current 1200–2600 assumption to some new number from a salary website.



### So the next TODO should be very small:

P0

Source India engineering labor rates.
Add them to labor_rates.json with stable IDs/provenance.
Make geography an explicit AssessmentState/economic input.
Remove universal US fallback.
Test India → INR and US → USD independently.
Test unknown geography → no silent fallback.

P1
7. Add 1–2 genuinely sourced compliance attestations for the implementations you actually use in the MVP, if reliable sources are available.
8. Otherwise leave compliance as unknown.