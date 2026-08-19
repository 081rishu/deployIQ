"""Deterministic report validation — spec 14.  [PRESENTATION LAYER]

A FAIL-CLOSED trust boundary between assembly and narration/rendering. It
validates an already-assembled `Report` (and, where the analytical cross-checks
require it, the `ReportInput` bundle it was assembled from) against the schema
and the frozen analytical invariants.

It performs NO analysis:
  * no economics, scores, ranking, architecture or alternative selection;
  * no threshold analysis;
  * no LLM call;
  * no repairing, guessing, midpointing, normalising or inferring.

If it cannot establish that the report is safe and internally consistent it
records an ERROR, and `valid` becomes False. Errors are surfaced, never
silently fixed. Warnings are limitations that can be disclosed without
invalidating the report.

The validator is deterministic: for the same Report and bundle it returns the
same result every time.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from report import assemble, evidence as ev
from report.schema import (
    FLAG_CURRENCY_UNRESOLVED,
    DriverClass,
    Figure,
    FigureStatus,
    GapKind,
    RangeSemantics,
    Report,
    ReportInput,
    ReportMode,
    StatementOrigin,
    Unit,
    ValidationResult,
)

# Directive/recommendation language that must not appear in report prose, other
# than inside a legitimate negated disclosure ("not a recommendation to build").
_DIRECTIVE_WORDS = (
    "should choose", "should build", "should automate",
    "best option", "winner", "go ahead", "proceed with",
    "ideal solution", "optimal solution", "clearly better",
    "superior", "viable", "compelling",
)
# Words that negate a "recommend" so that "not a recommendation" stays legal.
_NEGATIONS = ("not", "no ", "none", "never", "n't", "neither", "rather than")

# Range semantics that must never be relabelled as statistical.
_CONFIDENCE_LABELS = ("confidence interval", "probability interval",
                      "statistical confidence", "confidence range")

# Economic-looking numeric claims that must be traceable to a Figure/source if
# they appear in authored prose. This is deliberately NOT a crude "no digits"
# rule: version numbers, dates and identifiers are legitimate. Only a number
# that reads as a money/percent/measure claim is an orphan when it appears in
# an authored statement (which carries no figure).
_MONEY_SYMBOLS = r"\$\u20b9\u20ac\u00a3\u00a5"
_ECON_NUMBER = re.compile(
    r"(" + _MONEY_SYMBOLS + r"\s*\d"
    r"|\d\s*%"
    r"|\d+(?:,\d{3})*\s+(?:months?|hours?|days?|tickets?|units?|"
    r"months?|savings?|cost|million|billion)\b)", re.I)

_CANONICAL_TITLES = {
    1: "Executive Summary", 2: "Problem Definition", 3: "Current Process",
    4: "Current Cost", 5: "Proposed AI Solution", 6: "Alternative Solutions",
    7: "Implementation Requirements", 8: "AI Operating Cost",
    9: "Expected Benefits", 10: "Risks and Reliability", 11: "Assumptions",
    12: "External Sources", 13: "Sensitivity Analysis",
    14: "What to Validate Next",
}
_CANONICAL_KEYS = {
    1: "executive_summary", 2: "problem_definition", 3: "current_process",
    4: "current_cost", 5: "proposed_ai_solution", 6: "alternative_solutions",
    7: "implementation_reqs", 8: "ai_operating_cost", 9: "expected_benefits",
    10: "risks_and_reliability", 11: "assumptions", 12: "external_sources",
    13: "sensitivity_analysis", 14: "what_to_validate_next",
}


def directive_hits(text: str) -> list[str]:
    """Return the directive/recommendation phrases found in `text`.

    A `recommend` token is only flagged when its containing sentence is NOT
    negated, so the approved disclosure ("not a recommendation to build") is
    accepted while actual recommendation language is caught. This is the
    reusable mechanism that later also guards LLM narration.
    """
    lower = text.lower()
    hits: list[str] = []

    for m in re.finditer(r"recommend", lower):
        start = max(lower.rfind(".", 0, m.start()),
                    lower.rfind(";", 0, m.start()),
                    lower.rfind("\n", 0, m.start())) + 1
        sentence = lower[start:m.start()]
        if any(n in sentence for n in _NEGATIONS):
            continue
        hits.append("recommend")
        break

    for token in _DIRECTIVE_WORDS:
        if token in lower and token not in hits:
            hits.append(token)
    return sorted(hits)


def numeric_claims(text: str) -> list[str]:
    """Return the economic-looking numeric claims found in `text`.

    This is the guard narration reuses so the LLM cannot invent a figure. It is
    deliberately NOT a blanket digit ban: version numbers, dates and
    identifiers are legitimate. Only a number that reads as a money/percent/
    measure claim is flagged. The narration contract lets the LLM place a
    `{{FIGURE:key}}` placeholder instead of a raw number, and the deterministic
    system owns the value.
    """
    return [m.group(1) for m in _ECON_NUMBER.finditer(text)]


# Provenance terms that narration must never strengthen into a stronger claim.
# `stronger_word -> forbidden rewrites`. Detecting these in a rewritten text
# that the source did not contain is what rejects a provenance mutation.
_PROVENANCE_STRENGTH = {
    "estimated": ("measured", "observed", "established", "confirmed"),
    "assumed": ("established", "known", "confirmed", "verified", "proven"),
    "derived": ("observed", "measured", "recorded", "sampled"),
    "unknown": ("unlikely", "rare", "absent", "unlikely to"),
    "not computable": ("zero", "0", "nothing", "none"),
}


def provenance_mutation(source: str, rewritten: str) -> list[str]:
    """Return provenance-strengthening mutations between two texts.

    `source` is the deterministic statement; `rewritten` is the LLM's version.
    If the source carries a weak provenance term and the rewrite replaces it
    with a stronger claim (or the source states an absence and the rewrite
    fills a value), the mutation is reported so the narration is discarded.
    """
    s = source.lower()
    r = rewritten.lower()
    hits: list[str] = []
    for weak, strong_words in _PROVENANCE_STRENGTH.items():
        if weak in s:
            for strong in strong_words:
                if strong in r and strong not in s:
                    hits.append(f"{weak!r} -> {strong!r}")
    return hits


def _all_figures(report: Report) -> Iterable[Figure]:
    for section in report.sections:
        yield from section.figures


def _figure_by_key(report: Report) -> dict[str, Figure]:
    out: dict[str, Figure] = {}
    for f in _all_figures(report):
        out.setdefault(f.key, f)
    return out


def _section_gaps(report: Report) -> list:
    return [g for s in report.sections for g in s.gaps]


def _all_statements(report: Report) -> Iterable:
    for section in report.sections:
        yield from section.statements


class ReportValidator:
    """Runs every validation group against a Report (and an optional bundle)."""

    def __init__(self, report: Report, bundle: Optional[ReportInput]):
        self.report = report
        self.bundle = bundle
        self.result = ValidationResult()
        self.index = ev.build_index()

    # -- helpers ------------------------------------------------------------

    def _err(self, code: str, message: str, section: str = "") -> None:
        self.result.add_error(code, message, section)

    def _warn(self, code: str, message: str, section: str = "") -> None:
        self.result.add_warning(code, message, section)

    def _rule(self, name: str) -> None:
        self.result.rule_checked(name)

    # -- Group 1: structural integrity --------------------------------------

    def check_structure(self) -> None:
        self._rule("structural")
        sections = self.report.sections
        numbers = [s.number for s in sections]

        missing = [n for n in range(1, 15) if n not in numbers]
        # Refused-mode reports are intentionally sparse in the current approved
        # P2 shape (no proposed solution / benefits section). Enforcing all 14
        # sections here would make every refused report invalid by construction.
        if missing and self.report.mode is not ReportMode.REFUSED:
            self._err("missing_section",
                      f"missing canonical sections: {missing}")
        elif missing and self.report.mode is ReportMode.REFUSED:
            self._warn("missing_section_refused",
                       f"refused report omits canonical sections: {missing}")
        # Duplicate check applies only to canonical sections. Intentional
        # presentation groupings (Decision Drivers, Scores) carry number 0 and
        # legitimately share it — they must not be rejected (approved design).
        canonical_numbers = [n for n in numbers if n in range(1, 15)]
        duplicates = {n for n in canonical_numbers
                      if canonical_numbers.count(n) > 1}
        if duplicates:
            self._err("duplicate_section",
                      f"duplicate canonical section numbers: {sorted(duplicates)}")

        for section in sections:
            if section.number in _CANONICAL_TITLES:
                if section.key != _CANONICAL_KEYS[section.number]:
                    self._err("section_key_mismatch",
                              f"section {section.number} key {section.key!r} "
                              f"does not match canonical "
                              f"{_CANONICAL_KEYS[section.number]!r}",
                              section.key)
                if section.title != _CANONICAL_TITLES[section.number]:
                    self._err("section_title_mismatch",
                              f"section {section.number} title "
                              f"{section.title!r} does not match canonical "
                              f"{_CANONICAL_TITLES[section.number]!r}",
                              section.key)
            else:
                # A non-canonical section (e.g. Decision Drivers number 0,
                # Scores number 0) must not collide with the canonical 1..14.
                if 1 <= section.number <= 14:
                    self._err("unexpected_canonical_section",
                              f"section {section.number} "
                              f"({section.key!r}) is not a canonical section "
                              f"but carries a canonical number", section.key)

        if self.report.mode not in (ReportMode.FULL, ReportMode.PARTIAL,
                                    ReportMode.REFUSED):
            self._err("invalid_mode", f"invalid report mode "
                      f"{self.report.mode!r}")

        if self.report.mode is ReportMode.REFUSED \
                and not self.report.refusal_reason.strip():
            self._err("refused_without_reason",
                      "a refused report must state why it was refused")

        if self.report.manifest is None:
            self._err("missing_manifest", "report has no manifest")

    # -- Group 2 & 15: figure + range-semantics integrity --------------------

    def check_figures(self) -> None:
        self._rule("figure_integrity")
        self._rule("range_semantics")
        for f in _all_figures(self.report):
            if f.status is FigureStatus.KNOWN:
                self._check_known_figure(f)
            else:
                self._check_missing_figure(f)

    def _check_known_figure(self, f: Figure) -> None:
        if f.provenance is None and "provenance_unknown" not in f.flags:
            self._err("known_without_provenance",
                      f"{f.key}: a known figure requires provenance", f.key)
        if not (f.derivation or "").strip():
            self._err("known_without_derivation",
                      f"{f.key}: a known figure requires a derivation", f.key)
        if f.range_semantics is RangeSemantics.CATEGORY:
            if f.value_text is None:
                self._err("category_without_value",
                          f"{f.key}: categorical figure has no value_text",
                          f.key)
        else:
            if f.value_min is None or f.value_max is None:
                self._err("known_without_value",
                          f"{f.key}: a known numeric figure requires both "
                          f"bounds", f.key)
            elif f.value_min > f.value_max:
                self._err("min_gt_max",
                          f"{f.key}: value_min {f.value_min} > value_max "
                          f"{f.value_max}", f.key)
            elif f.range_semantics is RangeSemantics.POINT \
                    and f.value_min != f.value_max:
                self._err("point_differing_bounds",
                          f"{f.key}: POINT semantics but bounds differ "
                          f"({f.value_min} != {f.value_max})", f.key)

        if f.unit is Unit.MONEY:
            if not f.currency and FLAG_CURRENCY_UNRESOLVED not in f.flags:
                self._err("money_without_currency",
                          f"{f.key}: a money figure requires a currency unless "
                          f"declared currency_unresolved", f.key)

        label = f"{f.label} {f.derivation} {f.unit_detail}".lower()
        if f.range_semantics is RangeSemantics.ENVELOPE:
            for phrase in _CONFIDENCE_LABELS:
                if phrase not in label:
                    continue
                if (f"not {phrase}" in label
                        or f"not a {phrase}" in label
                        or f"no {phrase}" in label):
                    continue
                self._err("envelope_as_confidence",
                          f"{f.key}: an envelope range is labelled "
                          f"'{phrase}', which is not what it is", f.key)

    def _check_missing_figure(self, f: Figure) -> None:
        if f.value_min is not None or f.value_max is not None \
                or f.value_text is not None:
            self._err("absence_with_value",
                      f"{f.key}: a {f.status.value} figure carries a value — "
                      f"absence is not zero", f.key)
        if not (f.absence_reason or "").strip():
            self._err("absence_without_reason",
                      f"{f.key}: a {f.status.value} figure requires an "
                      f"absence_reason", f.key)

    # -- Group 4: provenance / evidence -------------------------------------

    def check_evidence(self) -> None:
        self._rule("evidence_integrity")
        for f in _all_figures(self.report):
            for sid in f.source_ids:
                if self.index.resolve(sid) is None:
                    self._err("unresolved_evidence_id",
                              f"{f.key}: evidence id {sid!r} does not resolve "
                              f"in any registry", f.key)
            if f.provenance is not None \
                    and f.provenance.value == "sourced" and not f.source_ids:
                self._err("sourced_without_evidence",
                          f"{f.key}: a sourced figure carries no evidence id",
                          f.key)
            # Derived figures may cite multiple ids; they must not be forced
            # to a single one. Nothing to check beyond resolution above.
            if f.provenance is not None \
                    and f.provenance.value == "derived" and not f.source_ids:
                self.result.add_warning(
                    "derived_without_source_ids",
                    f"{f.key}: a derived figure cites no contributing source "
                    f"id, so its provenance mix is not traceable", f.key)

    # -- Group 5: currency ---------------------------------------------------

    def check_currency(self) -> None:
        self._rule("currency_consistency")
        canonical = (ev.resolve_currency(self.bundle.state).currency
                     if self.bundle is not None else None)
        for f in _all_figures(self.report):
            if f.unit is not Unit.MONEY or f.status is not FigureStatus.KNOWN:
                continue
            unresolved = FLAG_CURRENCY_UNRESOLVED in f.flags
            if unresolved:
                if f.currency:
                    self._err("unresolved_currency_with_symbol",
                              f"{f.key}: marked currency_unresolved but carries "
                              f"a currency {f.currency!r}", f.key)
                continue
            if self.bundle is not None and canonical:
                if f.currency != canonical:
                    self._err("currency_mismatch",
                              f"{f.key}: figure currency {f.currency!r} does "
                              f"not match assessment currency {canonical!r}",
                              f.key)

        if self.bundle is not None and not canonical:
            # Assessment currency unresolved: every money figure must declare
            # it, and the report must say so (a CURRENCY_UNRESOLVED gap).
            money = [f for f in _all_figures(self.report)
                     if f.unit is Unit.MONEY and f.status is FigureStatus.KNOWN]
            if money and not all(FLAG_CURRENCY_UNRESOLVED in f.flags
                                 for f in money):
                self._err("unresolved_currency_not_declared",
                          "assessment currency is unresolved but a money figure "
                          "does not declare currency_unresolved")
            if not any(g.kind is GapKind.CURRENCY_UNRESOLVED
                       for g in _section_gaps(self.report)):
                self._warn("unresolved_currency_not_disclosed",
                           "assessment currency is unresolved and no "
                           "CURRENCY_UNRESOLVED gap is present")

    # -- Group 3: absence integrity -----------------------------------------

    def check_absence(self) -> None:
        self._rule("absence_integrity")
        if self.bundle is None or self.bundle.economics is None:
            return
        figs = _figure_by_key(self.report)
        fy = self.bundle.economics.first_year
        # Payback is absent/undefined when payback_months is None.
        payback = figs.get("benefits.payback")
        if fy is not None and fy.payback_months is None and payback is not None \
                and payback.status is FigureStatus.KNOWN:
            self._err("absent_rendered_known",
                      "benefits.payback is KNOWN but the engine reports "
                      "payback_months=None (undefined)")
        # Scores not computable upstream must not render known.
        if self.bundle.scores is not None:
            for score in (self.bundle.scores.economic,
                          self.bundle.scores.feasibility,
                          self.bundle.scores.risk,
                          self.bundle.scores.composite):
                f = figs.get(f"scores.{score.key}")
                if f is not None and not score.computable \
                        and f.status is FigureStatus.KNOWN:
                    self._err("absent_rendered_known",
                              f"scores.{score.key} is KNOWN but the upstream "
                              f"score is not computable", f.key)

    # -- Group 6: economic representation -----------------------------------

    def check_economic_consistency(self) -> None:
        self._rule("economic_consistency")
        if self.bundle is None or self.bundle.economics is None:
            return
        econ = self.bundle.economics
        figs = _figure_by_key(self.report)

        def _match(key: str, rng) -> None:
            f = figs.get(key)
            if f is None or f.status is not FigureStatus.KNOWN:
                return
            if rng is None:
                self._err("economic_value_mismatch",
                          f"{key}: KNOWN but the upstream value is absent")
                return
            if f.value_min != rng.min or f.value_max != rng.max:
                self._err("economic_value_mismatch",
                          f"{key}: value does not equal the supplied "
                          f"EconomicResult ({f.value_min}-{f.value_max} vs "
                          f"{rng.min}-{rng.max})", key)

        _match("current_cost.total", econ.current_annual_total)
        _match("ai_operating.total", econ.ai_operating_total)
        _match("impl.total", econ.implementation_total)
        if econ.first_year is not None:
            _match("benefits.annual_savings",
                   econ.first_year.annual_cost_savings)
            _match("benefits.first_year_net",
                   econ.first_year.first_year_net_benefit)
            _match("benefits.first_year_ai_cost",
                   econ.first_year.first_year_ai_cost)
            _match("summary.current_cost", econ.current_annual_total)
            _match("summary.ai_operating_cost", econ.ai_operating_total)
            _match("summary.annual_savings",
                   econ.first_year.annual_cost_savings)

    # -- Group 7: labor realization -----------------------------------------

    def check_labor_realization(self) -> None:
        self._rule("labor_realization")
        if self.bundle is None:
            return
        policy = self.bundle.labor_realization
        manifest = self.report.manifest
        if manifest is not None:
            if policy is not None and manifest.labor_realization != policy.value:
                self._err("realization_mismatch",
                          f"manifest labor_realization "
                          f"{manifest.labor_realization!r} does not match the "
                          f"bundle policy {policy.value!r}")
            if policy is None and manifest.labor_realization is not None:
                self._err("realization_invented",
                          "the bundle has no labor realization policy but the "
                          "report silently chose one")
        if policy is not None and policy.value == "capacity_retained":
            # Freed capacity must be presented as capacity, never as savings.
            benefits = self.report.section("expected_benefits")
            if benefits is not None:
                text = " ".join(s.text.lower() for s in benefits.statements)
                if "freed-capacity value above is capacity, not savings" not in text:
                    self._warn("realization_capacity_not_stated",
                               "capacity_retained policy but the freed-capacity "
                               "disclosure is absent", "expected_benefits")

    # -- Group 8: scoring ----------------------------------------------------

    def check_scores(self) -> None:
        self._rule("score_integrity")
        exec_sum = self.report.section("executive_summary")
        if exec_sum is not None:
            if any("composite" in (f.key or "") for f in exec_sum.figures):
                self._err("composite_in_summary",
                          "the Composite Readiness Score appears in the "
                          "Executive Summary", "executive_summary")
            # Refused reports may legitimately omit the confidence framing when
            # the analysis did not proceed to scored outputs.
            if self.report.mode is not ReportMode.REFUSED and not any(
                    "confidence describes" in s.text.lower()
                    for s in exec_sum.statements):
                self._err("confidence_presented_as_quality",
                          "the Executive Summary does not state that "
                          "confidence is not quality", "executive_summary")
        if self.bundle is not None and self.bundle.scores is not None:
            figs = _figure_by_key(self.report)
            for score in (self.bundle.scores.economic,
                          self.bundle.scores.feasibility,
                          self.bundle.scores.risk,
                          self.bundle.scores.composite):
                f = figs.get(f"scores.{score.key}")
                if f is not None and score.computable \
                        and f.status is FigureStatus.KNOWN \
                        and f.value_min != score.value:
                    self._err("score_changed",
                              f"scores.{score.key} value {f.value_min} does not "
                              f"match the supplied ScoreBundle "
                              f"{score.value}", f.key)

    # -- Group 9: decision drivers ------------------------------------------

    def check_drivers(self) -> None:
        self._rule("driver_ordering")
        if self.bundle is None or self.bundle.drivers is None:
            return
        section = self.report.section("decision_drivers")
        if section is None:
            return
        upstream = self.bundle.drivers.drivers
        keys = [d.key for d in section.drivers]
        upstream_keys = [d.key for d in upstream]
        if keys != upstream_keys:
            self._err("driver_order_changed",
                      f"driver order in report {keys} does not match upstream "
                      f"{upstream_keys} — the report must not re-rank",
                      "decision_drivers")
        by_key = {d.key: d for d in upstream}
        for entry in section.drivers:
            up = by_key.get(entry.key)
            if up is None:
                continue
            expected = DriverClass.for_driver(up.driver_type.value, up.impact)
            if entry.presentation_class is not expected:
                self._err("driver_misclassified",
                          f"{entry.key}: presentation class "
                          f"{entry.presentation_class} does not match upstream "
                          f"({expected})", "decision_drivers")
            if up.impact == 0 and up.driver_type.value != "data_coverage" \
                    and entry.presentation_class is DriverClass.ECONOMICALLY_ACTIVE:
                self._err("factual_as_economic",
                          f"{entry.key}: a zero-impact factual input is "
                          f"presented as an economic driver", "decision_drivers")
            if up.driver_type.value == "data_coverage" \
                    and entry.presentation_class is DriverClass.ECONOMICALLY_ACTIVE:
                self._err("coverage_as_economic",
                          f"{entry.key}: a data-coverage item is presented as "
                          f"an economic driver", "decision_drivers")

    # -- Group 10 & 11: alternatives + sensitivity ---------------------------

    def check_alternatives(self) -> None:
        self._rule("alternatives_informational")
        section = self.report.section("alternative_solutions")
        if section is None:
            return
        for f in section.figures:
            if f.unit is Unit.MONEY:
                self._err("alternative_economics",
                          f"{f.key}: the report models economics for an "
                          f"alternative", "alternative_solutions")
        text = " ".join(s.text for s in section.statements)
        for phrase in directive_hits(text):
            self._err("alternative_recommendation",
                      f"alternatives section contains directive language: "
                      f"{phrase}", "alternative_solutions")
        if self.bundle is not None:
            alt = self.bundle.alternatives
            n_up = len(alt.alternatives)
            n_tab = 0
            for t in section.tables:
                if t.key == "alternatives.comparison":
                    n_tab = len(t.rows)
            if n_up and n_tab and n_tab != n_up:
                self._err("alternative_count_mismatch",
                          f"alternatives table shows {n_tab} rows but the "
                          f"supplied AlternativesResult has {n_up}",
                          "alternative_solutions")

    def check_sensitivity(self) -> None:
        self._rule("sensitivity_bounds")
        section = self.report.section("sensitivity_analysis")
        if section is None:
            return
        text = " ".join(s.text for s in section.statements).lower()
        for phrase in ("decision changes", "crosses", "threshold for",
                       "becomes viable", "would need to be"):
            if phrase in text:
                self._err("sensitivity_as_threshold",
                          f"sensitivity section frames a decision threshold: "
                          f"{phrase!r}", "sensitivity_analysis")
        if self.bundle is not None and self.bundle.sensitivity is not None:
            sens = self.bundle.sensitivity
            all_rows = [c for t in section.tables for r in t.rows for c in r]
            texts = [str(c.text or "").lower() for c in all_rows]
            if sens.skipped and not any("skipped" in t for t in texts):
                self._err("sensitivity_skipped_hidden",
                          "sensitivity skipped rows were dropped",
                          "sensitivity_analysis")
            failed = [i for i in sens.impacts if i.failed]
            if failed and not any("could not be evaluated" in t or "failed" in t
                                  for t in texts):
                self._err("sensitivity_failed_hidden",
                          "sensitivity failed rows were converted or dropped",
                          "sensitivity_analysis")

    # -- Group 12: refused report safety -------------------------------------

    def check_refused(self) -> None:
        self._rule("refused_safety")
        if self.report.mode is not ReportMode.REFUSED:
            return
        if self.report.section("proposed_ai_solution") is not None:
            self._err("refused_has_solution",
                      "a refused report must not present a proposed solution")
        if self.report.section("expected_benefits") is not None:
            self._err("refused_has_benefits",
                      "a refused report must not present expected benefits")
        forbidden_exact = {
            "solution.pattern", "solution.implementation",
            "solution.overall_automation", "summary.annual_savings",
            "summary.ai_operating_cost", "summary.automation",
            "benefits.payback", "benefits.annual_savings",
            "benefits.first_year_net", "benefits.first_year_ai_cost",
        }
        forbidden_prefixes = (
            "solution.",          # architecture/automation/performance claims
            "ai_operating.",      # downstream AI operating economics
            "impl.",              # implementation economics
            "benefits.",          # savings/net/payback economics
            "scores.",            # downstream scores
        )
        for f in _all_figures(self.report):
            if f.status is not FigureStatus.KNOWN:
                continue
            key = f.key or ""
            if key in forbidden_exact or any(key.startswith(p)
                                             for p in forbidden_prefixes):
                self._err("refused_fabricated_value",
                          f"{key}: a refused report fabricates a downstream "
                          f"conclusion", key)
        # Refused reports may still render available assessment facts in money
        # units (for example user-provided loaded-cost inputs in §2). What is
        # forbidden is fabricated DOWNSTREAM economics (AI operating / benefits /
        # implementation / score claims) and any solution claim.
        # Refused report must say what should be validated next.
        vnext = self.report.section("what_to_validate_next")
        if vnext is None:
            self._err("refused_no_validate_next",
                      "a refused report must point to what to validate next")

    # -- Group 13 & 14: language + number boundary ---------------------------

    def check_language_and_numbers(self) -> None:
        self._rule("language_guard")
        self._rule("number_boundary")
        for statement in _all_statements(self.report):
            hits = directive_hits(statement.text)
            for phrase in hits:
                self._err("directive_language",
                          f"statement contains directive language: {phrase!r}",
                          section=_statement_section(self.report, statement))
            # Authored statements carry no figures of their own, so a number
            # that reads as an economic/measure claim there is an orphan. This
            # is NOT a blanket digit ban: version numbers, dates and
            # identifiers are legitimate, and verbatim statements trace their
            # numbers to an upstream source (verbatim_from).
            if statement.verbatim_from is None \
                    and _ECON_NUMBER.search(statement.text):
                self.result.add_warning(
                    "orphan_numeric_claim",
                    f"an authored statement contains a number with no figure "
                    f"source: {statement.text!r}",
                    section=_statement_section(self.report, statement))

    # -- Group 16: executive summary -----------------------------------------

    def check_executive(self) -> None:
        self._rule("executive_safety")
        exec_sum = self.report.section("executive_summary")
        if exec_sum is None:
            return
        if self.bundle is not None and self.bundle.economics is not None \
                and self.report.mode is not ReportMode.REFUSED:
            econ = self.bundle.economics
            text = " ".join(s.text for s in exec_sum.statements)
            # Preserve the payback statement.
            if econ.first_year is not None and econ.first_year.payback_statement \
                    and econ.first_year.payback_statement not in text:
                self._err("exec_payback_lost",
                          "the Executive Summary does not preserve the engine's "
                          "payback statement", "executive_summary")
            # Preserve the realization policy.
            if econ.realization_statement and econ.realization_statement not in text:
                self._err("exec_realization_lost",
                          "the Executive Summary does not preserve the "
                          "realization policy statement", "executive_summary")
        # Sanity flags must not disappear.
        if self.bundle is not None and self.bundle.scores is not None:
            econ_score = self.bundle.scores.economic
            if econ_score.flags:
                found_flags = set()
                for f in _all_figures(self.report):
                    found_flags.update(f.flags)
                missing = [fl for fl in econ_score.flags if fl not in found_flags]
                if missing:
                    self._err("sanity_flag_dropped",
                              f"economic sanity flags vanished from the report: "
                              f"{missing}")

    # -- Group 17: gap preservation -----------------------------------------

    def check_gaps(self) -> None:
        self._rule("gap_preservation")
        if self.bundle is None:
            return
        all_gaps = _section_gaps(self.report)
        kinds = {g.kind for g in all_gaps}
        if self.bundle.economics is not None:
            if self.bundle.economics.absent_components \
                    and GapKind.ABSENT_COST not in kinds:
                self._err("gap_absent_cost_dropped",
                          "absent cost components exist upstream but no "
                          "ABSENT_COST gap was preserved")
        if self.bundle.solution.compliance_gap:
            if not self.report.refusal_reason \
                    and GapKind.REGISTRY_GAP not in kinds:
                self._err("gap_compliance_dropped",
                          "an upstream compliance gap was not preserved")
        if self.bundle.economics is not None \
                and self.bundle.economics.labor_consistency is not None \
                and self.bundle.economics.labor_consistency.status.value \
                == "divergent":
            if not any(g.kind is GapKind.UNRESOLVED_FIELD and "Labor" in g.label
                       for g in all_gaps):
                self._err("gap_labor_dropped",
                          "divergent labor formulations were not preserved as "
                          "a gap")
        if self.bundle.solution.reference_comparison is not None:
            if self.bundle.solution.reference_comparison.unevaluated_conditions \
                    and GapKind.UNEVALUATED_CONDITION not in kinds:
                self._err("gap_unevaluated_dropped",
                          "unevaluated reference conditions were dropped")

    # -- entry point ----------------------------------------------------------

    def run(self) -> ValidationResult:
        self.check_structure()
        self.check_figures()
        self.check_evidence()
        self.check_currency()
        self.check_absence()
        self.check_economic_consistency()
        self.check_labor_realization()
        self.check_scores()
        self.check_drivers()
        self.check_alternatives()
        self.check_sensitivity()
        self.check_refused()
        self.check_language_and_numbers()
        self.check_executive()
        self.check_gaps()
        return self.result


def _statement_section(report: Report, statement) -> str:
    for section in report.sections:
        if statement in section.statements:
            return section.key
    return ""


def validate(report: Report, bundle: Optional[ReportInput] = None) -> ValidationResult:
    """Validate an assembled Report fail-closed.

    `bundle` (the ReportInput the report was assembled from) enables the
    analytical cross-checks (economics, scores, drivers, alternatives,
    sensitivity, gaps, determinism). Without it only the self-contained
    structural/figure/language rules run.
    """
    result = ReportValidator(report, bundle).run()

    # Group 1 #9: deterministic assembly must not change report content.
    if bundle is not None:
        reassembled = assemble.assemble(bundle)
        if not _same_content(report, reassembled):
            result.add_warning(
                "determinism_divergence",
                "report content differs from a fresh deterministic assembly of "
                "the same bundle")
        result.rule_checked("determinism")
    return result


def _same_content(a: Report, b: Report) -> bool:
    da = a.model_dump()
    db = b.model_dump()
    da.get("manifest", {}).pop("generated_at", None)
    db.get("manifest", {}).pop("generated_at", None)
    return da == db
