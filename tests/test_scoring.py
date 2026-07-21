from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

import pytest

from graphcheck.scoring import (
    SEVERITY_WEIGHTS,
    ScoreCalculation,
    calculate_score,
    calculate_score_deductions,
    calculate_suite_scores,
)


@dataclass(frozen=True)
class StubCheck:
    suite_id: str
    severity: str
    verdict: str
    id: str = "check"
    executed_override: bool | None = None

    @property
    def executed(self) -> bool:
        if self.executed_override is not None:
            return self.executed_override
        return self.verdict != "skipped"


def _check(verdict: str, severity: str = "warn", suite_id: str = "suite") -> StubCheck:
    return StubCheck(suite_id=suite_id, severity=severity, verdict=verdict)


def test_weighted_score_exposes_explainable_components_and_coverage():
    score = calculate_score(
        [
            _check("pass", "error"),
            _check("fail", "error"),
            _check("warn"),
            _check("errored"),
            _check("skipped", "error"),
        ]
    )

    assert score == ScoreCalculation(
        value=38,
        earned_weight=3,
        possible_weight=8,
        executed=4,
        skipped=1,
    )
    assert score.selected == 5
    assert score.coverage_percent == 80


def test_scoring_uses_exact_half_even_rounding():
    rounds_down_to_even = [_check("pass"), *[_check("warn") for _ in range(7)]]
    rounds_up_to_even = [*[_check("pass") for _ in range(3)], *[_check("warn") for _ in range(5)]]

    assert calculate_score(rounds_down_to_even).value == 12
    assert calculate_score(rounds_up_to_even).value == 38


def test_scoring_is_invariant_to_check_order():
    checks = [
        _check("pass", "error"),
        _check("fail", "error"),
        _check("warn"),
        _check("skipped"),
    ]
    expected = calculate_score(checks)

    assert all(calculate_score(order) == expected for order in permutations(checks))


def test_deductions_reconcile_to_score_and_attribute_points_by_check():
    checks = [
        _check("pass", "error"),
        StubCheck("suite", "error", "fail", id="failed-check"),
        StubCheck("suite", "warn", "warn", id="warning-check"),
    ]

    score = calculate_score(checks)
    deductions = calculate_score_deductions(checks)

    assert score.value == 43
    assert [(item.check_id, item.points) for item in deductions] == [
        ("failed-check", 43),
        ("warning-check", 14),
    ]
    assert sum(item.points for item in deductions) == 57
    assert calculate_score_deductions(reversed(checks)) == deductions


def test_suite_scores_are_independent_and_sorted_by_suite_id():
    checks = [
        _check("pass", "error", "beta"),
        _check("pass", "warn", "alpha"),
        _check("warn", "warn", "alpha"),
    ]
    scores = calculate_suite_scores(checks)

    assert list(scores) == ["alpha", "beta"]
    assert scores["alpha"].value == 50
    assert scores["alpha"].earned_weight == 1
    assert scores["alpha"].possible_weight == 2
    assert scores["beta"].value == 100
    assert calculate_score(checks).value == 80
    assert calculate_score(checks).value != round(
        (scores["alpha"].value + scores["beta"].value) / 2
    )


def test_empty_and_all_skipped_inputs_have_null_scores():
    empty = calculate_score([])
    skipped = calculate_score([_check("skipped"), _check("skipped", "error")])

    assert empty.value is None
    assert empty.coverage_percent is None
    assert skipped.value is None
    assert skipped.coverage_percent == 0


def test_weights_are_frozen_and_invalid_inputs_fail_loudly():
    assert dict(SEVERITY_WEIGHTS) == {"error": 3, "warn": 1}
    with pytest.raises(TypeError):
        SEVERITY_WEIGHTS["error"] = 5  # type: ignore[index]
    with pytest.raises(ValueError, match="unsupported check severity"):
        calculate_score([_check("pass", "critical")])
    with pytest.raises(ValueError, match="unsupported verdict"):
        calculate_score([_check("unknown")])
    with pytest.raises(ValueError, match="non-executed check"):
        calculate_score(
            [
                StubCheck(
                    suite_id="suite",
                    severity="error",
                    verdict="pass",
                    executed_override=False,
                )
            ]
        )
