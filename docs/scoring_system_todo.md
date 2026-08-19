# Scoring System (spec 9) — Implementation Plan & Critique

Implements `calc/economic_score.py`, `feasibility_score.py`, `risk_score.py`,
`composite.py` and `driver_ranking.py` per spec section 9 and ARCHITECTURE
3.3/3.5. Pure Python, no LLM. Scores explain; Decision Drivers are the output.

Validate with `python3 scripts/scoring_cases.py` (25 checks, no API key).

---

## Done

### Every v4 [TBD] is now defined
- [x] 9.1 economic normalisation — piecewise-linear payback (<=6mo -> 100,
      >=24mo -> 0) at weight 0.60, benefit/cost saturating at ratio 2.0 at 0.40
- [x] 9.2 feasibility weights — data 0.45 / achievability 0.30 / integration 0.25
- [x] 9.3 risk combination — probability x impact, plus a reliability-gap
      penalty, with compliance as a hard flag rather than a numeric term
- [x] 9.4 composite weighting — 0.40 / 0.30 / 0.30, context only
- [x] 9.5 driver ranking — elasticity, (% change in score)/(% change in input)
- [x] 9.5 uncertainty callout — relative range width x elasticity
- [x] 9.7 inputs identified (calculation still open)

### Structural decisions taken beyond filling in numbers
- [x] **Scores are computable-or-not, never zero-by-default.** A missing input
      yields `computable=False`, a named missing-input list and band
      "not computable". A zero score and an unknown score mean opposite things.
- [x] **Compliance is a flag, not a term.** It forces Risk to 0, raises an
      explicit BLOCKER, and propagates into the composite. Verified: with
      economics at 98.3 the blocker still surfaces and is not averaged away.
- [x] **Composite refuses to compute from a partial set**, so it can never
      imply completeness the assessment lacks.
- [x] **Data readiness and failure impact became CATEGORIES.** Free text cannot
      deterministically produce a sub-score. `DataReadiness` and
      `ImpactSeverity` enums added, with FieldSpecs whose extraction hints let
      one answer fill both the description and the category (spec 10.4), so
      the interview does not grow by two questions in the common case.
- [x] **Failure probability is derived from the architecture's own metrics**
      (a failure metric directly, else 1 - accuracy), tagged DERIVED with the
      originating citation, rather than requiring a new LLM guess.
- [x] Every driver statement is generated in code from calculated values. The
      LLM's only role is rephrasing.

### Defect found by the validation harness and fixed
- [x] The uncertainty test asserted "the callout is no longer automation" after
      narrowing automation's range — which passed vacuously because the callout
      was already a different variable. Replaced with an assertion that
      narrowing a variable lowers ITS OWN uncertainty index (0.114 -> 0.015).

### Spec/doc changes
- [x] deployIQ_MVP.txt section 9 rewritten 9.1-9.8, v6 revision entry added
- [x] Six [TBD] open items marked RESOLVED; three new open items registered
- [x] Gartner claim verified before use: "through 2026, organizations will
      abandon 60% of AI projects unsupported by AI-ready data" (press release
      26 Feb 2025, from a Q3 2024 survey of 248 data management leaders). The
      spec records it as a PREDICTION justifying the WEIGHT, not as a number
      feeding the score.
- [x] Vendor-blog citation for probability x impact dropped — it is standard
      PMBOK/ISO 31000 practice and needs no such source. The CSIRO reference
      was omitted rather than cited unverified.

---

## Corrective pass — S1-S11 resolved. FROZEN.

Implemented per `docs/deployIQ_scoring_system_proposed_fix.md`.
Validated by `python3 scripts/scoring_cases.py`; 299 assertions pass across
all five suites.

| # | Fix | Where | Evidence |
|---|-----|-------|----------|
| S1 | Drivers ranked on UNBOUNDED economic quantities (annual benefit, first-year net benefit, payback), not score elasticity | `calc/driver_ranking.py` | economically active drivers keep non-zero impact at economic score 91.7 AND 30.0 |
| S2 | Uncertainty is TYPED: numeric_range / assumption_range / categorical / none | `calc/uncertainty.py` | a category returns `relative_width = None`, never 67% |
| S3 | Economic plausibility gate before normalisation | `calc/economic_sanity.py` | 0.1-0.7 month payback and 39x benefit/cost both flagged; score NOT capped |
| S4 | Bounds transparency on every score | `calc/models.py` | feasibility names the categorical inputs held fixed |
| S5 | Reliability gap is a calibrated categorical MODIFIER | `calc/scoring_calibration.py` | x1.0 / x1.15 / x1.40 / x1.80 bands; the arbitrary 0.5 penalty is gone |
| S6 | Severity ladder centralised | `calc/scoring_calibration.py` | one canonical ladder, no duplicate |
| S7 | HITL-aware residual failure probability | `calc/risk_score.py` | autonomous residual = raw 14%; human_review residual 2.1-7.0% |
| S8 | Composite weights centralised; precision reduced | `calc/composite.py` | 87 not 87.7; "summary indicator", never "overall decision score" |
| S9 | Interview quality drives Overall Assessment Confidence (9.7) | `calc/assessment_confidence.py` | a CONTRADICTORY critical field caps confidence at low while the score is unchanged |
| S10 | Driver TYPES separate business facts from data-coverage facts | `calc/driver_ranking.py` | "Labor represents the entire measured current cost **because only labor cost was supplied**" |
| S11 | Evidence ids attached to drivers where available | `calc/driver_ranking.py` | evidence informs confidence, never rank |

### The headline change
Under score elasticity, labor rate measured **0.024 at economic score 98.3 and
0.529 at 74.3** — a 22x swing caused by where the score sat on its curve, not
by the business. Ranking now measures the same variables against unbounded
economic quantities, and an economically active driver keeps a non-zero impact
in both the saturated and unsaturated case.

### Calibration registry (spec 27)
39 scoring parameters in `calc/scoring_calibration.py`, each with
parameter_id, value, unit, provenance, rationale and version. All tagged
`assumed`. No magic numbers remain in the scoring modules — feasibility
weights and economic thresholds are asserted to come from the registry.

### Two test assertions corrected rather than the code
- **S1-A** initially asserted that no driver may have zero impact. Wrong:
  `data_readiness` and `integration_complexity` genuinely have zero ECONOMIC
  impact — they move the feasibility score only. Scoped to economically active
  variables.
- The uncertainty callout case was rewritten for typed uncertainty; it now
  asserts the callout is a genuinely numeric input, and that narrowing a range
  lowers that variable's own index.

---

## Remaining work — not scoring defects

- [ ] Escape fractions per HITL mode (0.15-0.50 for human review) are MVP
      assumptions with stated rationale, not measured review effectiveness.
- [ ] Reliability modifier bands, severity ladder, composite weights and
      sanity thresholds are all product calibrations awaiting evidence.
- [ ] No economic input is benchmark-backed in the default fixture, so
      confidence is capped at medium by evidence coverage — correct behaviour,
      resolved by adding sourced inputs rather than by changing the scoring.

## Boundary (do not cross)
Scores explain; Decision Drivers are the output; the human decides. No
threshold on any score may produce a category, label, or recommendation. The
validation suite asserts that no driver statement contains a recommendation
word, and that check should stay.
