from types import SimpleNamespace

import pytest

from graphcheck.contracts.results import CoverageStatus, RunStatus
from graphcheck.reporting.coverage import calculate_coverage_status


@pytest.mark.parametrize(
    ("case", "run_status", "errored", "skipped", "expected"),
    [
        ("pass", RunStatus.COMPLETE, 0, 0, CoverageStatus.COMPLETE),
        ("fail", RunStatus.COMPLETE, 0, 0, CoverageStatus.COMPLETE),
        ("warn", RunStatus.COMPLETE, 0, 0, CoverageStatus.COMPLETE),
        ("errored", RunStatus.COMPLETE, 1, 0, CoverageStatus.PARTIAL),
        ("pass-generated", RunStatus.COMPLETE, 0, 1, CoverageStatus.PARTIAL),
        ("all-generated", RunStatus.COMPLETE, 0, 2, CoverageStatus.PARTIAL),
        ("unsupported", RunStatus.PARTIAL, 0, 1, CoverageStatus.PARTIAL),
        ("not-run", RunStatus.PARTIAL, 0, 1, CoverageStatus.PARTIAL),
        ("partial-error-skip", RunStatus.PARTIAL, 1, 1, CoverageStatus.PARTIAL),
        ("failed", RunStatus.FAILED, 0, 0, CoverageStatus.FAILED),
        ("zero-selected", RunStatus.COMPLETE, 0, 0, CoverageStatus.COMPLETE),
    ],
)
def test_calculate_coverage_status_contract(case, run_status, errored, skipped, expected):
    results = SimpleNamespace(
        run=SimpleNamespace(run_status=run_status),
        totals=SimpleNamespace(errored=errored, skipped=skipped),
    )

    assert calculate_coverage_status(results) is expected, case
