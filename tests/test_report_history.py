import json
from pathlib import Path

from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.project import write_default_project
from graphcheck.reporting import history as history_module
from graphcheck.reporting.history import (
    discover_report_runs,
    find_report_run,
    format_report_comparison,
    format_report_history,
    report_summary_json,
)
from graphcheck.reporting.writer import load_results

FIXTURES = Path(__file__).parent / "contracts" / "fixtures"
runner = CliRunner()


def _write_run(
    root: Path,
    run_id: str,
    finished_at: str,
    *,
    fixture: str = "complete",
    directory: str | None = None,
    report: bool = True,
) -> Path:
    payload = json.loads((FIXTURES / f"results.{fixture}.json").read_text(encoding="utf-8"))
    payload["run"]["id"] = run_id
    payload["run"]["started_at"] = finished_at
    payload["run"]["finished_at"] = finished_at

    run_dir = root / ".graphcheck" / "runs" / (directory or run_id)
    run_dir.mkdir(parents=True)
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps(payload), encoding="utf-8")
    load_results(results_path)
    if report:
        (run_dir / "report.html").write_text(f"report for {run_id}", encoding="utf-8")
    return run_dir


def _write_multi_suite_run(root: Path, run_id: str, finished_at: str) -> Path:
    run_dir = _write_run(root, run_id, finished_at)
    results_path = run_dir / "results.json"
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    payload["run"]["selection"]["suites"] = ["alpha", "beta"]
    payload["checks"][1]["suite_id"] = "alpha"
    payload["checks"][0]["suite_id"] = "beta"
    payload["checks"][2]["suite_id"] = "beta"
    payload["suites"] = [
        {
            "id": "alpha",
            "source_sha": "sha-alpha",
            "score": 100,
            "totals": {
                "checks": 1,
                "pass": 1,
                "fail": 0,
                "warn": 0,
                "errored": 0,
                "skipped": 0,
            },
        },
        {
            "id": "beta",
            "source_sha": "sha-beta",
            "score": 0,
            "totals": {
                "checks": 2,
                "pass": 0,
                "fail": 1,
                "warn": 1,
                "errored": 0,
                "skipped": 0,
            },
        },
    ]
    results_path.write_text(json.dumps(payload), encoding="utf-8")
    load_results(results_path)
    return run_dir


def _init_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_default_project(tmp_path)


def test_report_list_displays_newest_first_with_metadata(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_run(tmp_path, "run-old", "2026-07-01T10:00:00Z")
    _write_run(tmp_path, "run-new", "2026-07-02T10:00:00Z", fixture="partial")

    result = runner.invoke(app, ["report", "--list"])

    assert result.exit_code == 0
    assert "REPORT NAME" in result.stdout
    assert "FINISHED AT" in result.stdout
    assert "SUITE SCORES" in result.stdout
    assert "run-new" in result.stdout
    assert "partial" in result.stdout
    assert "customer-360=100" in result.stdout
    assert result.stdout.index("run-new") < result.stdout.index("run-old")


def test_report_list_shows_suite_scores_instead_of_the_overall_score(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_multi_suite_run(tmp_path, "run-multi", "2026-07-02T10:00:00Z")

    result = runner.invoke(app, ["report", "--list"])

    assert result.exit_code == 0
    assert "alpha=100, beta=0" in result.stdout
    assert "SUITE SCORES" in result.stdout


def test_report_history_orders_fractional_utc_timestamps_chronologically(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_run(tmp_path, "run-whole-second", "2026-07-01T10:00:00Z")
    _write_run(tmp_path, "run-half-second", "2026-07-01T10:00:00.500000Z")

    result = runner.invoke(app, ["report", "--list"])

    assert result.exit_code == 0
    assert result.stdout.index("run-half-second") < result.stdout.index("run-whole-second")


def test_report_list_deduplicates_latest_alias(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_run(tmp_path, "run-one", "2026-07-01T10:00:00Z")
    _write_run(
        tmp_path,
        "run-one",
        "2026-07-01T10:00:00Z",
        directory="latest",
    )

    result = runner.invoke(app, ["report", "--list"])

    assert result.exit_code == 0
    assert result.stdout.count("run-one") == 1


def test_report_open_id_opens_selected_historical_report(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_run(tmp_path, "run-old", "2026-07-01T10:00:00Z")
    _write_run(tmp_path, "run-new", "2026-07-02T10:00:00Z")
    opened = []

    def open_in_current_terminal(runs_dir, run_id, opener, on_open):
        opened.append((runs_dir, run_id, opener))
        on_open("url")
        return "url"

    monkeypatch.setattr("graphcheck.cli.open_report_explorer", open_in_current_terminal)

    result = runner.invoke(app, ["report", "--open", "run-old"])

    assert result.exit_code == 0
    assert len(opened) == 1
    assert opened[0][0] == tmp_path / ".graphcheck" / "runs"
    assert opened[0][1] == "run-old"
    assert opened[0][2] is not None
    assert "Opened report explorer for run-old" in result.stdout
    assert "Keep this terminal open; press Ctrl+C to stop." in result.stdout


def test_report_compare_highlights_regressions_between_results(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_run(
        tmp_path,
        "run-before",
        "2026-07-01T10:00:00Z",
        fixture="partial",
    )
    _write_run(tmp_path, "run-after", "2026-07-02T10:00:00Z")

    result = runner.invoke(
        app,
        ["report", "--compare", "run-before", "run-after"],
    )

    assert result.exit_code == 0
    assert "Comparing run-before -> run-after" in result.stdout
    assert "Suite scores:" in result.stdout
    assert "customer-360: 100 -> 43 (-57)" in result.stdout
    assert "Score:" not in result.stdout
    assert "Regressions (1)" in result.stdout
    assert "customer-360::account-no-orphans: skipped -> warn" in result.stdout
    assert "customer-360::cq-001: fail" in result.stdout


def test_report_compare_includes_added_and_removed_suite_scores(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    _write_multi_suite_run(tmp_path, "run-before", "2026-07-01T10:00:00Z")
    _write_run(tmp_path, "run-after", "2026-07-02T10:00:00Z")

    result = runner.invoke(
        app,
        ["report", "--compare", "run-before", "run-after"],
    )

    assert result.exit_code == 0
    assert "alpha: 100 -> n/a" in result.stdout
    assert "beta: 0 -> n/a" in result.stdout
    assert "customer-360: n/a -> 43" in result.stdout


def test_report_prune_keeps_newest_history_and_latest_alias(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    oldest = _write_run(tmp_path, "run-a", "2026-07-01T10:00:00Z")
    middle = _write_run(tmp_path, "run-b", "2026-07-02T10:00:00Z")
    newest = _write_run(tmp_path, "run-c", "2026-07-03T10:00:00Z")
    unknown = tmp_path / ".graphcheck" / "runs" / "manual-notes"
    unknown.mkdir()
    (unknown / "notes.txt").write_text("keep me", encoding="utf-8")
    latest = _write_run(
        tmp_path,
        "run-c",
        "2026-07-03T10:00:00Z",
        directory="latest",
    )

    result = runner.invoke(app, ["report", "--prune", "--keep", "2"])

    assert result.exit_code == 0
    assert "run-a" in result.stdout
    assert not oldest.exists()
    assert middle.exists()
    assert newest.exists()
    assert latest.exists()
    assert unknown.exists()


def test_report_prune_rejects_unsafe_keep_count(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    existing = _write_run(tmp_path, "run-a", "2026-07-01T10:00:00Z")

    result = runner.invoke(app, ["report", "--prune", "--keep", "0"])

    assert result.exit_code == 2
    assert "--keep must be at least 1" in result.stderr
    assert existing.exists()


def test_report_prune_requires_retention_count(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["report", "--prune"])

    assert result.exit_code == 2
    assert "--prune requires --keep COUNT" in result.stderr


def test_report_failures_only_writes_diagnostic_report(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    run_dir = _write_run(
        tmp_path,
        "run-one",
        "2026-07-01T10:00:00Z",
        directory="latest",
    )

    result = runner.invoke(app, ["report", "--failures-only"])

    diagnostic = run_dir / "report.failures.html"
    assert result.exit_code == 0
    assert diagnostic.exists()
    html = diagnostic.read_text(encoding="utf-8")
    assert "Which accounts does a customer control" in html
    assert "Accounts are connected to a Customer" in html
    assert "Customer.tax_id is present" not in html


def test_report_failures_only_can_generate_and_open_selected_run(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    run_dir = _write_run(tmp_path, "run-one", "2026-07-01T10:00:00Z")
    opened = []
    monkeypatch.setattr("graphcheck.cli.webbrowser.open", lambda url: opened.append(url) or True)

    result = runner.invoke(app, ["report", "--open", "run-one", "--failures-only"])

    diagnostic = run_dir / "report.failures.html"
    assert result.exit_code == 0
    assert opened == [diagnostic.resolve().as_uri()]


def test_report_compare_is_a_standalone_action(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["report", "--compare", "run-one", "run-two", "--open"],
    )

    assert result.exit_code == 2
    assert "standalone actions" in result.stderr


def test_report_id_requires_open(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["report", "run-one"])

    assert result.exit_code == 2
    assert "A report ID requires --open" in result.stderr


def test_report_history_reads_schema_1_0_artifacts(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    run_dir = _write_run(tmp_path, "legacy-run", "2026-07-01T10:00:00Z")
    results_path = run_dir / "results.json"
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "1.0"
    results_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["report", "--list"])

    assert result.exit_code == 0
    assert "legacy-run" in result.stdout
    assert json.loads(results_path.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_report_history_uses_summaries_and_loads_only_compared_runs(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    for run_id, finished_at in (
        ("run-one", "2026-07-01T10:00:00Z"),
        ("run-two", "2026-07-02T10:00:00Z"),
        ("run-three", "2026-07-03T10:00:00Z"),
    ):
        directory = runs_dir / run_id
        directory.mkdir(parents=True)
        payload = json.loads((FIXTURES / "results.complete.json").read_text(encoding="utf-8"))
        payload["run"]["id"] = run_id
        payload["run"]["started_at"] = finished_at
        payload["run"]["finished_at"] = finished_at
        results_path = directory / "results.json"
        results_path.write_text(json.dumps(payload), encoding="utf-8")
        (directory / "summary.json").write_text(
            report_summary_json(load_results(results_path)), encoding="utf-8"
        )
        (directory / "report.html").write_text("report", encoding="utf-8")

    real_load = history_module.load_results
    loaded: list[Path] = []

    def count_loads(path):
        loaded.append(path)
        return real_load(path)

    monkeypatch.setattr(history_module, "load_results", count_loads)
    records = discover_report_runs(runs_dir)

    assert "run-three" in format_report_history(records)
    assert loaded == []

    first = find_report_run(records, "run-one")
    second = find_report_run(records, "run-two")
    assert "Comparing run-one -> run-two" in format_report_comparison(first, second)
    assert loaded == [first.results_path, second.results_path]


def test_report_summary_maps_errored_checks_to_partial():
    raw = json.loads((FIXTURES / "results.complete.json").read_text(encoding="utf-8"))
    raw["checks"][0].update(
        verdict="errored",
        measured=None,
        evidence=None,
        error={
            "code": "query.execution",
            "message": "Query execution failed",
            "fix": "Check the generated Cypher",
        },
    )
    raw["totals"].update(fail=0, errored=1)
    raw["suites"][0]["totals"].update(fail=0, errored=1)

    summary = json.loads(report_summary_json(load_results(raw)))

    assert summary["schema_version"] == "1.0"
    assert summary["status"] == "partial"


def test_report_summary_keeps_pre_check_failure_failed():
    raw = json.loads((FIXTURES / "results.failed.json").read_text(encoding="utf-8"))
    raw["run"]["error"]["code"] = "neo4j.credential_not_read_only"

    summary = json.loads(report_summary_json(load_results(raw)))

    assert summary["status"] == "failed"
