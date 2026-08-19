# Alternatives (MVP spec 11) — implementation record & open items

## STATUS: **FROZEN** (2026-08-19)

Implemented in `solution/alternatives.py`, validated by
`python3 scripts/alternatives_cases.py` — 81 assertions, and 435 across all
seven suites.

Section 11 is closed. Do not add functionality to it. Reopen only if a test
exposes a correctness defect — a fabricated alternative reaching the output, a
guard failing to strip an LLM claim, the primary selection being modified, or
a hard constraint not being applied.

The remaining LOW items below are **report/product decisions, not analytical
defects**, and are recorded for the report layer rather than for this module.

```
Interviewer       frozen
Estimator         frozen
Economic Engine   frozen
Scoring           frozen
Decision Drivers  frozen
Alternatives      frozen   <- this pass
```

Alternatives is the first module built on top of the four frozen analytical
layers rather than inside them. It reads a completed `SolutionEstimate` and
never writes to one.

---

## What was built

```
AssessmentState + SolutionEstimate
        |
        v
required capabilities            (taken from the estimate, not re-decomposed)
        |
        v
registry candidate space         every (pattern, implementation) pair
        |
        v
HARD filters                     capability coverage + evidence-backed compliance
        |                        — the SAME functions the primary path uses
        v
materiality filter               (pattern, implementation_kind) dedupe
        |
        v
metadata sufficiency             architecture text + strengths + limitations
        |
        v
<= 3 alternatives                ordered by the primary ranker's own score
        |
        v
LLM explains                     fixed id list, output passes guard()
```

### Files

| File | Role |
|---|---|
| `solution/alternatives.py` | the whole module: selection, comparison, guard |
| `solution/schema.py` | `Alternative`, `AlternativeComparison`, `AlternativesResult`, `RejectedAlternative`, and the `AlternativeSource` / `DifferenceKind` / `HumanInvolvement` enums |
| `solution/ranking.py` | refactor only — `score_candidate` / `ranking_context` extracted so alternatives reuse the identical scorer |
| `solution/patterns.py` | `rules_based_workflow` + `n8n_rules` / `make_rules` / `custom_rules` |
| `solution/performance.py` | `rule_coverage` / `fallthrough_rate` metrics for the new pattern |
| `solution/calibration.py` | `AlternativesCalibration`; `CalibrationParam` gained `unit` + `last_reviewed` |
| `lib/compliance.py` | evidence composition for the rules builds; `known_standards()` / `supported_standards()` |
| `scripts/alternatives_cases.py` | acceptance cases A–Q, one per spec clause |

### Spec coverage

| Clause | How it is enforced |
|---|---|
| 11.1 registry-only candidates | the candidate list is built from `patterns.all_patterns()` before any LLM call; the LLM is handed that fixed list |
| 11.1 hard constraints + compliance | `ranking.covers_compliance_by_evidence` — the same evidence registry that filters the primary |
| 11.1 materially different | `(pattern_id, implementation_kind)`; a vendor swap is not a different approach |
| 11.1 sufficient metadata | architecture text + ≥1 strength + ≥1 limitation, else rejected with a reason |
| 11.1 never pad | a GDPR-constrained assessment surfaces exactly one alternative (case E) |
| 11.3 comparison axes | approach/strengths/limitations from the registry; complexity from `solution.scope`; performance from `solution.performance`; risks from `solution.risks` |
| 11.4 no override, no second score | `derive()` never mutates the estimate (asserted byte-identical); display order reuses the ranker's existing score |
| 11.5 LLM boundary | `guard()` strips any sentence carrying a digit, and any invented id is discarded |
| 11.6 not a recommendation | directive phrasing stripped; conditional preference deliberately preserved; `is_recommendation` is a constant `False` on the payload |
| 11.7 uncertainty | assumed performance metrics, scale shortfalls and unverified compliance metadata are all declared per alternative |
| 11.8 qualitative only | no economics; `economics_included` is a constant `False` |

### Three decisions worth recording

**Same architecture, different implementation model counts as material.**
The registry offers `document_pipeline` as both a low-code and a custom build,
and 11.2 names "low-code automation" and "custom implementation" as separate
alternative types. Treating only a different *pattern* as material would have
hidden the build-vs-buy choice entirely.

**Compliance headroom is read from the evidence registry, never from the
registry's inline claims.** `Compatibility.compliance` was reset to UNKNOWN
across the board in an earlier pass, so comparing those claims would have
reported headroom based on which entry happened to be hand-updated —
`make_rules` would have looked more compliant than `make` for no evidential
reason. The comparison now uses `lib.compliance.supported_standards`, the same
authority the hard filter uses.

**A digit in generated prose is always wrong.** Every number in this section
comes from a registry-backed or code-derived field, so the guard does not need
to judge whether a figure is accurate — it can reject all of them and lose
nothing. Stripping is per sentence, so one bad claim costs one sentence rather
than the whole explanation.

---

## OPEN ITEMS

### 1. `rules_based_workflow` — CLOSED

Added to the Solution Registry with three implementations: `n8n_rules`,
`make_rules`, `custom_rules`. These are the same platforms as their AI
counterparts **with the model taken out of the loop**, which is the entire
point of the pattern.

| Metadata | Value |
|---|---|
| capabilities | ingest, classify, route, validate, post_process, human_escalate, human_review |
| **not** declared | `generate`, `extract` |
| complexity | SMALL (low-code), MEDIUM (custom — a real build, but no model to select, prompt, evaluate or maintain) |
| deployment | hybrid / cloud / hybrid |
| providers | **none** — no model-bearing provider on any of the three |
| compliance | evidence registry only; `n8n_rules` and `custom_rules` have no attestations and claim none |
| performance | `rule_coverage`, `fallthrough_rate`, `tool_execution_reliability` — all tagged `assumed` |

**Declining `generate` and `extract` is the load-bearing decision.** Capability
coverage is a HARD filter, so claiming a capability means "this can do it for
this assessment". A ruleset cannot write language or read an unstructured
invoice, so the pattern is correctly inert for those workflows and active for
workflows that genuinely need no model — which is exactly when DeployIQ should
be able to say so. Case S asserts both halves.

**The compliance consequence is real, not cosmetic.** `lib/compliance.py`
evaluates an implementation as its composition; a rules build drops
`openai_api` from that composition because there is no model API to attest
for. `make_rules` therefore satisfies SOC 3, which `make` cannot, because the
model API was the component holding it back (case U).

**It does not disturb the primary selection.** The pattern is not a reference
baseline for either sector, so it competes at reference alignment 0.2 and
cannot displace a curated architecture. All seven suites pass unchanged
(cases S, T).

**The other two 11.2 categories were deliberately left out**, per instruction:
process redesign / simplification, and smaller or specialized ML model. They
remain reported at runtime in `categories_not_in_registry`.

### 2. `when_preferable` can legitimately be empty — LOW

A foreign pattern whose registry metadata differs in no comparable dimension
(same scale rating, same deployment, same latency, same effort band) yields
no demonstrable advantage. This is currently surfaced as an uncertainty
("no situation in which this would be preferable ... could be established").

Open question for the report layer: should such an alternative be shown at
all? Showing it is honest; hiding it is arguably more useful. **Not decided —
it is a product call, not a correctness one.**

### 3. Human involvement is declarative, not estimated — LOW

`HumanInvolvement` reads what the implementation *declares* in the registry.
It is not a per-task HITL estimate, because that would need a second LLM
decomposition per candidate — outside MVP scope per 11.8. Stated in each
alternative's `uncertainties`.

### 4. Status-quo gate — CLOSED

Moved into `solution/calibration.py` as `AlternativesCalibration`, a structure
separate from `ScopeCalibration` because it answers a different question — not
"how big is this build" but "is carrying on unchanged still a live option".

```
parameter_id   status_quo_automation_ceiling
value          40.0
unit           percent_automation_upper_bound
provenance     assumed
version        1
last_reviewed  2026-08-19
```

`alternatives.STATUS_QUO_CEILING` is the calibration object itself, not a copy
of the number, so there is no second literal to drift (case V). A new
`all_calibration_params()` discloses it alongside the 23 scope parameters.

`CalibrationParam` gained `unit` and `last_reviewed`. **An empty
`last_reviewed` means never formally reviewed, not reviewed and found
acceptable** — the pre-existing scope weights were left empty rather than
backfilled with a date that would misrepresent them.

### 5. No API surface yet — MEDIUM (section 13 work, not section 11)

`derive()` is not reachable over HTTP. `api/ai_solution.py` and
`api/report.py` remain unbuilt, which is section 13 work; alternatives should
be exposed alongside the estimate when that lands rather than on an endpoint
of its own.

---

## What alternatives deliberately does NOT do

- select or re-rank the primary architecture
- calculate any score of its own
- calculate economics per alternative
- let the LLM introduce, rank, cost or recommend anything
- show a fixed number of alternatives
