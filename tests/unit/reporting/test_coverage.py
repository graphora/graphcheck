from types import SimpleNamespace

import pytest

from graphcheck.contracts.results import CoverageStatus, RunStatus
from graphcheck.reporting.coverage import calculate_coverage_status


@pytest.mark.parametrize(
    ("run_status", "errored", "skipped", "expected"),
    [
        pytest.param(RunStatus.FAILED, 1, 1, CoverageStatus.FAILED, id="failed-run"),
        pytest.param(RunStatus.PARTIAL, 0, 0, CoverageStatus.PARTIAL, id="partial-run"),
        pytest.param(RunStatus.COMPLETE, 1, 0, CoverageStatus.PARTIAL, id="errored-check"),
        pytest.param(RunStatus.COMPLETE, 0, 1, CoverageStatus.PARTIAL, id="skipped-check"),
        pytest.param(RunStatus.COMPLETE, 0, 0, CoverageStatus.COMPLETE, id="complete-coverage"),
    ],
)
def test_calculate_coverage_status_contract(run_status, errored, skipped, expected):
    results = SimpleNamespace(
        run=SimpleNamespace(run_status=run_status),
        totals=SimpleNamespace(errored=errored, skipped=skipped),
    )

    assert calculate_coverage_status(results) is expected
