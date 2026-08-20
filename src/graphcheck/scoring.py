from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

SEVERITY_WEIGHTS: Mapping[str, int] = MappingProxyType({"error": 3, "warn": 1})
_EXECUTED_VERDICTS = {"pass", "fail", "warn", "errored"}


class ScorableCheck(Protocol):
    severity: Any
    verdict: Any
    suite_id: str

    @property
    def executed(self) -> bool: ...


class IdentifiedScorableCheck(ScorableCheck, Protocol):
    id: str


@dataclass(frozen=True, slots=True)
class ScoreCalculation:
    value: int | None
    earned_weight: int
    possible_weight: int
    executed: int
    skipped: int

    @property
    def selected(self) -> int:
        return self.executed + self.skipped

    @property
    def coverage_percent(self) -> int | None:
        if self.selected == 0:
            return None
        return _round_percentage(self.executed, self.selected)


@dataclass(frozen=True, slots=True)
class ScoreDeduction:
    suite_id: str
    check_id: str
    points: int


def calculate_score(checks: Iterable[ScorableCheck]) -> ScoreCalculation:
    """Calculate the frozen severity-weighted pass rate in one deterministic pass."""

    earned_weight = 0
    possible_weight = 0
    executed = 0
    skipped = 0
    for check in checks:
        verdict = _value(check.verdict)
        if not check.executed:
            if verdict != "skipped":
                raise ValueError(f"non-executed check has unsupported verdict {verdict!r}")
            skipped += 1
            continue
        if verdict not in _EXECUTED_VERDICTS:
            raise ValueError(f"executed check has unsupported verdict {verdict!r}")
        severity = _value(check.severity)
        try:
            weight = SEVERITY_WEIGHTS[severity]
        except KeyError as exc:
            raise ValueError(f"unsupported check severity {severity!r}") from exc
        possible_weight += weight
        executed += 1
        if verdict == "pass":
            earned_weight += weight

    value = None if possible_weight == 0 else _round_percentage(earned_weight, possible_weight)
    return ScoreCalculation(
        value=value,
        earned_weight=earned_weight,
        possible_weight=possible_weight,
        executed=executed,
        skipped=skipped,
    )


def calculate_suite_scores(
    checks: Iterable[ScorableCheck],
) -> dict[str, ScoreCalculation]:
    """Calculate independently rounded scores for every represented suite."""

    members: dict[str, list[ScorableCheck]] = {}
    for check in checks:
        members.setdefault(check.suite_id, []).append(check)
    return {suite_id: calculate_score(members[suite_id]) for suite_id in sorted(members)}


def calculate_score_deductions(
    checks: Iterable[IdentifiedScorableCheck],
) -> tuple[ScoreDeduction, ...]:
    """Attribute the rounded points lost to individual non-passing checks."""

    members = tuple(checks)
    score = calculate_score(members)
    if score.value is None:
        return ()

    penalties: list[tuple[str, str, int]] = []
    identities: set[tuple[str, str]] = set()
    for check in members:
        identity = (check.suite_id, check.id)
        if identity in identities:
            raise ValueError(f"duplicate check identity {identity!r}")
        identities.add(identity)
        verdict = _value(check.verdict)
        if check.executed and verdict != "pass":
            penalties.append((check.suite_id, check.id, SEVERITY_WEIGHTS[_value(check.severity)]))
    if not penalties:
        return ()

    points_lost = 100 - score.value
    lost_weight = sum(weight for _, _, weight in penalties)
    allocations: dict[tuple[str, str], int] = {}
    remainders: list[tuple[int, str, str]] = []
    for suite_id, check_id, weight in penalties:
        points, remainder = divmod(points_lost * weight, lost_weight)
        allocations[(suite_id, check_id)] = points
        remainders.append((remainder, suite_id, check_id))

    unallocated = points_lost - sum(allocations.values())
    for _, suite_id, check_id in sorted(
        remainders,
        key=lambda item: (-item[0], item[1], item[2]),
    )[:unallocated]:
        allocations[(suite_id, check_id)] += 1

    return tuple(
        ScoreDeduction(suite_id=suite_id, check_id=check_id, points=points)
        for (suite_id, check_id), points in sorted(allocations.items())
    )


def _round_percentage(numerator: int, denominator: int) -> int:
    """Round an exact non-negative ratio to a percentage using half-to-even."""

    if numerator < 0 or denominator <= 0 or numerator > denominator:
        raise ValueError("percentage requires 0 <= numerator <= denominator")
    quotient, remainder = divmod(numerator * 100, denominator)
    comparison = remainder * 2 - denominator
    if comparison > 0 or (comparison == 0 and quotient % 2 == 1):
        quotient += 1
    return quotient


def _value(value: object) -> str:
    return str(getattr(value, "value", value))
