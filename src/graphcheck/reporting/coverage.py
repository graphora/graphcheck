"""Canonical coverage-status policy shared by every result surface."""

from __future__ import annotations

from graphcheck.contracts.results import CoverageStatus, Results, RunStatus


def calculate_coverage_status(results: Results) -> CoverageStatus:
    """Derive report coverage without changing the engine's execution status."""
    if results.run.run_status is RunStatus.FAILED:
        return CoverageStatus.FAILED
    return (
        CoverageStatus.PARTIAL
        if results.run.run_status is not RunStatus.COMPLETE
        or results.totals.errored > 0
        or results.totals.skipped > 0
        else CoverageStatus.COMPLETE
    )
