# DeployIQ — AI Interviewer Proposed Fix Specification

## Purpose

The AI Interviewer is the **collection and conversation layer**. It must conduct
a natural adaptive conversation while collecting the minimum
decision-relevant information required by the downstream pipeline.

It must:
- collect and validate assessment facts;
- extract multiple facts from one response;
- preserve provenance;
- handle explicit unknowns without infinite questioning;
- stop when the assessment is analytically sufficient;
- never calculate, recommend, rank architectures, or make economic decisions.

Pipeline:

```text
Live/Text Conversation
        ↓
AI Interviewer
        ↓
AssessmentState
        ↓
Solution Estimator
        ↓
Economic Engine
        ↓
Scoring System
        ↓
Decision Drivers
        ↓
Report
```

---

## 1. Architectural Boundary

### LLM responsibilities

The LLM may:
- phrase questions naturally;
- acknowledge responses;
- maintain conversational continuity;
- interpret responses;
- extract facts;
- identify explicit uncertainty;
- phrase clarifications.

### Deterministic responsibilities

Code determines:
- which fields are missing;
- field priority;
- required vs optional fields;
- state validation;
- overwrite behavior;
- interview sufficiency;
- termination.

The LLM must never decide:
- architecture;
- economic attractiveness;
- scores;
- recommendations;
- compliance satisfaction.

---

## 2. Natural Conversation Warm-Up

The interviewer must not feel like a form from the first turn.

Introduce conversation phases:

```text
WARMUP
  ↓
DISCOVERY
  ↓
ANALYSIS
  ↓
CLARIFICATION
  ↓
READY / UNCERTAIN
```

### WARMUP

A natural opening may be:

> "Hi! I'm here to understand the process you're looking at and see what an
> AI implementation could realistically look like. What's your name?"

After the name:

> "Nice to meet you. What are you working on?"

A brief social question such as "How's your day going?" is allowed, but must
not become a long scripted small-talk sequence.

### Opportunistic extraction

If the user says:

> "I'm Rishabh, we're a BPO in India handling around 5,000 customer support
> tickets a month and we're thinking about automating triage."

extract all applicable facts:

```text
name = Rishabh
geography = India
sector = customer_support
monthly_volume = 5000
process = ticket triage
```

Do not subsequently ask for facts already supplied.

### Natural transition

Once meaningful context exists:

> "Got it — you're looking at automating ticket triage for a support
> operation. Can you walk me through how that works today?"

The transition must use the existing deterministic need-selection system, not a
fixed question tree.

---

## 3. Conversation Context vs AssessmentState

Conversational metadata should not pollute analytical state.

Conversation context may contain:

```text
name
warmup_completed
conversation_phase
recent conversational context
```

`AssessmentState` contains only assessment-relevant information.

Name and rapport must never affect economic calculations or scoring.

---

## 4. Geography — P0

Geography is Tier-1 because it affects:
- labor-rate selection;
- currency;
- economic calculations;
- evidence applicability.

Capture it directly or extract it from context.

If the user says:

> "Our team is split between India and the US."

do not silently select one. Ask which geography should be used for the
economic baseline.

Currency should be derived deterministically from supported geography.

Do not ask separately for currency when geography determines it.

If geography cannot resolve to a supported currency:

```text
UNRESOLVED
```

Do not default to USD.

---

## 5. Tiered Field Collection

Do not collect every possible field before completion.

### Tier 1 — Decision-critical

The exact list must be derived from the current AssessmentState schema and
downstream consumers.

At minimum, this includes the information required to establish:
- sector;
- process/workflow;
- geography;
- volume;
- labor/workforce basis;
- handling-time information;
- current tools;
- data readiness;
- applicable compliance requirements;
- sufficient economic baseline information.

A critical Tier-1 field cannot silently remain unresolved.

### Tier 2 — Important enrichment

Examples:
- current tooling/infrastructure cost;
- error/rework information;
- current quality metrics;
- process stages;
- workforce role details;
- implementation constraints;
- review/escalation details.

Collect these when they materially improve the assessment.

### Tier 3 — Optional

Non-critical context that does not materially affect the assessment.

Tier 3 never blocks completion.

---

## 6. Missing Information

Distinguish:

```text
known
unknown
not_applicable
unresolved
```

If the user says:

> "I don't know."

do not ask indefinitely.

After a bounded clarification attempt, mark the field appropriately.

Never fabricate a value to make the state complete.

The downstream estimator remains responsible for refusing a confident estimate
when a critical field is missing.

---

## 7. Multi-Field Extraction

Preserve existing multi-fill behavior.

Example:

> "We process about 10,000 invoices a month. Three people handle them,
> each invoice takes around five minutes, and we're currently using SAP."

Populate every confidently supported field from that answer.

Do not re-ask already supplied facts.

Every extracted field must retain value and provenance, plus existing
confidence/source metadata where supported.

---

## 8. State Validation

Do not use bare `setattr()` for unvalidated user/LLM-derived state.

Enable Pydantic assignment validation or an equivalent explicit validation
layer.

Invalid values such as:

```text
monthly_volume = "a lot"
fte_count = {"foo": "bar"}
handling_time = -15
```

must be rejected.

A failed update must preserve the previous valid value.

### Corrections

If the user says:

> "Actually, it's 15,000 tickets, not 10,000."

the new value replaces the old value. Do not append duplicate facts.

---

## 9. Range Handling

User-provided ranges must remain ranges.

Example:

> "Between 10,000 and 15,000 tickets a month."

must not silently become `12500`.

Likewise:

> "It takes roughly 5–10 minutes."

must remain a range.

If a downstream calculation derives a midpoint, that transformation must be
explicitly marked as derived.

---

## 10. Current Tooling

Do not ask for subjective integration-complexity labels.

Collect observable facts:
- current systems;
- APIs;
- databases;
- SaaS tools;
- workflow platforms;
- legacy systems;
- manual handoffs.

Integration complexity is derived downstream by deterministic estimator logic.

---

## 11. Economic Inputs

Collect economic information where practical.

Examples:

> "Are you paying for any software or infrastructure specifically for this
> process?"

For rework:

> "When something goes wrong, roughly how much additional work is required
> to fix it?"

Use sector-appropriate quality metrics.

Customer support:
- first-contact resolution;
- escalation rate;
- rework.

Document processing:
- exception rate;
- first-pass yield;
- STP.

If unavailable, preserve `ABSENT`/`UNKNOWN` according to field semantics.

Never assume 100% quality.

---

## 12. Workforce / Labor Information

If `worker_role` is consumed by the Economic Engine, map it to the canonical
labor vocabulary.

Example:

> "We have 12 accounts-payable clerks."

should resolve to the canonical AP/process-labor role when unambiguous.

If ambiguous, clarify rather than silently choosing a role.

Do not allow free-text role descriptions to silently miss the labor registry.

---

## 13. Process Stages

Inspect whether `process_stages` has an actual downstream consumer.

If consumed, wire it correctly.

If not consumed by any downstream module or report, remove it from the
interviewer contract rather than collecting dead data.

Every retained interviewer field should have a downstream consumer or an
explicit reporting purpose.

---

## 14. Compliance Requirements

Normalize user compliance requirements to the canonical vocabulary expected
by the Solution Registry/evidence layer.

Examples:

```text
"HIPAA" → HIPAA
"We need to comply with GDPR" → GDPR
```

Flow:

```text
User statement
    ↓
Canonical requirement
    ↓
Evidence registry
    ↓
Deterministic compliance filter
```

Do not let the LLM select an architecture from the compliance requirement.

If the user says:

> "We have some healthcare privacy requirements."

ask for clarification rather than assigning HIPAA automatically.

---

## 15. Failure / Risk Inputs

Prefer observable workflow facts:
- what happens when the process fails;
- who reviews outputs;
- whether human approval is required;
- whether errors are reversible;
- whether an incorrect output creates financial/legal/customer impact.

Do not ask generic "What is the failure probability?" unless the user can
actually provide it.

Failure probability may be derived downstream from architecture and HITL
information.

Do not collect duplicate values that the estimator can deterministically
derive.

---

## 16. HITL / Human Review

Collect observed workflow constraints:
- whether human approval is required;
- which tasks require review;
- review fraction if known;
- escalation behavior.

Do not ask:

> "What percentage of AI errors will humans catch?"

That remains a calibration/estimation question downstream.

---

## 17. Termination

The interviewer may terminate when:

```text
Tier-1 fields are sufficiently complete
AND
no critical field is unresolved
AND
the downstream estimator quality gate passes
```

Do not terminate merely because:
- a fixed number of questions was asked;
- a fixed number of turns elapsed;
- the LLM thinks it has enough information.

If the user cannot provide a critical field after bounded clarification:

```text
mark unknown/unresolved
terminate with explicit insufficiency
```

The downstream estimator must then refuse a confident estimate where required.

---

## 18. Over-Asking Guard

Do not ask for a field when:
- already supplied;
- derivable from existing state;
- no downstream consumer;
- Tier 3 and immaterial;
- explicitly unknown and another clarification will not help.

Prefer one high-value question over several low-value questions.

---

## 19. Question Generation

Preserve the constrained output:

```json
{
  "acknowledgment": "...",
  "question": "..."
}
```

The deterministic controller supplies the question objective.

The LLM supplies:
- natural wording;
- acknowledgment;
- conversational transition;
- clarification phrasing.

The LLM does not choose which missing field matters most.

---

## 20. Voice and Text

Use one shared interviewer reasoning/state engine.

```text
                 ┌─────────────┐
Text input ─────→│             │
                 │ Interviewer │──→ AssessmentState
Voice input ────→│   Engine    │
                 │             │
                 └─────────────┘
```

Voice-specific code handles:
- STT;
- TTS;
- realtime transport;
- interruption/barge-in.

It must not implement separate assessment logic.

---

## 21. Required Tests

### Warm-up
- natural opening;
- name stays outside AssessmentState;
- brief rapport does not create unnecessary turns;
- warm-up transitions to discovery;
- warm-up answers can populate analytical fields.

### Geography
- explicit geography;
- contextual geography;
- conflicting geography asks clarification;
- no silent USD/default geography.

### Validation
- invalid assignment rejected;
- previous valid value preserved after invalid update;
- correction overwrites previous value.

### Ranges
- ranges remain ranges;
- no silent midpoint conversion.

### Multi-fill
- one response populates multiple fields;
- known fields are not re-asked.

### Tiering
- missing Tier 1 continues interview;
- missing Tier 2 does not necessarily block;
- Tier 3 never blocks completion.

### Unknown
- explicit "I don't know" does not loop indefinitely;
- bounded clarification;
- appropriate unknown/unresolved state.

### Compliance
- canonical normalization;
- ambiguous requirement asks clarification;
- interviewer never selects architecture.

### Downstream
Run a realistic completed interview through:

```text
AssessmentState
    ↓
Solution Estimator
    ↓
Economic Engine
    ↓
Scoring
```

and verify acceptance without fabricated values.

### Voice/Text
Equivalent content should produce equivalent analytical state.

---

## 22. Definition of Done

- [ ] Natural WARMUP exists.
- [ ] WARMUP transitions naturally into assessment.
- [ ] Name/rapport remain outside AssessmentState.
- [ ] Geography is collected.
- [ ] Currency is derived deterministically.
- [ ] No silent geography/currency fallback.
- [ ] Tier 1/2/3 collection policy exists.
- [ ] Multi-field extraction works.
- [ ] State assignment is validated.
- [ ] Corrections overwrite old values.
- [ ] Ranges remain ranges.
- [ ] Tooling facts are collected instead of subjective complexity.
- [ ] Compliance is normalized but never decided by the LLM.
- [ ] Sector-appropriate quality metrics are used.
- [ ] Explicit unknowns do not cause infinite loops.
- [ ] Termination is deterministic.
- [ ] Over-asking guard remains active.
- [ ] Question generation remains constrained.
- [ ] Voice and text use the same interviewer engine.
- [ ] Downstream acceptance test passes.
- [ ] Provenance survives the interview.

---

## 23. Architectural Guardrails

Never introduce:
- fixed questionnaire trees;
- scripted sector → volume → FTE → handling-time sequences;
- LLM-selected missing fields;
- LLM-selected architecture;
- fabricated values for missing Tier-1 fields;
- generic "Medium complexity" questions;
- automatic compliance decisions;
- a separate voice reasoning engine;
- unnecessary agent frameworks.

The interviewer should remain:

> **A natural conversational intake agent whose deterministic controller
> decides what information is needed, while the LLM decides how to ask for it.**

The conversation should feel natural while the resulting analytical state
remains deterministic, auditable, and safe.
