from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from graphcheck.contracts.check import CompetencyCheck, ConformanceCheck, DriftCheck
from graphcheck.contracts.results import Estimate, Evidence, EvidenceElement, Pattern
from graphcheck.engine.baseline import BaselineValue
from graphcheck.engine.compiler import CompiledCheck
from graphcheck.engine.sampling import wilson_estimate
from graphcheck.errors import GraphCheckError


@dataclass(frozen=True)
class Evaluation:
    passed: bool
    measured: dict[str, object]
    evidence: Evidence | None = None
    estimate: Estimate | Literal[False] = False

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("evaluation.passed must be boolean")


class VerdictEvaluator:
    """Pure verdict evaluation over already-executed query results."""

    def evaluate(
        self,
        compiled: CompiledCheck,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
        baseline: BaselineValue | None = None,
    ) -> Evaluation:
        pattern = compiled.check.pattern
        if pattern is Pattern.CONFORMANCE:
            return self._conformance(compiled, rows)
        if pattern in (Pattern.COMPETENCY_SHAPE, Pattern.COMPETENCY_REGRESSION):
            return self._competency(compiled, rows, columns)
        if pattern is Pattern.DRIFT:
            return self._drift(compiled, rows, baseline)
        raise GraphCheckError(
            "engine.evaluation_unsupported",
            f"No evaluator exists for pattern {pattern!s}.",
            "Use a pattern supported by this GraphCheck engine release.",
        )

    def _conformance(
        self, compiled: CompiledCheck, rows: Sequence[Mapping[str, Any]]
    ) -> Evaluation:
        spec = compiled.check.spec
        if not isinstance(spec, ConformanceCheck):
            raise _bad_result(compiled, "the loaded payload is not conformance")
        row = _single_summary_row(compiled, rows)
        _require_schema(compiled, row)

        if spec.check in {"pii_name_match", "pii_value_match"}:
            return self._pii(compiled, row, spec.check)

        if spec.check == "completeness":
            coverage = _number(row, "coverage", compiled)
            population = _integer(row, "population", compiled)
            conforming = _integer(row, "conforming_count", compiled)
            violations = _integer(row, "violation_count", compiled)
            threshold = float(spec.with_.get("threshold", 1.0))
            expected_coverage = 1.0 if population == 0 else conforming / population
            if (
                conforming + violations != population
                or not 0.0 <= coverage <= 1.0
                or not math.isclose(coverage, expected_coverage, rel_tol=1e-12, abs_tol=1e-12)
            ):
                raise _bad_result(
                    compiled,
                    "population, conforming_count, violation_count, and coverage disagree",
                )
            measured: dict[str, object] = {
                "coverage": coverage,
                "population": population,
                "conforming": conforming,
                "violations": violations,
            }
            passed = coverage >= threshold
        else:
            violations = _integer(row, "violation_count", compiled)
            population = _integer(row, "population", compiled, default=violations)
            measured = {"violations": violations, "population": population}
            for key, value in row.items():
                if key not in _SUMMARY_INTERNAL_FIELDS and _is_measurement(value):
                    measured.setdefault(key, value)
            passed = violations == 0

        estimate: Estimate | Literal[False] = False
        if compiled.sampled:
            sample_size = _integer(row, "sample_size", compiled)
            population = _integer(row, "population", compiled)
            if compiled.sample_population is not None and population != compiled.sample_population:
                raise _bad_result(
                    compiled,
                    "sample query population disagrees with its deterministic preflight",
                )
            if sample_size > population:
                raise _bad_result(compiled, "sample_size exceeds population")
            if violations > sample_size:
                raise _bad_result(compiled, "violation_count exceeds sample_size")
            if sample_size < population:
                estimate = wilson_estimate(violations, sample_size, population)

        if passed:
            return Evaluation(True, measured, estimate=estimate)
        message = f"{compiled.name} found {violations} violating graph element(s)."
        evidence = _build_evidence(
            message,
            compiled,
            explicit=row.get("evidence", []),
            total_count=violations,
        )
        return Evaluation(False, measured, evidence=evidence, estimate=estimate)

    def _pii(
        self,
        compiled: CompiledCheck,
        row: Mapping[str, Any],
        check_name: str,
    ) -> Evaluation:
        population = _integer(row, "population", compiled)
        sample_size = _integer(row, "sample_size", compiled)
        if sample_size > population:
            raise _bad_result(compiled, "sample_size exceeds population")
        if population > 0 and sample_size == 0:
            raise _bad_result(compiled, "non-empty PII population has an empty sample")
        if compiled.sample_population is not None and population != compiled.sample_population:
            raise _bad_result(
                compiled,
                "sample query population disagrees with its deterministic preflight",
            )
        candidates = row.get("candidates")
        if not isinstance(candidates, list):
            raise _bad_result(compiled, "PII summary candidates is not a list")
        if len(candidates) != sample_size:
            raise _bad_result(compiled, "PII candidate count disagrees with sample_size")

        confidence = compiled.expected.get("confidence")
        patterns = compiled.expected.get("patterns")
        notice = compiled.expected.get("completeness_notice")
        if confidence not in {"name-match", "value-match"}:
            raise _bad_result(compiled, "PII confidence is invalid")
        if not isinstance(patterns, list) or not patterns:
            raise _bad_result(compiled, "PII patterns are missing")
        if not isinstance(notice, str) or not notice.strip():
            raise _bad_result(compiled, "PII completeness notice is missing")

        grouped: Counter[tuple[str, tuple[str, ...], str]] = Counter()
        matched_pointers: list[EvidenceElement] = []
        matched_candidates = 0
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise _bad_result(compiled, "PII candidate is not a mapping")
            property_name = candidate.get("property")
            pointer = _pointer_from_value(candidate.get("evidence"))
            if not isinstance(property_name, str) or not property_name:
                raise _bad_result(compiled, "PII candidate property is invalid")
            if pointer is None or pointer.kind != "node":
                raise _bad_result(compiled, "PII candidate omitted its node evidence pointer")

            matched_pattern_ids = _pii_matches(
                check_name,
                property_name,
                candidate.get("value"),
                patterns,
                compiled,
            )
            if not matched_pattern_ids:
                continue
            matched_candidates += 1
            matched_pointers.append(pointer)
            labels = tuple(sorted(pointer.labels or []))
            for pattern_id in matched_pattern_ids:
                grouped[(pattern_id, labels, property_name)] += 1

        findings = []
        for (pattern_id, labels, property_name), observed in sorted(grouped.items()):
            exposure_count = (
                observed
                if sample_size == population
                else round(population * observed / sample_size)
            )
            findings.append(
                {
                    "pattern": pattern_id,
                    "location": {"labels": list(labels), "property": property_name},
                    "exposure_count": exposure_count,
                    "confidence": confidence,
                }
            )

        measured: dict[str, object] = {
            "population": population,
            "sample_size": sample_size,
            "matches": matched_candidates,
            "findings": findings,
            "confidence": confidence,
            "completeness_notice": notice,
        }
        estimate: Estimate | Literal[False] = False
        if sample_size < population:
            estimate = wilson_estimate(matched_candidates, sample_size, population)
        if matched_candidates == 0:
            return Evaluation(True, measured, estimate=estimate)

        evidence = _build_evidence(
            f"{compiled.name} found {matched_candidates} sampled PII exposure(s).",
            compiled,
            explicit=matched_pointers,
            total_count=matched_candidates,
        )
        return Evaluation(False, measured, evidence=evidence, estimate=estimate)

    def _competency(
        self,
        compiled: CompiledCheck,
        rows: Sequence[Mapping[str, Any]],
        columns: Sequence[str] | None,
    ) -> Evaluation:
        spec = compiled.check.spec
        if not isinstance(spec, CompetencyCheck):
            raise _bad_result(compiled, "the loaded payload is not competency")
        expected = spec.expect
        actual_columns = list(columns) if columns is not None else _columns_from_rows(rows)
        row_count = len(rows)
        frozen_rows = [_freeze(row) for row in rows]
        frequencies = Counter(frozen_rows)
        duplicate_rows = [
            row for row, frozen in zip(rows, frozen_rows, strict=True) if frequencies[frozen] > 1
        ]
        is_unique = not duplicate_rows

        measured: dict[str, object] = {
            "rows": row_count,
            "columns": actual_columns,
            "unique": is_unique,
            "empty": row_count == 0,
        }
        failures: list[str] = []
        evidence_rows: list[Mapping[str, Any]] = []

        bounds = expected.rows
        if bounds is not None:
            if bounds.min is not None and row_count < bounds.min:
                failures.append(f"rows {row_count} is below min {bounds.min}")
            if bounds.max is not None and row_count > bounds.max:
                failures.append(f"rows {row_count} exceeds max {bounds.max}")
                evidence_rows.extend(rows[bounds.max :])
            if bounds.exactly is not None and row_count != bounds.exactly:
                failures.append(f"rows {row_count} does not equal {bounds.exactly}")
                evidence_rows.extend(rows)

        if expected.columns is not None and actual_columns != expected.columns:
            failures.append(
                f"columns {actual_columns!r} do not equal expected {expected.columns!r}"
            )
            evidence_rows.extend(rows)
        if expected.unique is not None and is_unique is not expected.unique:
            if expected.unique:
                failures.append("rows are not unique")
                evidence_rows.extend(duplicate_rows)
            else:
                failures.append("rows are unique")
                evidence_rows.extend(rows)
        if expected.empty is not None and (row_count == 0) is not expected.empty:
            failures.append(f"empty is not {expected.empty}")
            evidence_rows.extend(rows)

        actual_values = _regression_values(rows, actual_columns)
        if expected.contains is not None:
            contains_ok = all(_contains(actual_values, value) for value in expected.contains)
            measured["contains"] = contains_ok
            if not contains_ok:
                failures.append("result does not contain every pinned value")
                evidence_rows.extend(rows)
        if expected.equals is not None:
            # Neo4j does not guarantee row order without ORDER BY. `equals` therefore compares
            # the complete result as a duplicate-preserving bag, avoiding graph-stable verdicts
            # that change only because the server returned rows in another order.
            equals_ok = _bag(actual_values) == _bag(expected.equals)
            measured["equals"] = equals_ok
            if not equals_ok:
                failures.append("result does not equal the pinned values")
                evidence_rows.extend(rows)

        if not failures:
            return Evaluation(True, measured)
        message = f"{compiled.name}: " + "; ".join(dict.fromkeys(failures))
        evidence = _build_evidence(
            message,
            compiled,
            rows=evidence_rows or rows,
            params=compiled.params,
            total_count=max(1, len(evidence_rows or rows)),
        )
        return Evaluation(False, measured, evidence=evidence)

    def _drift(
        self,
        compiled: CompiledCheck,
        rows: Sequence[Mapping[str, Any]],
        baseline: BaselineValue | None,
    ) -> Evaluation:
        spec = compiled.check.spec
        if not isinstance(spec, DriftCheck):
            raise _bad_result(compiled, "the loaded payload is not drift")
        if baseline is None:
            raise GraphCheckError(
                "engine.baseline_missing",
                f"Drift check {compiled.check.id!r} has no resolved baseline.",
                "Pin the requested baseline and run the suite again.",
            )
        row = _single_summary_row(compiled, rows)
        _require_schema(compiled, row)
        current = _number(row, "current", compiled)
        previous = baseline.value
        if spec.metric == "property_coverage" and (
            not 0.0 <= current <= 100.0 or not 0.0 <= previous <= 100.0
        ):
            raise _bad_result(compiled, "property_coverage values must use percent units [0, 100]")
        delta = current - previous
        percent = None if previous == 0 else 100.0 * delta / abs(previous)
        measured: dict[str, object] = {
            "current": current,
            "baseline": previous,
            "delta": delta,
            "change_pct": percent,
        }

        failures = _drift_failures(current, previous, spec.tolerance)
        if not failures:
            return Evaluation(True, measured)
        explicit = [*row.get("evidence", []), *baseline.evidence]
        total_count = max(1, _coerce_nonnegative_int(row.get("population", 0)))
        if spec.metric in {"node_count", "relationship_count"}:
            # Counts describe a measurement scope, not a set of currently offending elements.
            # Keep any baseline/current pointers as supplemental context, but put the honest scope
            # first so a small evidence cap can never replace it with an arbitrary survivor.
            explicit.insert(0, _aggregate_count_drift_pointer(spec))
            total_count = 1
        message = f"{compiled.name}: " + "; ".join(failures)
        evidence = _build_evidence(
            message,
            compiled,
            explicit=explicit,
            total_count=total_count,
            allow_aggregate=spec.metric in {"node_count", "relationship_count"},
        )
        return Evaluation(False, measured, evidence=evidence)


def evaluate_check(
    compiled: CompiledCheck,
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str] | None = None,
    baseline: BaselineValue | None = None,
) -> Evaluation:
    return VerdictEvaluator().evaluate(compiled, rows, columns=columns, baseline=baseline)


_SUMMARY_INTERNAL_FIELDS = {
    "schema_ok",
    "missing_labels",
    "missing_relationship_types",
    "missing_properties",
    "evidence",
}


def _single_summary_row(
    compiled: CompiledCheck, rows: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    if len(rows) != 1:
        raise _bad_result(compiled, f"compiled query returned {len(rows)} summary rows, expected 1")
    return rows[0]


def _require_schema(compiled: CompiledCheck, row: Mapping[str, Any]) -> None:
    if "schema_ok" not in row:
        raise _bad_result(compiled, "compiled summary omitted schema_ok")
    if row["schema_ok"] is True:
        return
    if row["schema_ok"] is not False:
        raise _bad_result(compiled, "compiled summary schema_ok is not boolean")
    labels = list(row.get("missing_labels") or [])
    rel_types = list(row.get("missing_relationship_types") or [])
    properties = list(row.get("missing_properties") or [])
    missing = [*(f"label {item!r}" for item in labels)]
    missing.extend(f"relationship type {item!r}" for item in rel_types)
    missing.extend(f"property {item!r}" for item in properties)
    detail = ", ".join(missing) or "an unknown graph schema token"
    raise GraphCheckError(
        "engine.schema_reference_missing",
        f"Check {compiled.check.id!r} references {detail}.",
        "Correct the label/type in the suite or run it against the intended graph.",
    )


def _integer(
    row: Mapping[str, Any],
    key: str,
    compiled: CompiledCheck,
    *,
    default: int | None = None,
) -> int:
    if key not in row and default is not None:
        return default
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad_result(compiled, f"field {key!r} is not numeric")
    integer = int(value)
    if integer != value or integer < 0:
        raise _bad_result(compiled, f"field {key!r} is not a non-negative integer")
    return integer


def _number(row: Mapping[str, Any], key: str, compiled: CompiledCheck) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad_result(compiled, f"field {key!r} is not numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise _bad_result(compiled, f"field {key!r} is not finite")
    return numeric


def _bad_result(compiled: CompiledCheck, detail: str) -> GraphCheckError:
    return GraphCheckError(
        "engine.invalid_query_result",
        f"Check {compiled.check.id!r} cannot be evaluated: {detail}.",
        "Fix the compiler/query so it returns the documented C1 result shape.",
    )


def _pii_matches(
    check_name: str,
    property_name: str,
    value: object,
    patterns: list[object],
    compiled: CompiledCheck,
) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, Mapping):
            raise _bad_result(compiled, "PII pattern is not a mapping")
        pattern_id = pattern.get("id")
        if not isinstance(pattern_id, str) or not pattern_id:
            raise _bad_result(compiled, "PII pattern id is invalid")
        if check_name == "pii_name_match":
            keys = pattern.get("keys")
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                raise _bad_result(compiled, f"PII name pattern {pattern_id!r} has invalid keys")
            if property_name.casefold() in {key.casefold() for key in keys}:
                matches.append(pattern_id)
            continue

        if check_name != "pii_value_match":
            raise _bad_result(compiled, f"unsupported PII check {check_name!r}")
        if not isinstance(value, str):
            raise _bad_result(compiled, "PII value candidate is not a string")
        regex = pattern.get("regex")
        checksum = pattern.get("checksum")
        if not isinstance(regex, str):
            raise _bad_result(compiled, f"PII value pattern {pattern_id!r} has no regex")
        try:
            regex_matches = re.fullmatch(regex, value) is not None
        except re.error as exc:  # metadata validation should make this unreachable
            raise _bad_result(
                compiled,
                f"PII value pattern {pattern_id!r} has invalid regex: {exc}",
            ) from exc
        if not regex_matches:
            continue
        if (
            checksum is None
            or (checksum == "luhn" and _luhn_valid(value))
            or (checksum == "verhoeff" and _verhoeff_valid(value))
        ):
            matches.append(pattern_id)
        elif checksum not in {"luhn", "verhoeff"}:
            raise _bad_result(
                compiled,
                f"PII value pattern {pattern_id!r} has unsupported checksum {checksum!r}",
            )
    return matches


def _luhn_valid(value: str) -> bool:
    if any(not (character.isdigit() or character in {" ", "-"}) for character in value):
        return False
    digits = [int(character) for character in value if character.isdigit()]
    if len(digits) < 2:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def _verhoeff_valid(value: str) -> bool:
    if not value or not value.isdigit():
        return False
    checksum = 0
    for index, character in enumerate(reversed(value)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[index % 8][int(character)]]
    return checksum == 0


def _columns_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return list(rows[0]) if rows else []


def _freeze(value: object) -> object:
    pointer = _pointer_from_value(value)
    if pointer is not None:
        return (pointer.kind, pointer.id)
    if isinstance(value, Mapping):
        # Cypher rows/maps are value objects: insertion order is not part of row equality.
        # Sorting also keeps uniqueness deterministic for lightweight connector doubles.
        return tuple(
            sorted(
                ((str(key), _freeze(item)) for key, item in value.items()),
                key=lambda item: item[0],
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _regression_values(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> list[object]:
    if len(columns) == 1:
        key = columns[0]
        return [row.get(key) for row in rows]
    return [{key: row.get(key) for key in columns} for row in rows]


def _contains(values: Sequence[object], expected: object) -> bool:
    return any(value == expected for value in values)


def _bag(values: Sequence[object]) -> Counter:
    return Counter(_freeze(value) for value in values)


def _build_evidence(
    message: str,
    compiled: CompiledCheck,
    *,
    explicit: Iterable[object] = (),
    rows: Iterable[Mapping[str, Any]] = (),
    params: Mapping[str, object] | None = None,
    total_count: int,
    allow_aggregate: bool = False,
) -> Evidence:
    pointers: list[EvidenceElement] = []
    for value in explicit:
        pointer = _pointer_from_value(value)
        if pointer is not None:
            pointers.append(pointer)
    for row in rows:
        pointers.extend(_pointers_from_row(row))
    if params:
        pointers.extend(_pointers_from_ids(params))
    if not allow_aggregate:
        pointers = [pointer for pointer in pointers if pointer.kind != "aggregate"]
    unique: list[EvidenceElement] = []
    seen: set[tuple[str, str]] = set()
    unique_count = 0
    for pointer in pointers:
        identity = (pointer.kind, pointer.id)
        if identity in seen:
            continue
        seen.add(identity)
        unique_count += 1
        if len(unique) < compiled.evidence_cap:
            unique.append(pointer)
    if not unique:
        raise GraphCheckError(
            "engine.evidence_missing",
            f"Check {compiled.check.id!r} failed but returned no evidence pointer.",
            "Project graph entities or `*_id` columns so every finding identifies its source.",
        )
    total = max(total_count, unique_count)
    return Evidence(
        message=message,
        elements=unique,
        truncated=total > len(unique),
        cap=compiled.evidence_cap,
        total_count=total,
    )


def _pointers_from_row(row: Mapping[str, Any]) -> list[EvidenceElement]:
    return _nested_pointers(row, seen=set())


def _nested_pointers(value: object, *, seen: set[int]) -> list[EvidenceElement]:
    pointer = _pointer_from_value(value)
    if pointer is not None:
        return [pointer]

    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return []
    identity = id(value)
    if identity in seen:
        return []
    seen.add(identity)

    pointers: list[EvidenceElement] = []
    if isinstance(value, Mapping):
        pointers.extend(_pointers_from_ids(value))
        for item in value.values():
            pointers.extend(_nested_pointers(item, seen=seen))
        return pointers
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            pointers.extend(_nested_pointers(item, seen=seen))
        return pointers

    # neo4j.graph.Path exposes both collections. Duck typing keeps the evaluator pure and also
    # supports connector-compatible path doubles without importing a concrete driver class.
    for attribute in ("nodes", "relationships"):
        items = getattr(value, attribute, None)
        if isinstance(items, (list, tuple)):
            for item in items:
                pointers.extend(_nested_pointers(item, seen=seen))
    return pointers


def _pointers_from_ids(values: Mapping[str, object]) -> list[EvidenceElement]:
    pointers: list[EvidenceElement] = []
    pointer_fields: dict[str, Literal["node", "rel"]] = {
        "node_element_id": "node",
        "rel_element_id": "rel",
        "relationship_element_id": "rel",
    }
    for key, value in values.items():
        lowered = str(key).lower()
        # Domain identifiers such as customer_id/account_id are property values, not Neo4j
        # evidence pointers. Only explicit graph-identity aliases are accepted here; projecting
        # the raw Node/Relationship or a {kind, id} map remains the unambiguous option.
        if lowered not in pointer_fields:
            continue
        if value is None or isinstance(value, (Mapping, list, tuple, set)):
            continue
        pointers.append(EvidenceElement(kind=pointer_fields[lowered], id=str(value)))
    return pointers


def _pointer_from_value(value: object) -> EvidenceElement | None:
    if isinstance(value, EvidenceElement):
        return value
    if isinstance(value, Mapping) and value.get("kind") in {"node", "rel"}:
        identifier = value.get("id")
        if identifier is None:
            return None
        kind = value["kind"]
        return EvidenceElement(
            kind=kind,
            id=str(identifier),
            labels=list(value.get("labels") or []) if kind == "node" else None,
            type=(str(value["type"]) if kind == "rel" and value.get("type") is not None else None),
        )

    identifier = getattr(value, "element_id", None)
    if identifier is None:
        identifier = getattr(value, "id", None)
    if identifier is None:
        return None
    labels = getattr(value, "labels", None)
    if labels is not None:
        return EvidenceElement(kind="node", id=str(identifier), labels=sorted(map(str, labels)))
    rel_type = getattr(value, "type", None)
    if rel_type is not None:
        return EvidenceElement(kind="rel", id=str(identifier), type=str(rel_type))
    return None


def _aggregate_count_drift_pointer(spec: DriftCheck) -> EvidenceElement:
    scope = ",".join(f"{key}={spec.target[key]}" for key in sorted(spec.target)) or "graph"
    label = spec.target.get("label")
    rel_type = spec.target.get("type")
    return EvidenceElement(
        kind="aggregate",
        id=f"{spec.metric}:{scope}",
        labels=[str(label)] if label is not None else None,
        type=str(rel_type) if rel_type is not None else None,
    )


def _drift_failures(current: float, baseline: float, tolerance: Mapping[str, object]) -> list[str]:
    supported = {
        "max_drop_pct",
        "max_increase_pct",
        "max_change_pct",
        "max_delta",
        "absolute",
        "min",
        "max",
    }
    unknown = set(tolerance) - supported
    if unknown:
        rendered = ", ".join(sorted(unknown))
        raise GraphCheckError(
            "engine.tolerance_unsupported",
            f"Unsupported drift tolerance key(s): {rendered}.",
            "Use a tolerance supported by this metric/compiler version.",
        )
    limits = {key: _tolerance_number(key, value) for key, value in tolerance.items()}
    delta = current - baseline
    failures: list[str] = []
    if "max_drop_pct" in limits and delta < 0:
        drop = math.inf if baseline == 0 else 100.0 * -delta / abs(baseline)
        if drop > limits["max_drop_pct"]:
            failures.append(f"drop {drop:.6g}% exceeds {limits['max_drop_pct']:.6g}%")
    if "max_increase_pct" in limits and delta > 0:
        increase = math.inf if baseline == 0 else 100.0 * delta / abs(baseline)
        if increase > limits["max_increase_pct"]:
            failures.append(f"increase {increase:.6g}% exceeds {limits['max_increase_pct']:.6g}%")
    if "max_change_pct" in limits and delta != 0:
        change = math.inf if baseline == 0 else 100.0 * abs(delta) / abs(baseline)
        if change > limits["max_change_pct"]:
            failures.append(f"change {change:.6g}% exceeds {limits['max_change_pct']:.6g}%")
    absolute_limit = limits.get("max_delta", limits.get("absolute"))
    if absolute_limit is not None and abs(delta) > absolute_limit:
        failures.append(f"absolute delta {abs(delta):.6g} exceeds {absolute_limit:.6g}")
    if "min" in limits and current < limits["min"]:
        failures.append(f"current {current:.6g} is below {limits['min']:.6g}")
    if "max" in limits and current > limits["max"]:
        failures.append(f"current {current:.6g} exceeds {limits['max']:.6g}")
    return failures


def _tolerance_number(key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphCheckError(
            "engine.tolerance_invalid",
            f"Drift tolerance {key!r} must be numeric.",
            "Set every tolerance value to a finite non-negative number.",
        )
    numeric = float(value)
    if not math.isfinite(numeric) or (key not in {"min", "max"} and numeric < 0):
        raise GraphCheckError(
            "engine.tolerance_invalid",
            f"Drift tolerance {key!r} has invalid value {value!r}.",
            "Set every percentage/delta tolerance to a finite non-negative number.",
        )
    return numeric


def _is_measurement(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _coerce_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    numeric = int(value)
    return numeric if numeric >= 0 else 0
