"""Results writing and offline report rendering."""

from graphcheck.reporting.history import (
    ReportHistoryError,
    ReportRun,
    delete_report_runs,
    discover_report_runs,
    find_report_run,
    format_report_comparison,
    format_report_history,
    prune_report_runs,
)
from graphcheck.reporting.html import render_html_report, write_html_report
from graphcheck.reporting.presentation import ResultPresentation, present_results
from graphcheck.reporting.writer import load_results, write_results

__all__ = [
    "ReportHistoryError",
    "ReportRun",
    "ResultPresentation",
    "delete_report_runs",
    "discover_report_runs",
    "find_report_run",
    "format_report_comparison",
    "format_report_history",
    "load_results",
    "prune_report_runs",
    "present_results",
    "render_html_report",
    "write_html_report",
    "write_results",
]
