import json
import logging
import re
import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from graphcheck import cli as cli_module
from graphcheck.cli import (
    _check_summary,
    _execution_error_table,
    _exit_code_color,
    _load_suite_inputs,
    _not_evaluated_table,
    _result_text,
    _suite_coverage_style,
    _suite_score_style,
    _suite_score_table,
    _write_run_artifacts,
    app,
)
from graphcheck.connection_profiles import write_default_profiles
from graphcheck.contracts.results import Capabilities, ResultsTarget
from graphcheck.engine import Engine as CoreEngine
from graphcheck.engine import EngineConfig
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.neo4j_adapter import Counts, QueryResult
from graphcheck.packs.catalog import PackCatalog, builtin_pack_catalog
from graphcheck.project import write_default_project
from graphcheck.reporting.history import discover_report_runs
from graphcheck.reporting.presentation import present_results
from graphcheck.reporting.writer import json_compatible, load_results

runner = CliRunner()
FIXTURES = Path(__file__).parent / "contracts" / "fixtures"
TARGET = ResultsTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:test-graph",
    capabilities=Capabilities(apoc=False, count_store=True),
    labels=[],
    relationship_types=[],
)


class FakeClient:
    def __init__(self, results=(), *, probe_error=None, target=TARGET, counts=None):
        self.results = list(results)
        self.probe_error = probe_error
        self.target = target
        self.counts = counts or Counts(nodes=1250, relationships=3480)
        self.read_calls = []
        self.closed = False

    def probe(self, *, timeout_s=None):
        if self.probe_error is not None:
            raise self.probe_error
        return self.target, object(), self.counts

    def run_read_result(self, query, params, *, timeout_s=None):
        self.read_calls.append((query, params, timeout_s))
        return self.results.pop(0)

    def close(self):
        self.closed = True


def _project(tmp_path: Path, suites: dict[str, str]) -> None:
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    checks = tmp_path / "checks"
    for name, text in suites.items():
        (checks / name).write_text(text, encoding="utf-8")


def _payload(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / ".graphcheck" / "runs" / "latest" / "results.json").read_text(encoding="utf-8")
    )


def _report(tmp_path: Path) -> str:
    return (tmp_path / ".graphcheck" / "runs" / "latest" / "report.html").read_text(
        encoding="utf-8"
    )


def _assert_no_traceback(result) -> None:
    assert "Traceback" not in f"{result.stdout}\n{result.stderr}"


def test_artifact_value_normalization_is_deterministic_for_yaml_sets():
    assert json_compatible({"values": {"gamma", "alpha", "beta"}}) == {
        "values": ["alpha", "beta", "gamma"]
    }


def test_artifact_writer_preserves_versioned_runs_and_refreshes_latest(tmp_path):
    first = load_results(FIXTURES / "results.complete.json")
    second = load_results(FIXTURES / "results.complete.json")
    second.run.finished_at = "2026-07-06T09:03:41Z"
    runs_dir = tmp_path / "runs"

    _write_run_artifacts(first, runs_dir)
    latest_results, latest_report = _write_run_artifacts(second, runs_dir)

    first_name = "neo4j_20260706T090241000000Z"
    second_name = "neo4j_20260706T090341000000Z"
    assert (runs_dir / first_name / "results.json").is_file()
    assert (runs_dir / first_name / "report.html").is_file()
    assert (runs_dir / second_name / "results.json").is_file()
    assert (runs_dir / second_name / "report.html").is_file()
    assert (runs_dir / second_name / "summary.json").is_file()
    assert load_results(latest_results).run.id == second_name
    assert latest_report.is_file()
    assert latest_results.read_bytes() == (runs_dir / second_name / "results.json").read_bytes()
    assert latest_report.read_bytes() == (runs_dir / second_name / "report.html").read_bytes()
    assert {record.id for record in discover_report_runs(runs_dir)} == {first_name, second_name}


def test_artifact_writer_preserves_runs_completed_within_the_same_second(tmp_path):
    first = load_results(FIXTURES / "results.complete.json")
    second = load_results(FIXTURES / "results.complete.json")
    first.run.finished_at = "2026-07-06T09:03:41.100000Z"
    second.run.finished_at = "2026-07-06T09:03:41.900000Z"
    runs_dir = tmp_path / "runs"

    _write_run_artifacts(first, runs_dir)
    _write_run_artifacts(second, runs_dir)

    assert {record.id for record in discover_report_runs(runs_dir)} == {
        "neo4j_20260706T090341100000Z",
        "neo4j_20260706T090341900000Z",
    }


def test_concurrent_latest_publication_is_serialized(tmp_path):
    # MCP 2.0 runs synchronous tools in worker threads, so two runs can publish the shared
    # `latest` alias at the same time. The publication lock must keep the exists/move/swap
    # sequence from interleaving, so neither publisher fails and `latest` is a complete pair
    # from exactly one run.
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)

    def make(finished_at: str):
        results = load_results(FIXTURES / "results.complete.json")
        results.run.finished_at = finished_at
        return results

    run_ids = {
        "2026-07-06T09:03:41.100000Z": "neo4j_20260706T090341100000Z",
        "2026-07-06T09:03:41.900000Z": "neo4j_20260706T090341900000Z",
    }
    payloads = [make(finished_at) for finished_at in run_ids]

    barrier = threading.Barrier(len(payloads))
    errors: list[Exception] = []
    errors_lock = threading.Lock()

    def publish(results):
        try:
            barrier.wait()
            _write_run_artifacts(results, runs_dir)
        except Exception as exc:
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=publish, args=(results,)) for results in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent publication raised: {errors}"

    latest_results = runs_dir / "latest" / "results.json"
    latest_report = runs_dir / "latest" / "report.html"
    assert latest_results.is_file()
    assert latest_report.is_file()
    assert load_results(latest_results).run.id in set(run_ids.values())
    assert {record.id for record in discover_report_runs(runs_dir)} == set(run_ids.values())


def test_artifact_writer_uses_target_neutral_id_for_redacted_runs(tmp_path):
    results = load_results(FIXTURES / "results.complete.json")
    results.run.target.database = "patient-prod"
    results.checks[0].params["customer_id"] = "redacted_20260706T090241000000Z"
    redacted = cli_module.redact_results(results)
    runs_dir = tmp_path / "runs"

    latest_results, latest_report = _write_run_artifacts(redacted, runs_dir)

    run_id = "redacted_collision1_20260706T090241000000Z"
    exported = latest_results.read_text(encoding="utf-8")
    html = latest_report.read_text(encoding="utf-8")
    assert (runs_dir / run_id / "results.json").is_file()
    assert load_results(latest_results).run.id == run_id
    assert "patient-prod" not in html
    assert "redacted_20260706T090241000000Z" not in html
    assert "redacted_20260706T090241000000Z" not in exported
    assert "patient-prod" not in json.loads(exported)["run"]["id"]


def test_artifact_writer_keeps_previous_latest_pair_when_refresh_fails(tmp_path, monkeypatch):
    first = load_results(FIXTURES / "results.complete.json")
    second = load_results(FIXTURES / "results.complete.json")
    second.run.finished_at = "2026-07-06T09:03:41Z"
    runs_dir = tmp_path / "runs"
    latest_results, latest_report = _write_run_artifacts(first, runs_dir)
    previous_results = latest_results.read_bytes()
    previous_report = latest_report.read_bytes()
    from graphcheck.application import artifacts as artifacts_module

    real_publish = artifacts_module.publish_run_directory

    def fail_latest_refresh(artifacts, directory):
        if directory.name == "latest":
            raise OSError("simulated latest report failure")
        return real_publish(artifacts, directory)

    monkeypatch.setattr(artifacts_module, "publish_run_directory", fail_latest_refresh)

    with pytest.raises(OSError, match="simulated latest report failure"):
        _write_run_artifacts(second, runs_dir)

    assert latest_results.read_bytes() == previous_results
    assert latest_report.read_bytes() == previous_report
    assert (runs_dir / "neo4j_20260706T090341000000Z" / "results.json").is_file()
    assert not list(runs_dir.glob(".*.staging-*"))
    assert not list(runs_dir.glob(".*.backup-*"))


def test_artifact_writer_renders_once_for_history_and_latest(tmp_path, monkeypatch):
    from graphcheck.application import artifacts as artifacts_module

    results = load_results(FIXTURES / "results.complete.json")
    calls = 0
    real_render = artifacts_module.render_run_artifacts

    def render_once(value, **kwargs):
        nonlocal calls
        calls += 1
        return real_render(value, **kwargs)

    monkeypatch.setattr(artifacts_module, "render_run_artifacts", render_once)

    _write_run_artifacts(results, tmp_path / "runs")

    assert calls == 1


_SMOKE_SUITE = {
    "smoke.yml": """\
suite: smoke
competency:
  - id: smoke
    question: Does the graph return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
}


def test_execute_run_publishes_once_and_preserves_result_on_artifact_failure(tmp_path, monkeypatch):
    from graphcheck.application.run import RunRequest, execute_run

    _project(tmp_path, _SMOKE_SUITE)
    monkeypatch.chdir(tmp_path)
    client = FakeClient([QueryResult([{"value": 1}], ("value",), ())])

    writer_calls = 0

    def failing_writer(results, runs_dir, *, render_observer=None):
        nonlocal writer_calls
        writer_calls += 1
        raise OSError("simulated artifact write failure")

    outcome = execute_run(
        RunRequest(profile=None, suite_ids=["smoke"], tags=[], fail_fast=False),
        client_factory=lambda profile, max_concurrency: client,
        artifact_writer=failing_writer,
    )

    # Publication is attempted exactly once, never retried through the exception translation.
    assert writer_calls == 1
    # The completed engine result is preserved, not replaced by a run.configuration failure.
    assert outcome.results.run.error is None
    assert outcome.results.run.status.value != "failed"
    # The failure surfaces as artifact_error with a real artifact-write timing boundary.
    assert isinstance(outcome.artifact_error, OSError)
    assert outcome.results_path is None
    assert outcome.artifact_started_perf is not None


def test_execute_run_classifies_unexpected_engine_fault_as_engine_unexpected(tmp_path, monkeypatch):
    from graphcheck.application import run as run_module
    from graphcheck.application.run import RunRequest, execute_run

    _project(tmp_path, _SMOKE_SUITE)
    monkeypatch.chdir(tmp_path)
    client = FakeClient([QueryResult([{"value": 1}], ("value",), ())])

    class ExplodingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise RuntimeError("unexpected engine fault")

    monkeypatch.setattr(run_module, "Engine", ExplodingEngine)

    writes: list[object] = []

    def capture_writer(results, runs_dir, *, render_observer=None):
        writes.append(results)
        return runs_dir / "latest" / "results.json", runs_dir / "latest" / "report.html"

    outcome = execute_run(
        RunRequest(profile=None, suite_ids=["smoke"], tags=[], fail_fast=False),
        client_factory=lambda profile, max_concurrency: client,
        artifact_writer=capture_writer,
    )

    # An unexpected Engine.run() fault is an engine error, not a configuration error, and the
    # failed result is written exactly once.
    assert outcome.results.run.error is not None
    assert outcome.results.run.error.code == "engine.unexpected"
    assert len(writes) == 1


def test_execute_run_surfaces_read_only_credential_rejection(tmp_path, monkeypatch):
    from graphcheck.application.run import RunRequest, execute_run

    _project(tmp_path, _SMOKE_SUITE)
    monkeypatch.chdir(tmp_path)

    class RejectingClient(FakeClient):
        def verify_read_only_credential(self):
            raise GraphCheckError(
                "neo4j.credential_not_read_only",
                "The configured Neo4j credential is not read-only.",
                "Use a dedicated read-only credential, then run the suite again.",
            )

    client = RejectingClient([QueryResult([{"value": 1}], ("value",), ())])

    def capture_writer(results, runs_dir, *, render_observer=None):
        return runs_dir / "latest" / "results.json", runs_dir / "latest" / "report.html"

    outcome = execute_run(
        RunRequest(
            profile=None,
            suite_ids=["smoke"],
            tags=[],
            fail_fast=False,
            verify_read_only_credential=True,
        ),
        client_factory=lambda profile, max_concurrency: client,
        artifact_writer=capture_writer,
    )

    # The read-only guard runs and its rejection becomes the run failure; the engine never
    # executes a query.
    assert outcome.results.run.error is not None
    assert outcome.results.run.error.code == "neo4j.credential_not_read_only"
    assert client.read_calls == []


def test_run_filters_suite_and_tag_writes_artifacts_and_prints_summary(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "selected.yml": """\
suite: selected
competency:
  - id: production
    tags: [production]
    question: Does production return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
  - id: development
    tags: [development]
    question: Does development return a value?
    query: RETURN 2 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
            "other.yml": """\
suite: other
competency:
  - id: other
    question: Does another suite return a value?
    query: RETURN 3 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
        },
    )
    client = FakeClient([QueryResult([{"value": 1}], ("value",), ())])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            "selected",
            "--select",
            "tag:production",
            "--fail-fast",
        ],
    )

    assert result.exit_code == 0
    payload = _payload(tmp_path)
    assert payload["run"]["selection"] == {
        "suites": ["selected"],
        "tags": ["production"],
        "fail_fast": True,
    }
    historical = tmp_path / ".graphcheck" / "runs" / payload["run"]["id"]
    assert (historical / "results.json").is_file()
    assert (historical / "report.html").is_file()
    assert [check["id"] for check in payload["checks"]] == ["production"]


@pytest.mark.parametrize("redact_option", ["--redact", "--redacted"])
def test_run_redact_writes_only_verified_masked_literals(tmp_path, monkeypatch, redact_option):
    _project(
        tmp_path,
        {
            "sensitive.yml": """\
suite: sensitive
competency:
  - id: customer
    question: Does the customer exist?
    query: RETURN $customer_id AS value
    params: {customer_id: CUST-SECRET-901}
    expect: {rows: {exactly: 1}, columns: [value]}
"""
        },
    )
    client = FakeClient([QueryResult([{"value": "CUST-SECRET-901"}], ("value",), ())])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run", redact_option])

    assert result.exit_code == 0
    assert "Target:" not in result.stdout
    assert "GraphCheck redacted run " in result.stdout
    assert "GraphCheck run " not in result.stdout
    assert "sensitive" not in result.stdout
    assert "customer" not in result.stdout
    exported = (tmp_path / ".graphcheck" / "runs" / "latest" / "results.json").read_text(
        encoding="utf-8"
    )
    payload = json.loads(exported)
    assert "CUST-SECRET-901" not in exported
    assert payload["run"]["redaction"] == {"applied": True, "policy": "mask"}
    assert payload["checks"][0]["params"] == {"customer_id": "[REDACTED]"}
    assert payload["checks"][0]["measured"] == {
        "columns": ["[REDACTED]"],
        "empty": "[REDACTED]",
        "rows": "[REDACTED]",
    }
    assert payload["checks"][0]["verdict"] == "pass"
    assert payload["totals"]["pass"] == 1
    report = (tmp_path / ".graphcheck" / "runs" / "latest" / "report.html").read_text(
        encoding="utf-8"
    )
    assert "CUST-SECRET-901" not in report
    assert '<meta name="graphcheck-redaction" content="mask">' in report
    assert '<span class="status-pill status-pill-redacted">DETAILS REDACTED</span>' in report
    assert "<h2>Graph Health Overview</h2>" in report
    assert '<span class="meta-label">Target Graph</span>' not in report
    assert '<span class="meta-label">Nodes</span>' not in report
    assert '<span class="meta-label">Relationships</span>' not in report
    assert "<strong>Expected:</strong>" not in report
    assert "<strong>Measured:</strong>" not in report
    assert "<h4>Compiled Cypher</h4>" not in report
    assert "View Details & Evidence" not in report
    assert 'id="toggle-details-btn"' not in report
    assert '<span class="check-pattern">Pattern: <code>competency-shape</code></span>' in report


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [(["run"], 2), (["run", "--concurrency", "3"], 3)],
)
def test_run_concurrency_precedence_cli_over_project(tmp_path, monkeypatch, arguments, expected):
    _project(
        tmp_path,
        {
            "suite.yml": """\
suite: concurrency
competency:
  - id: value
    question: Is there one value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
        },
    )
    project_path = tmp_path / "graphcheck.yml"
    project_path.write_text(
        project_path.read_text(encoding="utf-8").replace("concurrency: 1", "concurrency: 2"),
        encoding="utf-8",
    )
    client = FakeClient([QueryResult([{"value": 1}], ("value",), ())])
    captured = []

    def build(profile, *, max_concurrency):
        captured.append(max_concurrency)
        return client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", build)

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0
    assert captured == [expected]
    report = tmp_path / ".graphcheck" / "runs" / "latest" / "report.html"
    html = report.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "http://" not in html and "https://" not in html
    assert html.count("<script>") == 1
    assert "function filterChecks()" in html
    assert ' src="' not in html and ' href="' not in html
    assert "Result: No failures. All 1 selected check passed." in result.stdout
    assert "Coverage:" not in result.stdout
    target = "Target: neo4j · Neo4j 5.18.0 community · 1,250 nodes · 3,480 relationships"
    assert target in result.stdout
    assert result.stdout.index(target) < result.stdout.index("GraphCheck run ")
    assert f"{target}\nGraphCheck run " in result.stdout
    assert "Checks: 1 | passed 1" in result.stdout
    assert "Exit code: 0" in result.stdout
    assert result.stdout.endswith("\n\n")
    assert "Results and Report saved to:" in result.stdout
    assert "Results:" not in result.stdout
    assert "Report:" not in result.stdout
    assert "Suite selected:" not in result.stdout
    assert len(client.read_calls) == 1
    assert client.closed is True


def test_run_lists_generated_check_as_not_evaluated_without_repeating_passes(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "generated.yml": """\
suite: generated-suite
generated: true
competency:
  - id: draft-check
    question: Does the draft return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
        },
    )
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 2
    normalized = " ".join(result.stdout.split())
    assert "Not evaluated:" not in result.stdout
    assert "Suite" in result.stdout and "Check" in result.stdout and "Reason" in result.stdout
    assert "draft-check" in result.stdout
    assert "generated:" in normalized
    assert "draft-check — pass" not in result.stdout
    lines = result.stdout.splitlines()
    artifacts_line = next(i for i, line in enumerate(lines) if "Results and Report saved" in line)
    assert lines[artifacts_line - 1] == ""
    assert client.read_calls == []


def test_not_evaluated_table_italicizes_check_names():
    results = load_results(FIXTURES / "results.generated-only.json")
    output = StringIO()
    table = _not_evaluated_table(results)
    table.columns[1].no_wrap = True

    Console(
        file=output,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        width=180,
    ).print(table)

    rendered = output.getvalue()
    assert "Suite" in rendered and "Check" in rendered and "Reason" in rendered
    assert "\x1b[3mcustomer-360\x1b[0m" in rendered
    assert "\x1b[3mdraft competency check awaiting approval\x1b[0m" in rendered
    assert "cq-draft" in rendered


@pytest.mark.parametrize(
    ("exit_code", "color"),
    [
        (0, typer.colors.GREEN),
        (1, typer.colors.RED),
        (2, typer.colors.YELLOW),
        (3, typer.colors.RED),
    ],
)
def test_exit_code_uses_semantic_color(exit_code, color):
    assert _exit_code_color(exit_code) == color


def test_run_prints_aligned_multi_suite_score_table(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "alpha.yml": """\
suite: alpha
competency:
  - id: passing
    question: Does alpha return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
            "beta.yml": """\
suite: beta
competency:
  - id: warning
    severity: warn
    question: Is beta empty?
    query: RETURN 2 AS value
    expect: {empty: true}
""",
        },
    )
    client = FakeClient(
        [
            QueryResult([{"value": 1}], ("value",), ()),
            QueryResult(
                [
                    {
                        "value": 2,
                        "evidence": {
                            "kind": "node",
                            "id": "node-2",
                            "labels": ["Example"],
                        },
                    }
                ],
                ("value", "evidence"),
                (),
            ),
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 2
    assert "Score breakdown by check suite:" in result.stdout
    assert "Suite" in result.stdout
    assert "Score" in result.stdout
    assert "Check Coverage" in result.stdout
    assert "Passed" in result.stdout
    assert "Failed" in result.stdout
    assert "Warnings" in result.stdout
    assert "Errored" in result.stdout
    assert "Skipped" in result.stdout
    assert "alpha" in result.stdout
    assert "100/100" in result.stdout
    assert "beta" in result.stdout
    assert "0/100" in result.stdout
    assert "Suite alpha:" not in result.stdout
    assert "│" not in result.stdout
    assert "┼" not in result.stdout
    assert "+" not in result.stdout
    assert "|" not in result.stdout
    assert sum(bool(line) and set(line) == {"─"} for line in result.stdout.splitlines()) == 1
    assert not any(bool(line) and set(line) == {"-"} for line in result.stdout.splitlines())
    assert result.stdout.count("1/1") == 2
    assert "Overall:" not in result.stdout
    assert "Checks: 2" not in result.stdout
    assert "Score: 75" not in result.stdout
    assert "Exit code: 2" in result.stdout
    assert "Result: 1 warning." in result.stdout
    assert "Coverage:" not in result.stdout
    assert result.stdout.index("GraphCheck run ") < result.stdout.index(
        "Score breakdown by check suite:"
    )
    assert result.stdout.index("Score breakdown by check suite:") < result.stdout.index("Result:")
    assert result.stdout.index("Result:") < result.stdout.index("Results and Report saved to:")
    assert result.stdout.index("Results and Report saved to:") < result.stdout.index("Exit code: 2")
    assert "\n\nResult: 1 warning." in result.stdout
    assert "\nResults and Report saved to:" in result.stdout
    assert "\nExit code: 2" in result.stdout
    payload = _payload(tmp_path)
    assert [(suite["id"], suite["score"]) for suite in payload["suites"]] == [
        ("alpha", 100),
        ("beta", 0),
    ]


def test_redacted_multi_suite_output_uses_report_aliases(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "alpha.yml": """\
suite: alpha
competency:
  - id: alpha-check
    question: Does alpha return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
            "beta.yml": """\
suite: beta
competency:
  - id: beta-check
    question: Does beta return a value?
    query: RETURN 2 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
        },
    )
    client = FakeClient(
        [
            QueryResult([{"value": 1}], ("value",), ()),
            QueryResult([{"value": 2}], ("value",), ()),
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run", "--redact"])

    payload = _payload(tmp_path)
    report = _report(tmp_path)
    suite_ids = [suite["id"] for suite in payload["suites"]]
    assert result.exit_code == 0
    assert suite_ids == ["suite-1", "suite-2"]
    assert all(suite_id in result.stdout and suite_id in report for suite_id in suite_ids)
    assert "alpha" not in result.stdout and "beta" not in result.stdout
    assert "alpha-check" not in result.stdout and "beta-check" not in result.stdout
    assert "alpha" not in report and "beta" not in report
    assert "Target:" not in result.stdout
    assert re.search(r"GraphCheck redacted run [^:]+: complete", result.stdout)


def test_redacted_run_masks_check_error_output(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "secret.yml": """\
suite: secret-suite
competency:
  - id: secret-check
    question: Can the secret query execute?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        },
    )

    class ErroredClient(FakeClient):
        def run_read_result(self, query, params, *, timeout_s=None):
            raise GraphCheckError(
                "neo4j.query_failed",
                "Query failed for secret-check.",
                "Fix secret-suite before retrying.",
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: ErroredClient())

    result = runner.invoke(app, ["run", "--redact"])

    output = f"{result.stdout}\n{result.stderr}"
    assert result.exit_code == 1
    assert "secret-suite" not in output and "secret-check" not in output
    assert "suite-1" in output and "check-1" in output
    assert "[REDACTED]" in output
    assert "Target:" not in output
    assert "The following check(s) have execution errors:" in output
    assert "Suggested fixes are provided" not in output
    table = _execution_error_table(load_results(_payload(tmp_path)))
    assert [column.header for column in table.columns] == ["Suite", "Check", "Reason"]


def test_multi_suite_score_table_applies_semantic_colors_only_to_non_zero_values():
    results = load_results(FIXTURES / "results.clean.json")
    green = results.suites[0].model_copy(deep=True)
    green.id = "green"
    yellow = load_results(FIXTURES / "results.complete.json").suites[0].model_copy(deep=True)
    yellow.id = "yellow"
    yellow.score = 86
    red = load_results(FIXTURES / "results.generated-only.json").suites[0].model_copy(deep=True)
    red.id = "red"
    red.score = 49
    results.suites = [green, yellow, red]
    output = StringIO()

    table = _suite_score_table(results)
    Console(
        file=output,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        width=140,
    ).print(table)
    rendered = output.getvalue()

    assert {column.width for column in table.columns[3:]} == {8}
    assert _suite_score_style(None) == "white"
    assert _suite_score_style(100) == "green"
    assert _suite_score_style(99) == "yellow"
    assert _suite_score_style(50) == "yellow"
    assert _suite_score_style(49) == "red"
    assert _suite_coverage_style(2, 2) == "green"
    assert _suite_coverage_style(0, 1) == "yellow"
    assert "\x1b[1;37mSuite " in rendered
    assert "\x1b[3mgreen" in rendered
    assert "\x1b[3myellow" in rendered
    assert "\x1b[3mred" in rendered
    assert "\x1b[32m100/100\x1b[0m" in rendered
    assert "\x1b[33m 86/100\x1b[0m" in rendered
    assert "\x1b[31m 49/100\x1b[0m" in rendered
    assert "\x1b[32m           2/2\x1b[0m" in rendered
    assert "\x1b[32m           3/3\x1b[0m" in rendered
    assert "\x1b[33m           0/1\x1b[0m" in rendered
    assert "\x1b[32m       2\x1b[0m" in rendered
    assert "\x1b[31m       1\x1b[0m" in rendered
    assert "\x1b[33m       1\x1b[0m" in rendered
    assert re.search(r"\x1b\[90m +1\x1b\[0m", rendered)
    assert all(f"\x1b[{code}m-\x1b[0m" not in rendered for code in (31, 32, 33, 35, 90))

    single_suite = _check_summary(results.totals)
    assert "passed \x1b[32m2\x1b[0m" in single_suite
    assert "failed 0 | warnings 0 | errored 0 | skipped 0" in single_suite
    assert all(
        f"\x1b[{code}m{label}" not in single_suite
        for code in (31, 32, 33, 35, 90)
        for label in ("passed", "failed", "warnings", "errored", "skipped")
    )

    partial = present_results(load_results(FIXTURES / "results.partial.json"))
    output = StringIO()
    Console(
        file=output,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        width=140,
    ).print(_result_text(partial))
    assert "\x1b[3mcustomer-360\x1b[0m" in output.getvalue()


def test_failed_suite_score_table_uses_grey_na_for_every_field():
    output = StringIO()
    table = _suite_score_table(load_results(FIXTURES / "results.failed.json"))

    Console(
        file=output,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        width=140,
    ).print(table)

    assert len(re.findall(r"\x1b\[90m *n/a *\x1b\[0m", output.getvalue())) == 8


def test_run_suppresses_neo4j_driver_notification_logs(tmp_path, monkeypatch, caplog):
    _project(
        tmp_path,
        {
            "suite.yml": """\
suite: quiet
competency:
  - id: value
    question: Does the graph return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
        },
    )

    class NoisyClient(FakeClient):
        def run_read_result(self, query, params, *, timeout_s=None):
            logging.getLogger("neo4j.notifications").warning(
                "Received notification from DBMS server: deprecated query"
            )
            return super().run_read_result(query, params, timeout_s=timeout_s)

    client = NoisyClient([QueryResult([{"value": 1}], ("value",), ())])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)
    caplog.set_level(logging.WARNING, logger="neo4j.notifications")

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert "Received notification from DBMS server" not in caplog.text
    assert "Checks: 1 | passed 1" in result.stdout


def test_run_shows_interactive_progress_for_each_selected_check(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "suite.yml": """\
suite: progress
competency:
  - id: first
    question: Does the first query return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
  - id: second
    question: Does the second query return a value?
    query: RETURN 2 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
        },
    )
    client = FakeClient(
        [
            QueryResult([{"value": 1}], ("value",), ()),
            QueryResult([{"value": 2}], ("value",), ()),
        ]
    )
    progress: dict[str, object] = {"updates": []}
    lifecycle = []

    class FakeProgressBar:
        label = ""
        bar_template = ""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, amount):
            progress["updates"].append((amount, self.label, self.bar_template))

    def progressbar(**kwargs):
        lifecycle.append("progress")
        progress["options"] = kwargs
        bar = FakeProgressBar()
        bar.label = kwargs["label"]
        bar.bar_template = kwargs["bar_template"]
        return bar

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)
    monkeypatch.setattr("graphcheck.cli._interactive_stderr", lambda: True)
    monkeypatch.setattr("graphcheck.cli.typer.progressbar", progressbar)
    real_print_target = cli_module._print_run_target

    def print_target(target):
        lifecycle.append("target")
        real_print_target(target)

    monkeypatch.setattr("graphcheck.cli._print_run_target", print_target)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert lifecycle[:2] == ["target", "progress"]
    assert progress["options"]["length"] == 2
    assert progress["options"]["label"] == "00:00"
    assert progress["options"]["show_eta"] is False
    assert progress["options"]["color"] is True
    assert "\x1b[32m" in progress["options"]["fill_char"]
    assert progress["updates"] == [
        (
            1,
            "00:00",
            "%(label)s  [%(bar)s]  %(info)s Complete | Checking: progress/first",
        ),
        (
            1,
            "00:00",
            "%(label)s  [%(bar)s]  %(info)s Complete | Checking: progress/second",
        ),
    ]


def test_run_elapsed_clock_uses_minutes_and_seconds(monkeypatch):
    monkeypatch.setattr("graphcheck.cli.time.monotonic", lambda: 185.9)

    assert cli_module._elapsed_clock(60) == "02:05"


def test_redacted_run_progress_hides_check_identity(monkeypatch):
    captured: dict[str, object] = {"updates": []}

    class FakeProgressBar:
        label = ""
        bar_template = ""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, amount):
            captured["updates"].append((amount, self.bar_template))

    def progressbar(**kwargs):
        captured["initial_template"] = kwargs["bar_template"]
        bar = FakeProgressBar()
        bar.label = kwargs["label"]
        bar.bar_template = kwargs["bar_template"]
        return bar

    monkeypatch.setattr("graphcheck.cli._interactive_stderr", lambda: True)
    monkeypatch.setattr("graphcheck.cli.typer.progressbar", progressbar)

    with cli_module._run_progress(1, redacted=True) as update:
        assert update is not None
        update(1, 1, "secret-suite/secret-check")

    rendered = f"{captured['initial_template']} {captured['updates']}"
    assert "Preparing redacted graph checks" in rendered
    assert "Checking: redacted check" in rendered
    assert "secret-suite" not in rendered and "secret-check" not in rendered


def test_run_progress_template_escapes_percent_signs():
    assert cli_module._progress_template("suite/check%name").endswith("suite/check%%name")


def test_run_progress_uses_continuous_line_glyphs():
    assert cli_module._PROGRESS_FILL_CHAR == "━"
    assert cli_module._PROGRESS_EMPTY_CHAR == "─"


def test_run_artifacts_serialize_yaml_temporal_and_binary_values_consistently(
    tmp_path, monkeypatch
):
    _project(
        tmp_path,
        {
            "dates.yml": """\
suite: dates
competency:
  - id: pinned-date
    question: Does the graph return the pinned date values?
    query: RETURN $as_of AS as_of, $observed_at AS observed_at, $payload AS payload
    params:
      as_of: 2026-01-01
      observed_at: 2026-01-01T12:30:00Z
      payload: !!binary /w==
    expect:
      equals:
        - as_of: 2026-01-01
          observed_at: 2026-01-01T12:30:00Z
          payload: !!binary /w==
"""
        },
    )
    client = FakeClient(
        [
            QueryResult(
                [
                    {
                        "as_of": date(2026, 1, 1),
                        "observed_at": datetime(2026, 1, 1, 12, 30, tzinfo=UTC),
                        "payload": b"\xff",
                    }
                ],
                ("as_of", "observed_at", "payload"),
                (),
            )
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    payload = _payload(tmp_path)
    assert payload["checks"][0]["params"] == {
        "as_of": "2026-01-01",
        "observed_at": "2026-01-01T12:30:00Z",
        "payload": "_w==",
    }
    assert payload["checks"][0]["expected"]["equals"] == [
        {
            "as_of": "2026-01-01",
            "observed_at": "2026-01-01T12:30:00Z",
            "payload": "_w==",
        }
    ]
    assert payload["checks"][0]["measured"]["equals"] is True
    report = (tmp_path / ".graphcheck" / "runs" / "latest" / "report.html").read_text(
        encoding="utf-8"
    )
    assert "2026-01-01" in report
    assert "2026-01-01T12:30:00Z" in report
    assert "_w==" in report


@pytest.mark.parametrize(
    ("severity", "expected_exit", "verdict"),
    [("error", 1, "fail"), ("warn", 2, "warn")],
)
def test_run_returns_ci_exit_code_for_findings(
    tmp_path, monkeypatch, severity, expected_exit, verdict
):
    _project(
        tmp_path,
        {
            "finding.yml": f"""\
suite: finding
competency:
  - id: should-be-empty
    severity: {severity}
    question: Is the result empty?
    query: RETURN 1 AS value
    expect: {{empty: true}}
"""
        },
    )
    client = FakeClient(
        [
            QueryResult(
                [
                    {
                        "value": 1,
                        "evidence": {
                            "kind": "node",
                            "id": "node-1",
                            "labels": ["Customer"],
                        },
                    }
                ],
                ("value", "evidence"),
                (),
            )
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == expected_exit
    assert f"Result: 1 {'failure' if verdict == 'fail' else 'warning'}." in result.stdout
    assert "Coverage:" not in result.stdout
    payload = _payload(tmp_path)
    assert payload["run"]["exit_code"] == expected_exit
    assert payload["checks"][0]["verdict"] == verdict


def test_run_maps_errored_checks_to_partial_in_cli(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "suite.yml": """\
suite: errored
competency:
  - id: broken-query
    question: Can the query execute?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        },
    )

    class ErroredClient(FakeClient):
        def run_read_result(self, query, params, *, timeout_s=None):
            raise GraphCheckError("neo4j.query_failed", "Query failed.", "Fix the query.")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: ErroredClient())

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 1
    assert ": partial" in result.stdout
    assert "Result: 1 execution error. Coverage is incomplete." in result.stdout
    assert "errored 1" in result.stdout
    assert (
        "The following check(s) have execution errors. Suggested fixes are provided:"
        in result.stdout
    )
    assert all(heading in result.stdout for heading in ("Suite", "Check", "Reason", "Fix"))
    normalized = " ".join(result.stdout.split())
    assert all(
        value in normalized
        for value in (
            "errored",
            "Can the query execute",
            "broken-query",
            "Fix the query.",
        )
    ), normalized
    assert not result.stderr

    table = _execution_error_table(load_results(_payload(tmp_path)))
    assert table.columns[2]._cells[0].plain == "neo4j.query_failed: Query failed."
    assert [str(column._cells[0].style) for column in table.columns] == [
        "italic white",
        "italic white",
        "magenta",
        "white",
    ]

    output = StringIO()
    Console(
        file=output,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        width=140,
    ).print(table)
    rendered = output.getvalue()
    assert "\x1b[3;37m" in rendered
    assert "\x1b[35m" in rendered
    assert "\x1b[37m" in rendered


def test_run_connection_failure_is_exit_three_and_still_writes_reports(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "suite.yml": """\
suite: connection
competency:
  - id: reachable
    question: Is the graph reachable?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        },
    )
    client = FakeClient(
        probe_error=GraphCheckError(
            "neo4j.unreachable",
            "Neo4j could not be reached.",
            "Start Neo4j and verify the configured URI.",
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    payload = _payload(tmp_path)
    assert payload["run"]["status"] == "failed"
    assert payload["run"]["error"]["code"] == "neo4j.unreachable"
    assert (tmp_path / ".graphcheck" / "runs" / "latest" / "report.html").exists()
    assert ": failed" in result.stdout
    assert "Score breakdown by check suite:" in result.stdout
    assert result.stdout.count("n/a") == 8
    assert "Checks: 0" not in result.stdout
    assert "Score: n/a" not in result.stdout
    assert "Result: Run failed before checks could complete." in result.stdout
    assert "Results and Report saved to:" in result.stdout
    assert result.stdout.index("Result:") < result.stdout.index("Results and Report saved to:")
    assert result.stdout.index("Results and Report saved to:") < result.stdout.index("Exit code: 3")
    assert "neo4j.unreachable: Neo4j could not be reached." in result.stderr
    assert "Fix: Start Neo4j" in result.stderr
    assert client.closed is True


def test_run_rejects_write_capable_credential_and_reports_visible_fix(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "suite.yml": """\
suite: audit
competency:
  - id: readable
    question: Is the graph readable?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        },
    )

    class WriteCapableClient(FakeClient):
        def verify_read_only_credential(self):
            raise GraphCheckError(
                "neo4j.credential_not_read_only",
                "The credential has WRITE.",
                "Use a dedicated read-only user.",
            )

    client = WriteCapableClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    assert _payload(tmp_path)["run"]["error"]["code"] == "neo4j.credential_not_read_only"
    report = (tmp_path / ".graphcheck" / "runs" / "latest" / "report.html").read_text(
        encoding="utf-8"
    )
    assert "Troubleshooting Steps" in report
    assert "The credential has WRITE." in report
    assert "Action required" not in report
    assert client.read_calls == []


def test_run_connection_failure_keeps_root_error_when_artifact_write_fails(tmp_path, monkeypatch):
    _project(tmp_path, {})
    client = FakeClient(
        probe_error=GraphCheckError(
            "neo4j.unreachable",
            "Neo4j could not be reached.",
            "Start Neo4j and verify the configured URI.",
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)
    monkeypatch.setattr(
        cli_module,
        "_write_run_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError(5, "Access is denied")),
    )

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    assert "neo4j.unreachable: Neo4j could not be reached." in result.stderr
    assert "Fix: Start Neo4j and verify the configured URI." in result.stderr
    assert "run.artifact_failed: Could not write run artifacts:" in result.stderr
    assert "Fix: Check the configured artifacts path and filesystem permissions." in result.stderr


def test_run_invalid_selector_is_configuration_failure(tmp_path, monkeypatch):
    _project(tmp_path, {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--select", "production"])

    assert result.exit_code == 3
    payload = _payload(tmp_path)
    assert payload["run"]["error"]["code"] == "run.invalid_selector"


def test_run_without_a_project_is_exit_three_with_a_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "find_project_root",
        lambda: (_ for _ in ()).throw(
            GraphCheckError("project.missing", "No graphcheck.yml found.", "Run graphcheck init.")
        ),
    )

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    assert "project.missing" in result.stderr
    assert "Fix:" in result.stderr
    assert not (tmp_path / ".graphcheck").exists()


def test_run_empty_suite_selection_is_a_nonpassing_exit_two(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "existing.yml": """\
suite: existing
competency:
  - id: value
    question: Does this return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        },
    )
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run", "--suite", "missing"])

    assert result.exit_code == 2
    payload = _payload(tmp_path)
    assert payload["run"]["selection"]["suites"] == ["missing"]
    assert payload["checks"] == []
    assert payload["score"] is None
    assert client.read_calls == []


def test_suite_discovery_is_recursive_sorted_and_filters_resolved_suite_ids(tmp_path):
    checks = tmp_path / "checks"
    (checks / "nested").mkdir(parents=True)
    suite = (
        "competency:\n"
        "  - id: value\n"
        "    question: Value?\n"
        "    query: RETURN 1 AS value\n"
        "    expect: {rows: {exactly: 1}}\n"
    )
    (checks / "z.yaml").write_text(suite, encoding="utf-8")
    (checks / "a.yml").write_text(f"suite: explicit\n{suite}", encoding="utf-8")
    (checks / "nested" / "b.YML").write_text(suite, encoding="utf-8")

    all_loaded = _load_suite_inputs(checks, [])
    selected = _load_suite_inputs(checks, ["explicit", "b"])

    assert [item.suite.suite for item in all_loaded] == ["explicit", "b", "z"]
    assert [item.suite.suite for item in selected] == ["explicit", "b"]


def test_suite_selection_validates_unselected_files(tmp_path):
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "selected.yml").write_text("suite: selected\n", encoding="utf-8")
    (checks / "broken.yml").write_text("suite: broken\nunknown_top_level: true\n", encoding="utf-8")

    with pytest.raises(GraphCheckError) as caught:
        _load_suite_inputs(checks, ["selected"])

    assert caught.value.error.code == "run.suite_invalid"


def test_suite_discovery_ignores_stale_manifest_and_never_writes_it(tmp_path):
    checks = tmp_path / "checks"
    checks.mkdir()
    manifest = checks / ".graphcheck-suite-manifest.json"
    stale = '{"schema_version": 1, "files": {"suite.yml": {"suite": "wrong"}}}\n'
    manifest.write_text(stale, encoding="utf-8")
    (checks / "suite.yml").write_text("suite: actual\n", encoding="utf-8")

    assert [item.suite.suite for item in _load_suite_inputs(checks, ["actual"])] == ["actual"]
    assert manifest.read_text(encoding="utf-8") == stale
    manifest.unlink()
    assert _load_suite_inputs(checks, ["missing"]) == []
    assert not manifest.exists()


def test_run_invalid_suite_is_configuration_failure(tmp_path, monkeypatch):
    _project(tmp_path, {"broken.yml": "suite: broken\nunknown: true\n"})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    payload = _payload(tmp_path)
    assert payload["run"]["error"]["code"] == "run.suite_invalid"


def test_graceful_failure_matrix_missing_apoc_names_fix_and_continues(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "apoc.yml": """\
suite: apoc-required
conformance:
  - id: customer-names
    check: completeness
    with: {label: Customer, property: name}
"""
        },
    )
    installed = builtin_pack_catalog()
    checks = dict(installed.checks)
    checks["completeness"] = replace(checks["completeness"], requires=("read", "apoc"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "graphcheck.engine.compiler.builtin_pack_catalog",
        lambda: PackCatalog(checks=checks, pii=installed.pii),
    )
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: FakeClient())

    result = runner.invoke(app, ["run"])

    payload = _payload(tmp_path)
    assert result.exit_code == payload["run"]["exit_code"] == 2
    assert payload["checks"][0]["verdict"] == "skipped"
    assert payload["checks"][0]["skip_reason"] == "unsupported"
    assert "Install APOC" in result.stdout
    assert "Install APOC" in _report(tmp_path)
    _assert_no_traceback(result)


def test_graceful_failure_matrix_empty_schema_is_actionable_error(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "schema.yml": """\
suite: expected-schema
conformance:
  - id: customer-names
    check: completeness
    with: {label: Customer, property: name}
"""
        },
    )
    summary = {
        "schema_ok": False,
        "missing_labels": ["Customer"],
        "missing_relationship_types": [],
        "missing_properties": [],
        "coverage": 1.0,
        "population": 0,
        "conforming_count": 0,
        "violation_count": 0,
        "evidence": [],
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "graphcheck.cli.Neo4jClient",
        lambda profile: FakeClient([QueryResult([summary], tuple(summary), (), observed_rows=1)]),
    )

    result = runner.invoke(app, ["run"])

    payload = _payload(tmp_path)
    assert result.exit_code == payload["run"]["exit_code"] == 1, (
        result.stdout,
        result.stderr,
        payload,
    )
    assert payload["checks"][0]["error"]["code"] == "engine.schema_reference_missing"
    table = _execution_error_table(load_results(payload))
    assert "nothing to evaluate" in table.columns[2]._cells[0].plain
    assert "Correct the label/type" in table.columns[3]._cells[0].plain
    assert "nothing to evaluate" in _report(tmp_path)
    _assert_no_traceback(result)


def test_graceful_failure_matrix_permission_denied_names_fix(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "read.yml": """\
suite: read-access
competency:
  - id: readable
    question: Can the configured user read?
    query: MATCH (n) RETURN n LIMIT 1
    expect: {rows: {exactly: 1}}
"""
        },
    )

    class PermissionDeniedClient(FakeClient):
        def run_read_result(self, query, params, *, timeout_s=None):
            raise GraphCheckError(
                "neo4j.permission_denied",
                "Neo4j denied a read query for the configured user.",
                "Grant read access to the configured user, then run `graphcheck debug`.",
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "graphcheck.cli.Neo4jClient",
        lambda profile: PermissionDeniedClient(counts=Counts(nodes=0, relationships=0)),
    )

    result = runner.invoke(app, ["run"])

    payload = _payload(tmp_path)
    assert result.exit_code == payload["run"]["exit_code"] == 1, (
        result.stdout,
        result.stderr,
        payload,
    )
    assert payload["checks"][0]["error"]["code"] == "neo4j.permission_denied"
    table = _execution_error_table(load_results(payload))
    assert "neo4j.permission_denied" in table.columns[2]._cells[0].plain
    assert "Grant read access" in table.columns[3]._cells[0].plain
    assert "Grant read access" in _report(tmp_path)
    assert "Nothing to evaluate" in result.stdout
    assert "no selected check produced a measured result" in result.stdout
    assert "Empty graph" not in result.stdout
    assert "Nothing to evaluate" in _report(tmp_path)
    assert "Empty graph" not in _report(tmp_path)
    _assert_no_traceback(result)


def test_graceful_failure_matrix_deadline_is_partial_exit_two(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "deadline.yml": """\
suite: deadline
competency:
  - id: finishes-late
    question: Does the check finish?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        },
    )

    class DeadlineEngine:
        def __init__(self, client, *args, **kwargs):
            self.client = client

        def run(self, suites, **kwargs):
            ticks = iter((0.0, 0.0, 0.0, 0.0, 1.1, 1.1))
            return CoreEngine(
                self.client,
                config=EngineConfig(time_budget_s=1.0),
                monotonic=lambda: next(ticks, 1.1),
            ).run(
                suites,
                target=TARGET,
                **kwargs,
            )

    class DeadlineClient(FakeClient):
        def run_read_result(self, query, params, *, timeout_s=None):
            self.read_calls.append((query, params, timeout_s))
            raise GraphCheckTimeoutError(
                "neo4j.query_failed",
                "Neo4j timed out the in-flight query.",
                "Narrow the check or increase the run time budget.",
            )

    monkeypatch.chdir(tmp_path)
    # The run command drives the engine through the shared application.run service, so the
    # deadline-limited engine must be substituted where execute_run binds it.
    monkeypatch.setattr("graphcheck.application.run.Engine", DeadlineEngine)
    monkeypatch.setattr(
        "graphcheck.cli.Neo4jClient",
        lambda profile: DeadlineClient(),
    )

    result = runner.invoke(app, ["run"])

    payload = _payload(tmp_path)
    assert result.exit_code == payload["run"]["exit_code"] == 2
    assert payload["run"]["status"] == "partial"
    assert payload["checks"][0]["verdict"] == "errored"
    assert payload["checks"][0]["error"]["code"] == "engine.timeout"
    assert "1-second run budget was exhausted" in payload["run"]["partial_reason"]
    assert "Fix: select fewer checks" in result.stdout
    assert "Partial reason" in _report(tmp_path)
    _assert_no_traceback(result)


def test_graceful_failure_matrix_empty_graph_is_trustworthy_clean(tmp_path, monkeypatch):
    _project(
        tmp_path,
        {
            "empty.yml": """\
suite: empty-graph
conformance:
  - id: customer-names
    check: completeness
    with: {label: Customer, property: name}
"""
        },
    )
    summary = {
        "schema_ok": False,
        "missing_labels": ["Customer"],
        "missing_relationship_types": [],
        "missing_properties": [],
        "coverage": 1.0,
        "population": 0,
        "conforming_count": 0,
        "violation_count": 0,
        "evidence": [],
    }
    client = FakeClient(
        [QueryResult([summary], tuple(summary), (), observed_rows=1)],
        counts=Counts(nodes=0, relationships=0),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)

    result = runner.invoke(app, ["run"])

    payload = _payload(tmp_path)
    assert result.exit_code == payload["run"]["exit_code"] == 0
    assert payload["checks"][0]["verdict"] == "pass"
    assert payload["checks"][0]["measured"]["population"] == 0
    assert "CALL db.labels()" in client.read_calls[0][0]
    assert client.read_calls[0][1]["required_labels"] == ["Customer"]
    assert "Empty graph" in result.stdout
    assert "load data if this was unexpected" in result.stdout
    assert "Empty graph" in _report(tmp_path)
    _assert_no_traceback(result)
