# Sub-Agent Report Trace

- **Agent**: Explore
- **Session**: /MTg0OWVkM2MtYTgxYS00YmRjLTk3ODYtZjE4NGZmMTRjNDJi
- **Raw report chars**: 14811
- **Compressed (delivered to parent) chars**: 15619
- **Distinct files mentioned in raw report**: 19

## Spawn Prompt (what the parent asked)

Map the exact field names and types of these frozen-layer objects in the DeployIQ repo at c:/Users/risha/Desktop/deployIQ. I am writing report/assemble.py and must consume these objects without guessing field names. For EACH object report every field name + type + a one-line meaning. Report from the source files directly.

1. `calc/engine.py` → `EconomicResult` and every nested dataclass/pydantic model it contains: `CostLine`, `CostBreakdown` (fields incl. `lines`, `total()`, `completeness_note()`, `absent_lines`), the object behind `current_annual_cost`, the object behind `ai_state` (AI operating breakdown: residual labor, inference/API, infrastructure, human review, monitoring, maintenance, other recurring, total; inference_pricing_ids; currency_mismatch; token-usage assumptions), the object behind `first_year` (annual_cost_savings, first_year_net_benefit, monthly_net_benefit, payback_months, payback_statement, unit economics, quality comparison, freed capacity under CAPACITY_RETAINED). Also fields `labor_consistency` (task_based, workforce_based, status, verdict), `time_reconciliation` (dict keys), `benchmark` (available, calculated_unit_cost, benchmark, verdict), `baseline_basis`, `absent_components`, `realization_statement`, `uncertainty_statement`, `provenance_lineage`, `warnings`, `sanity`/`presentable_as_strong`.

2. `calc/sensitivity.py` → `SensitivityReport` (metric, baseline, note, rows/skipped/failed) and `VariableImpact` (variable, bounds, low outcome, high outcome, swing, provenance, source, direction, status/reason).

3. `calc/assessment_confidence.py` → `AssessmentConfidence` (level, reasons, ...).

4. `calc/driver_ranking.py` → `DecisionDrivers` (drivers, scores, uncertainty_callout, ...), `ScoreBundle`, `DriverImpact` (key, label, statement, driver_type, impact, dominant_quantity, confidence, uncertainty_type, relative_width, uncertainty_index, evidence_ids).

5. The three calibration audit table callables: `calc.calibration.audit_table()`, `calc.scoring_calibration.audit_table()`, `solution.calibration.all_calibration_params()` — what does each row/object look like (dict keys / attribute names incl. version, rationale, calibration_id/parameter_id/key, last_reviewed, unit)? Also `calc.calibration.CALIBRATION_VERSION`, `calc.scoring_calibration.SCORING_CALIBRATION_VERSION`.

Report the EXACT attribute names as they appear in code — do not paraphrase or guess. Quote the relevant dataclass/pydantic field blocks.

## Raw Report (what the sub-agent produced, pre-compression)

I'll map these frozen-layer objects directly from source. Let me start by locating the relevant files.Let me examine the key files' skeletons first.Let me read the core files.Now let me read the supporting modules that define the nested objects.Now let me read the actual field definitions for the nested classes.Let me read the `ai_annual_operating_cost` builder and the engine `run()` to see how the AI operating breakdown lines are keyed.Let me check the inference cost object and verify fields the user mentioned that don't appear on EconomicResult.Now let me read the remaining files: inference cost, sensitivity, assessment_confidence, driver_ranking classes, and the three calibration modules.Now the three calibration modules and the DriverType enum.Let me check the `time_reconciliation` source object (returned by `labor.authoritative_handling_time`).I have everything needed. Here is the complete field map.

## Exploration Report

**Confidence**: HIGH — every object read directly from source; calibration callables, versions, and nested dataclasses all verified.

**Files Examined**: `calc/engine.py`, `calc/models.py`, `calc/ai_state.py`, `calc/lifecycle.py`, `calc/labor.py`, `calc/benchmark_check.py`, `calc/inference.py`, `calc/sensitivity.py`, `calc/assessment_confidence.py`, `calc/driver_ranking.py`, `calc/calibration.py`, `calc/scoring_calibration.py`, `solution/calibration.py`, `lib/reconciliation.py`

**Key Tool Path**: listDirectory → skeleton → read_code (whole) → targeted reads.

### Findings

#### 1. `calc/engine.py` — `EconomicResult` (BaseModel, L77-112)
Fields (exact):
- `sector: Sector`
- `labor_realization: LaborRealization`
- `realization_statement: str`
- `labor_consistency: labor.LaborConsistency`
- `baseline_basis: str`
- `time_reconciliation: Optional[dict] = None`
- `current_annual_cost: CostBreakdown`
- `current_annual_total: RangeEstimate`
- `tasks: list[TaskEconomics] = []`
- `ai_operating: CostBreakdown`
- `ai_operating_total: RangeEstimate`
- `freed_capacity_value: Optional[RangeEstimate] = None`
- `implementation: CostBreakdown`
- `implementation_total: RangeEstimate`
- `first_year: lifecycle.FirstYearEconomics`
- `unit_economics: lifecycle.UnitEconomics`
- `benchmark: benchmark_check.BenchmarkCrossCheck`
- `quality_comparison: Optional[dict] = None`
- `reliability: Optional[dict] = None`
- `labor_rate_geography: Optional[str] = None`
- `inference_pricing_ids: list[str] = []`
- `inference_lineage: list[str] = []`
- `provenance_lineage: dict[str, list[str]] = {}`
- `warnings: list[str] = []`
- `absent_components: list[str] = []`

**IMPORTANT CORRECTIONS vs. what the user assumed:**
- There is **NO `uncertainty_statement`** and **NO `sanity`/`presentable_as_strong` field on `EconomicResult`**. Those live on **`DecisionDrivers`** (`uncertainty_statement`, L343) and in **`calc/economic_sanity.py`** (`EconomicSanity` with `presentable_as_strong` property, L44-52), which is consumed by `calc/economic_score.py` — not by the engine. `assemble.py` must get them from the scoring/driver layer, not `EconomicResult`.
- `first_year` does **not** itself contain `unit_economics`, `quality comparison`, or `freed capacity` — those are sibling top-level fields on `EconomicResult` (`unit_economics`, `quality_comparison`, `freed_capacity_value`).
- `quality_comparison` and `reliability` are stored as **`model_dump(mode="json")` dicts**, not pydantic objects (engine L413-414).

**Nested models:**

`calc/models.py` `CostLine` (BaseModel, L140-150): `key: str`, `label: str`, `amount: Optional[RangeEstimate] = None`, `status: LineStatus = LineStatus.KNOWN`, `note: str = ""`. Classmethod `absent(cls, key, label, note)` builds an ABSENT line. `LineStatus` enum (L130): `KNOWN = "known"`, `ABSENT = "absent"`.

`calc/models.py` `CostBreakdown` (BaseModel, L153-176): `label: str`, `lines: list[CostLine] = []`. Methods/properties: `known_lines` (property, known+non-null lines), `absent_lines` (property, ABSENT lines), `total() -> RangeEstimate` (sums known lines), `completeness_note() -> str`. **`lines` and `total()`/`absent_lines()`/`completeness_note()` are NOT serialized fields — `lines`/`label` are; the rest are methods/properties.**

`calc/ai_state.py` `TaskEconomics` (BaseModel, L75-87): `task: str`, `hitl: HitlMode`, `workload_share: float`, `current_cost: RangeEstimate`, `automation: RangeEstimate`, `residual_work_fraction: RangeEstimate`, `modelled_residual_labor: RangeEstimate`, `realized_residual_labor: RangeEstimate`, `human_review_cost: Optional[RangeEstimate] = None`, `freed_capacity_value: RangeEstimate`, `mechanism: str = ""`.

`calc/ai_state.py` `LaborRealization` (str Enum, L69-72): `COST_ELIMINATED = "cost_eliminated"`, `CAPACITY_RETAINED = "capacity_retained"`.

**Object behind `current_annual_cost`** = a `CostBreakdown` (built in `calc/current_state.py`). Its lines are accessible via `.lines` (each a `CostLine`), total via `.total()`.

**Object behind `ai_state`/`ai_operating`** = a `CostBreakdown` built by `ai_state.ai_annual_operating_cost()` (L236-281). The `CostLine` keys are exactly:
- `"residual_labor"` (label "Residual labor")
- `"human_review"` (label "Human review"; ABSENT if no task configured for review)
- `"inference"` (label "AI / API inference"; the `InferenceCost.line`, ABSENT if not derivable)
- `"maintenance"` (label "Maintenance"; ABSENT if no maintenance stage)
- `"infrastructure"` (label "AI infrastructure", always ABSENT — "not collected by the interviewer")
- `"monitoring"` (label "Monitoring", always ABSENT)
- `"other_recurring"` (label "Other recurring costs", always ABSENT)

The AI-operating **breakdown of residual labor / inference / infrastructure / human review / monitoring / maintenance / other recurring / total is expressed as these `CostLine` objects** (each with `key`, `label`, `amount`, `status`, `note`), NOT as named struct fields. `inference_pricing_ids`, `currency_mismatch`, token-usage assumptions and lineage come from the `InferenceCost` object (`calc/inference.py` L38-44): `line: CostLine`, `pricing_ids: list[str]`, `usage_assumption: Optional[str]`, `lineage: list[str]`, `currency: Optional[str]`, `currency_mismatch: Optional[str]`. Only `pricing_ids` and `lineage` are copied onto `EconomicResult`; `currency_mismatch` and `usage_assumption` are consumed inside `engine.run()` (L300-307, only `currency_mismatch` triggers a warning; `usage_assumption` is NOT surfaced on EconomicResult).

`calc/lifecycle.py` `FirstYearEconomics` (BaseModel, L31-40) — this is the **object behind `first_year`**: `implementation_cost: RangeEstimate`, `ai_annual_operating_cost: RangeEstimate`, `first_year_ai_cost: RangeEstimate`, `annual_cost_savings: RangeEstimate`, `first_year_net_benefit: RangeEstimate`, `monthly_net_benefit: RangeEstimate`, `payback_months: Optional[RangeEstimate] = None`, `payback_statement: str = ""`. (Unit economics and quality comparison are separate top-level `EconomicResult` fields, not here.)

`calc/lifecycle.py` `UnitEconomics` (BaseModel, L22-28): `current_unit_cost: Optional[RangeEstimate]`, `ai_unit_cost: Optional[RangeEstimate]`, `first_year_unit_cost: Optional[RangeEstimate]`, `current_valid_output: Optional[RangeEstimate]`, `ai_valid_output: Optional[RangeEstimate]`, `note: str = ""`.

`calc/labor.py` `LaborConsistency` (BaseModel, L95-116) — the **`labor_consistency`** object: `task_based: Optional[RangeEstimate] = None`, `workforce_based: Optional[RangeEstimate] = None`, `comparable: bool = False`, `divergence: Optional[float] = None`, `status: BaselineStatus = BaselineStatus.UNRESOLVED`, `primary: Optional[RangeEstimate] = None`, `secondary: Optional[RangeEstimate] = None`, `primary_basis: str = ""`, `verdict: str = ""`. Property `needs_more_information -> bool`. `BaselineStatus` enum (L65-71): `CONSISTENT`, `DIVERGENT`, `TASK_DERIVED`, `WORKFORCE_ONLY`, `UNRESOLVED`.

**`time_reconciliation`** (dict) — the `model_dump(mode="json")` of `lib/reconciliation.py` `TimeReconciliation` (L44-56). Dict keys: `user_total_minutes`, `model_total_minutes`, `divergence`, `severity` (a DivergenceSeverity enum serialized to value), `reconciled`, `reconciled_times`, `shares`, `authoritative_total_minutes`, `total_provenance`, `statement`, `warnings`. (Properties `blocks_estimate` and `confidence_penalty` are NOT serialized.)

`calc/benchmark_check.py` `BenchmarkCrossCheck` (BaseModel, L25-32) — the **`benchmark`** object: `available: bool`, `calculated_unit_cost: Optional[RangeEstimate] = None`, `benchmark: Optional[RangeEstimate] = None`, `benchmark_citation: str = ""`, `benchmark_provenance: str = ""`, `verdict: str = ""`, `statement: str = ""`.

`provenance_lineage` — `dict[str, list[str]]`; keys built in `engine.run()` L391-401: `labor_baseline`, `automation_rate`, `task_automation`, `human_review`, `maintenance`, `engineering_effort`, and optional `inference`. Values are lists of provenance kind strings.

`absent_components` — `list[str]`, formatted as `"{line.label} ({breakdown.label})"` for every absent line across current/ai_ops/impl (engine L381-382). `warnings` — `list[str]`. `realization_statement` — `str` (engine L311-318).

#### 2. `calc/sensitivity.py`
`VariableImpact` (BaseModel, L46-58): `variable: str`, `label: str`, `provenance: Provenance`, `source: str`, `baseline_metric: float`, `low_metric: float`, `high_metric: float`, `swing: float`, `direction: str`, `bounds: str = ""`, `failed: Optional[str] = None`. (Note: the user's assumed names `low`/`high` outcome are actually `low_metric`/`high_metric`; `provenance`+`source` are fields, `direction` is a field, `failed` is `Optional[str]`.)

`SensitivityReport` (BaseModel, L60-69): `metric: str`, `baseline: float`, `impacts: list[VariableImpact] = []`, `skipped: list[str] = []`, `note: str = <default text>`. (There is **NO `rows` field** — rows are `impacts`; `skipped` exists; no `failed` field on the report itself — failure lives on each `VariableImpact.failed`.)

Also `SensitivityVariable` (BaseModel, L31-43): `key`, `label`, `baseline: float`, `min: float`, `max: float`, `unit: str = "multiplier"`, `provenance: Provenance = Provenance.ASSUMED`, `source: str = ""`; property `has_range`.

#### 3. `calc/assessment_confidence.py` — `AssessmentConfidence` (BaseModel, L26-32)
`level: str = "medium"`, `field_quality_level: str = "medium"`, `field_quality_score: float = 0.0`, `reasons: list[str] = []`, `blocking: list[str] = []`, `capped_reason: Optional[str] = None`.

#### 4. `calc/driver_ranking.py`
`DriverImpact` (BaseModel, L319-337): `key: str`, `label: str`, `statement: str`, `driver_type: DriverType = DriverType.MODEL_ESTIMATE`, `impact: float = 0.0`, `per_quantity: dict[str, float] = {}`, `payback_status: str = "ok"`, `dominant_quantity: str = ""`, `confidence: str = "medium"`, `provenance: str = "estimated"`, `uncertainty_type: str = "none"`, `relative_width: Optional[float] = None`, `uncertainty_index: Optional[float] = None`, `evidence_ids: list[str] = []`, `evidence_note: str = ""`.

`DecisionDrivers` (BaseModel, L340-354): `drivers: list[DriverImpact] = []`, `uncertainty_callout: Optional[DriverImpact] = None`, `uncertainty_statement: str = ""`, `scores: ScoreBundle`, `method: str = <default long text>`.

`ScoreBundle` (BaseModel, L90-97): `economic: Score`, `feasibility: Score`, `risk: Score`, `composite: Score`, `result: EconomicResult`, `confidence: Optional[dict] = None`; method `value(key)`.

`DriverType` (str Enum, L61-70): `BUSINESS_FACT="business_fact"`, `MODEL_ESTIMATE="model_estimate"`, `DATA_COVERAGE="data_coverage"`, `UNCERTAINTY="uncertainty"`.

#### 5. Calibration audit tables
**`calc.calibration.CALIBRATION_VERSION = 1`** (L18). `CalibratedRange` (L24-47): `calibration_id: str`, `min: float`, `max: float`, `unit: str`, `rationale: str`, `provenance: Provenance = Provenance.ASSUMED`, `version: int = CALIBRATION_VERSION`, `last_reviewed: str = "2026-08-19"` (module `LAST_REVIEWED`). Property `key` returns `calibration_id`; properties `mid`, `as_range()`.

`calc.calibration.audit_table() -> list[dict]` (L116-133) — each row dict keys: `calibration_id`, `min`, `max`, `unit`, `provenance`, `rationale`, `version`, `last_reviewed`; PLUS one extra employer-load row appending `status` key (that row also carries the same 8 keys + `status`). Note `provenance` is emitted as `.value` string here.

**`calc.scoring_calibration.SCORING_CALIBRATION_VERSION = 1`** (L20). `ScoringParam` (L24-31): `parameter_id: str`, `value: float`, `unit: str`, `rationale: str`, `provenance: Provenance = Provenance.ASSUMED`, `version: int = SCORING_CALIBRATION_VERSION`, `last_reviewed: str = "2026-08-19"`. Note: no `key` accessor here — it's `parameter_id`.

`calc.scoring_calibration.audit_table() -> list[dict]` (L196-213): rows are `ScoringParam.model_dump(mode="json")` → keys `parameter_id`, `value`, `unit`, `rationale`, `provenance`, `version`, `last_reviewed`. The escape-fraction rows (L207-212) additionally add `min` and `max` keys.

**`solution.calibration.all_calibration_params() -> list[CalibrationParam]`** (L170-172) — returns `CalibrationParam` objects (NOT dicts). `CalibrationParam` (L20-30): `key: str` (parameter_id), `value: float`, `unit: str = "scope_points"`, `rationale: str = ""`, `provenance: Provenance = Provenance.ASSUMED`, `version: int = CALIBRATION_VERSION`, `last_reviewed: str = ""` (empty means never reviewed). `solution.calibration.CALIBRATION_VERSION = 1` (L17). The `all_params()`/`all_calibration_params()` return live objects, so `assemble.py` accesses via attributes, not dict keys.

### Recommendation
In `report/assemble.py`, consume `EconomicResult` with `.current_annual_cost.total()`, `.ai_operating.lines` (each `CostLine` → `.key`/`.label`/`.amount`/`.status`/`.note`), `.first_year.*`, `.benchmark.*`, `.provenance_lineage`, `.time_reconciliation` (dict), `.warnings`, `.absent_components`. Get `sanity`/`uncertainty_statement` from the scoring/driver layer (`economic_sanity.EconomicSanity` and `DecisionDrivers.uncertainty_statement`), since they are NOT on `EconomicResult`. Read calibration values as attributes from `CalibrationParam`/`CalibratedRange` objects or dict keys from the `audit_table()` return values per the exact lists above.

### Not covered / Uncertain
- I did not read the `DivergenceSeverity` enum values in `lib/reconciliation.py` (only the class body); its `.value` strings are the `severity` dict entry but I did not enumerate the members. Not needed for field names.
- `calc/current_state.py` (the `CostBreakdown` behind `current_annual_cost`) was not opened, but since the type is `CostBreakdown`, the generic `CostLine`/`CostBreakdown` schema above fully describes it.
- The `Score` model fields (used inside `ScoreBundle`) were not enumerated — out of scope for the requested objects.

## Compressed Report (what the parent actually received)

<HYDRATION_REPORT>
I'll map these frozen-layer objects directly from source. Let me start by locating the relevant files.Let me examine the key files' skeletons first.Let me read the core files.Now let me read the supporting modules that define the nested objects.Now let me read the actual field definitions for the nested classes.Let me read the `ai_annual_operating_cost` builder and the engine `run()` to see how the AI operating breakdown lines are keyed.Let me check the inference cost object and verify fields the user mentioned that don't appear on EconomicResult.Now let me read the remaining files: inference cost, sensitivity, assessment_confidence, driver_ranking classes, and the three calibration modules.Now the three calibration modules and the DriverType enum.Let me check the `time_reconciliation` source object (returned by `labor.authoritative_handling_time`).I have everything needed. Here is the complete field map.

## Exploration Report

**Confidence**: HIGH — every object read directly from source; calibration callables, versions, and nested dataclasses all verified.

**Files Examined**: `calc/engine.py`, `calc/models.py`, `calc/ai_state.py`, `calc/lifecycle.py`, `calc/labor.py`, `calc/benchmark_check.py`, `calc/inference.py`, `calc/sensitivity.py`, `calc/assessment_confidence.py`, `calc/driver_ranking.py`, `calc/calibration.py`, `calc/scoring_calibration.py`, `solution/calibration.py`, `lib/reconciliation.py`

**Key Tool Path**: listDirectory → skeleton → read_code (whole) → targeted reads.

### Findings

#### 1. `calc/engine.py` — `EconomicResult` (BaseModel, L77-112)
Fields (exact):
- `sector: Sector`
- `labor_realization: LaborRealization`
- `realization_statement: str`
- `labor_consistency: labor.LaborConsistency`
- `baseline_basis: str`
- `time_reconciliation: Optional[dict] = None`
- `current_annual_cost: CostBreakdown`
- `current_annual_total: RangeEstimate`
- `tasks: list[TaskEconomics] = []`
- `ai_operating: CostBreakdown`
- `ai_operating_total: RangeEstimate`
- `freed_capacity_value: Optional[RangeEstimate] = None`
- `implementation: CostBreakdown`
- `implementation_total: RangeEstimate`
- `first_year: lifecycle.FirstYearEconomics`
- `unit_economics: lifecycle.UnitEconomics`
- `benchmark: benchmark_check.BenchmarkCrossCheck`
- `quality_comparison: Optional[dict] = None`
- `reliability: Optional[dict] = None`
- `labor_rate_geography: Optional[str] = None`
- `inference_pricing_ids: list[str] = []`
- `inference_lineage: list[str] = []`
- `provenance_lineage: dict[str, list[str]] = {}`
- `warnings: list[str] = []`
- `absent_components: list[str] = []`

**IMPORTANT CORRECTIONS vs. what the user assumed:**
- There is **NO `uncertainty_statement`** and **NO `sanity`/`presentable_as_strong` field on `EconomicResult`**. Those live on **`DecisionDrivers`** (`uncertainty_statement`, L343) and in **`calc/economic_sanity.py`** (`EconomicSanity` with `presentable_as_strong` property, L44-52), which is consumed by `calc/economic_score.py` — not by the engine. `assemble.py` must get them from the scoring/driver layer, not `EconomicResult`.
- `first_year` does **not** itself contain `unit_economics`, `quality comparison`, or `freed capacity` — those are sibling top-level fields on `EconomicResult` (`unit_economics`, `quality_comparison`, `freed_capacity_value`).
- `quality_comparison` and `reliability` are stored as **`model_dump(mode="json")` dicts**, not pydantic objects (engine L413-414).

**Nested models:**

`calc/models.py` `CostLine` (BaseModel, L140-150): `key: str`, `label: str`, `amount: Optional[RangeEstimate] = None`, `status: LineStatus = LineStatus.KNOWN`, `note: str = ""`. Classmethod `absent(cls, key, label, note)` builds an ABSENT line. `LineStatus` enum (L130): `KNOWN = "known"`, `ABSENT = "absent"`.

`calc/models.py` `CostBreakdown` (BaseModel, L153-176): `label: str`, `lines: list[CostLine] = []`. Methods/properties: `known_lines` (property, known+non-null lines), `absent_lines` (property, ABSENT lines), `total() -> RangeEstimate` (sums known lines), `completeness_note() -> str`. **`lines` and `total()`/`absent_lines()`/`completeness_note()` are NOT serialized fields — `lines`/`label` are; the rest are methods/properties.**

`calc/ai_state.py` `TaskEconomics` (BaseModel, L75-87): `task: str`, `hitl: HitlMode`, `workload_share: float`, `current_cost: RangeEstimate`, `automation: RangeEstimate`, `residual_work_fraction: RangeEstimate`, `modelled_residual_labor: RangeEstimate`, `realized_residual_labor: RangeEstimate`, `human_review_cost: Optional[RangeEstimate] = None`, `freed_capacity_value: RangeEstimate`, `mechanism: str = ""`.

`calc/ai_state.py` `LaborRealization` (str Enum, L69-72): `COST_ELIMINATED = "cost_eliminated"`, `CAPACITY_RETAINED = "capacity_retained"`.

**Object behind `current_annual_cost`** = a `CostBreakdown` (built in `calc/current_state.py`). Its lines are accessible via `.lines` (each a `CostLine`), total via `.total()`.

**Object behind `ai_state`/`ai_operating`** = a `CostBreakdown` built by `ai_state.ai_annual_operating_cost()` (L236-281). The `CostLine` keys are exactly:
- `"residual_labor"` (label "Residual labor")
- `"human_review"` (label "Human review"; ABSENT if no task configured for review)
- `"inference"` (label "AI / API inference"; the `InferenceCost.line`, ABSENT if not derivable)
- `"maintenance"` (label "Maintenance"; ABSENT if no maintenance stage)
- `"infrastructure"` (label "AI infrastructure", always ABSENT — "not collected by the interviewer")
- `"monitoring"` (label "Monitoring", always ABSENT)
- `"other_recurring"` (label "Other recurring costs", always ABSENT)

The AI-operating **breakdown of residual labor / inference / infrastructure / human review / monitoring / maintenance / other recurring / total is expressed as these `CostLine` objects** (each with `key`, `label`, `amount`, `status`, `note`), NOT as named struct fields. `inference_pricing_ids`, `currency_mismatch`, token-usage assumptions and lineage come from the `InferenceCost` object (`calc/inference.py` L38-44): `line: CostLine`, `pricing_ids: list[str]`, `usage_assumption: Optional[str]`, `lineage: list[str]`, `currency: Optional[str]`, `currency_mismatch: Optional[str]`. Only `pricing_ids` and `lineage` are copied onto `EconomicResult`; `currency_mismatch` and `usage_assumption` are consumed inside `engine.run()` (L300-307, only `currency_mismatch` triggers a warning; `usage_assumption` is NOT surfaced on EconomicResult).

`calc/lifecycle.py` `FirstYearEconomics` (BaseModel, L31-40) — this is the **object behind `first_year`**: `implementation_cost: RangeEstimate`, `ai_annual_operating_cost: RangeEstimate`, `first_year_ai_cost: RangeEstimate`, `annual_cost_savings: RangeEstimate`, `first_year_net_benefit: RangeEstimate`, `monthly_net_benefit: RangeEstimate`, `payback_months: Optional[RangeEstimate] = None`, `payback_statement: str = ""`. (Unit economics and quality comparison are separate top-level `EconomicResult` fields, not here.)

`calc/lifecycle.py` `UnitEconomics` (BaseModel, L22-28): `current_unit_cost: Optional[RangeEstimate]`, `ai_unit_cost: Optional[RangeEstimate]`, `first_year_unit_cost: Optional[RangeEstimate]`, `current_valid_output: Optional[RangeEstimate]`, `ai_valid_output: Optional[RangeEstimate]`, `note: str = ""`.

`calc/labor.py` `LaborConsistency` (BaseModel, L95-116) — the **`labor_consistency`** object: `task_based: Optional[RangeEstimate] = None`, `workforce_based: Optional[RangeEstimate] = None`, `comparable: bool = False`, `divergence: Optional[float] = None`, `status: BaselineStatus = BaselineStatus.UNRESOLVED`, `primary: Optional[RangeEstimate] = None`, `secondary: Optional[RangeEstimate] = None`, `primary_basis: str = ""`, `verdict: str = ""`. Property `needs_more_information -> bool`. `BaselineStatus` enum (L65-71): `CONSISTENT`, `DIVERGENT`, `TASK_DERIVED`, `WORKFORCE_ONLY`, `UNRESOLVED`.

**`time_reconciliation`** (dict) — the `model_dump(mode="json")` of `lib/reconciliation.py` `TimeReconciliation` (L44-56). Dict keys: `user_total_minutes`, `model_total_minutes`, `divergence`, `severity` (a DivergenceSeverity enum serialized to value), `reconciled`, `reconciled_times`, `shares`, `authoritative_total_minutes`, `total_provenance`, `statement`, `warnings`. (Properties `blocks_estimate` and `confidence_penalty` are NOT serialized.)

`calc/benchmark_check.py` `BenchmarkCrossCheck` (BaseModel, L25-32) — the **`benchmark`** object: `available: bool`, `calculated_unit_cost: Optional[RangeEstimate] = None`, `benchmark: Optional[RangeEstimate] = None`, `benchmark_citation: str = ""`, `benchmark_provenance: str = ""`, `verdict: str = ""`, `statement: str = ""`.

`provenance_lineage` — `dict[str, list[str]]`; keys built in `engine.run()` L391-401: `labor_baseline`, `automation_rate`, `task_automation`, `human_review`, `maintenance`, `engineering_effort`, and optional `inference`. Values are lists of provenance kind strings.

`absent_components` — `list[str]`, formatted as `"{line.label} ({breakdown.label})"` for every absent line across current/ai_ops/impl (engine L381-382). `warnings` — `list[str]`. `realization_statement` — `str` (engine L311-318).

#### 2. `calc/sensitivity.py`
`VariableImpact` (BaseModel, L46-58): `variable: str`, `label: str`, `provenance: Provenance`, `source: str`, `baseline_metric: float`, `low_metric: float`, `high_metric: float`, `swing: float`, `direction: str`, `bounds: str = ""`, `failed: Optional[str] = None`. (Note: the user's assumed names `low`/`high` outcome are actually `low_metric`/`high_metric`; `provenance`+`source` are fields, `direction` is a field, `failed` is `Optional[str]`.)

`SensitivityReport` (BaseModel, L60-69): `metric: str`, `baseline: float`, `impacts: list[VariableImpact] = []`, `skipped: list[str] = []`, `note: str = <default text>`. (There is **NO `rows` field** — rows are `impacts`; `skipped` exists; no `failed` field on the report itself — failure lives on each `VariableImpact.failed`.)

Also `SensitivityVariable` (BaseModel, L31-43): `key`, `label`, `baseline: float`, `min: float`, `max: float`, `unit: str = "multiplier"`, `provenance: Provenance = Provenance.ASSUMED`, `source: str = ""`; property `has_range`.

#### 3. `calc/assessment_confidence.py` — `AssessmentConfidence` (BaseModel, L26-32)
`level: str = "medium"`, `field_quality_level: str = "medium"`, `field_quality_score: float = 0.0`, `reasons: list[str] = []`, `blocking: list[str] = []`, `capped_reason: Optional[str] = None`.

#### 4. `calc/driver_ranking.py`
`DriverImpact` (BaseModel, L319-337): `key: str`, `label: str`, `statement: str`, `driver_type: DriverType = DriverType.MODEL_ESTIMATE`, `impact: float = 0.0`, `per_quantity: dict[str, float] = {}`, `payback_status: str = "ok"`, `dominant_quantity: str = ""`, `confidence: str = "medium"`, `provenance: str = "estimated"`, `uncertainty_type: str = "none"`, `relative_width: Optional[float] = None`, `uncertainty_index: Optional[float] = None`, `evidence_ids: list[str] = []`, `evidence_note: str = ""`.

`DecisionDrivers` (BaseModel, L340-354): `drivers: list[DriverImpact] = []`, `uncertainty_callout: Optional[DriverImpact] = None`, `uncertainty_statement: str = ""`, `scores: ScoreBundle`, `method: str = <default long text>`.

`ScoreBundle` (BaseModel, L90-97): `economic: Score`, `feasibility: Score`, `risk: Score`, `composite: Score`, `result: EconomicResult`, `confidence: Optional[dict] = None`; method `value(key)`.

`DriverType` (str Enum, L61-70): `BUSINESS_FACT="business_fact"`, `MODEL_ESTIMATE="model_estimate"`, `DATA_COVERAGE="data_coverage"`, `UNCERTAINTY="uncertainty"`.

#### 5. Calibration audit tables
**`calc.calibration.CALIBRATION_VERSION = 1`** (L18). `CalibratedRange` (L24-47): `calibration_id: str`, `min: float`, `max: float`, `unit: str`, `rationale: str`, `provenance: Provenance = Provenance.ASSUMED`, `version: int = CALIBRATION_VERSION`, `last_reviewed: str = "2026-08-19"` (module `LAST_REVIEWED`). Property `key` returns `calibration_id`; properties `mid`, `as_range()`.

`calc.calibration.audit_table() -> list[dict]` (L116-133) — each row dict keys: `calibration_id`, `min`, `max`, `unit`, `provenance`, `rationale`, `version`, `last_reviewed`; PLUS one extra employer-load row appending `status` key (that row also carries the same 8 keys + `status`). Note `provenance` is emitted as `.value` string here.

**`calc.scoring_calibration.SCORING_CALIBRATION_VERSION = 1`** (L20). `ScoringParam` (L24-31): `parameter_id: str`, `value: float`, `unit: str`, `rationale: str`, `provenance: Provenance = Provenance.ASSUMED`, `version: int = SCORING_CALIBRATION_VERSION`, `last_reviewed: str = "2026-08-19"`. Note: no `key` accessor here — it's `parameter_id`.

`calc.scoring_calibration.audit_table() -> list[dict]` (L196-213): rows are `ScoringParam.model_dump(mode="json")` → keys `parameter_id`, `value`, `unit`, `rationale`, `provenance`, `version`, `last_reviewed`. The escape-fraction rows (L207-212) additionally add `min` and `max` keys.

**`solution.calibration.all_calibration_params() -> list[CalibrationParam]`** (L170-172) — returns `CalibrationParam` objects (NOT dicts). `CalibrationParam` (L20-30): `key: str` (parameter_id), `value: float`, `unit: str = "scope_points"`, `rationale: str = ""`, `provenance: Provenance = Provenance.ASSUMED`, `version: int = CALIBRATION_VERSION`, `last_reviewed: str = ""` (empty means never reviewed). `solution.calibration.CALIBRATION_VERSION = 1` (L17). The `all_params()`/`all_calibration_params()` return live objects, so `assemble.py` accesses via attributes, not dict keys.

### Recommendation
In `report/assemble.py`, consume `EconomicResult` with `.current_annual_cost.total()`, `.ai_operating.lines` (each `CostLine` → `.key`/`.label`/`.amount`/`.status`/`.note`), `.first_year.*`, `.benchmark.*`, `.provenance_lineage`, `.time_reconciliation` (dict), `.warnings`, `.absent_components`. Get `sanity`/`uncertainty_statement` from the scoring/driver layer (`economic_sanity.EconomicSanity` and `DecisionDrivers.uncertainty_statement`), since they are NOT on `EconomicResult`. Read calibration values as attributes from `CalibrationParam`/`CalibratedRange` objects or dict keys from the `audit_table()` return values per the exact lists above.

### Not covered / Uncertain
- I did not read the `DivergenceSeverity` enum values in `lib/reconciliation.py` (only the class body); its `.value` strings are the `severity` dict entry but I did not enumerate the members. Not needed for field names.
- `calc/current_state.py` (the `CostBreakdown` behind `current_annual_cost`) was not opened, but since the type is `CostBreakdown`, the generic `CostLine`/`CostBreakdown` schema above fully describes it.
- The `Score` model fields (used inside `ScoreBundle`) were not enumerated — out of scope for the requested objects.

---
[Exploration: Explore (deepseek-v4-flash) | 76854ms | 13 turns | 35 tools | 85913in/9314out tokens | status: completed]
</HYDRATION_REPORT>

CRITICAL: Write your final answer DIRECTLY from the findings above. Do NOT re-page files the sub-agent already covered (no whole-file skeleton/symbols/read on those paths). To confirm one specific cited line, a narrow sb_read_code range (<=40 lines) is allowed. Files the report did NOT cover stay fully readable — locate them with superbrain_listDirectory / superbrain_search instead of guessing.

---
**Explore coverage (harness):** opened 15 file(s) over 13 turn(s). listed but not opened: .. Treat any subsystem this report does not explicitly cover as UNVERIFIED, not absent - confirm with a direct read or say what you could not verify.
