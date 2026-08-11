import json
import logging
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from graphcheck import cli as cli_module
from graphcheck.cli import _load_suite_inputs, _write_run_artifacts, app
from graphcheck.connection_profiles import write_default_profiles
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.engine import Engine as CoreEngine
from graphcheck.engine import EngineConfig
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Counts, QueryResult
from graphcheck.packs.catalog import PackCatalog, builtin_pack_catalog
from graphcheck.project import write_default_project
from graphcheck.reporting.history import discover_report_runs
from graphcheck.reporting.writer import json_compatible, load_results

runner = CliRunner()
FIXTURES = Path(__file__).parent / "contracts" / "fixtures"
TARGET = RunTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:test-graph",
    capabilities=Capabilities(apoc=False, count_store=True),
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


def test_artifact_writer_keeps_previous_latest_pair_when_refresh_fails(tmp_path, monkeypatch):
    first = load_results(FIXTURES / "results.complete.json")
    second = load_results(FIXTURES / "results.complete.json")
    second.run.finished_at = "2026-07-06T09:03:41Z"
    runs_dir = tmp_path / "runs"
    latest_results, latest_report = _write_run_artifacts(first, runs_dir)
    previous_results = latest_results.read_bytes()
    previous_report = latest_report.read_bytes()
    real_publish = cli_module._publish_run_directory

    def fail_latest_refresh(artifacts, directory):
        if directory.name == "latest":
            raise OSError("simulated latest report failure")
        return real_publish(artifacts, directory)

    monkeypatch.setattr(cli_module, "_publish_run_directory", fail_latest_refresh)

    with pytest.raises(OSError, match="simulated latest report failure"):
        _write_run_artifacts(second, runs_dir)

    assert latest_results.read_bytes() == previous_results
    assert latest_report.read_bytes() == previous_report
    assert (runs_dir / "neo4j_20260706T090341000000Z" / "results.json").is_file()
    assert not list(runs_dir.glob(".*.staging-*"))
    assert not list(runs_dir.glob(".*.backup-*"))


def test_artifact_writer_renders_once_for_history_and_latest(tmp_path, monkeypatch):
    results = load_results(FIXTURES / "results.complete.json")
    calls = 0
    real_render = cli_module.render_run_artifacts

    def render_once(value):
        nonlocal calls
        calls += 1
        return real_render(value)

    monkeypatch.setattr(cli_module, "render_run_artifacts", render_once)

    _write_run_artifacts(results, tmp_path / "runs")

    assert calls == 1


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
    assert "Checks: 1 | passed 1" in result.stdout
    assert "exit code: 0" in result.stdout
    assert "Suite selected:" not in result.stdout
    assert len(client.read_calls) == 1
    assert client.closed is True


def test_run_prints_each_suite_summary_without_multi_suite_aggregate(tmp_path, monkeypatch):
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
    assert (
        "Suite alpha: score 100 | checks 1 | passed 1 | failed 0 | warnings 0 | "
        "errored 0 | skipped 0"
    ) in result.stdout
    assert (
        "Suite beta: score 0 | checks 1 | passed 0 | failed 0 | warnings 1 | errored 0 | skipped 0"
    ) in result.stdout
    assert "Overall:" not in result.stdout
    assert "Checks: 2" not in result.stdout
    assert "Score: 75" not in result.stdout
    assert "Exit code: 2" in result.stdout
    payload = _payload(tmp_path)
    assert [(suite["id"], suite["score"]) for suite in payload["suites"]] == [
        ("alpha", 100),
        ("beta", 0),
    ]


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
        progress["options"] = kwargs
        bar = FakeProgressBar()
        bar.label = kwargs["label"]
        bar.bar_template = kwargs["bar_template"]
        return bar

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)
    monkeypatch.setattr("graphcheck.cli._interactive_stderr", lambda: True)
    monkeypatch.setattr("graphcheck.cli.typer.progressbar", progressbar)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
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


def test_run_progress_template_escapes_percent_signs():
    assert cli_module._progress_template("suite/check%name").endswith("suite/check%%name")


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
    assert "errored 1" in result.stdout


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
    assert "neo4j.unreachable: Neo4j could not be reached." in result.stderr
    assert "Fix: Start Neo4j" in result.stderr
    assert client.closed is True


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
    assert "nothing to evaluate" in result.stderr
    assert "Fix: Correct the label/type" in result.stderr
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
    assert "neo4j.permission_denied" in result.stderr
    assert "Fix: Grant read access" in result.stderr
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
            ticks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 1.1, 1.1))
            return CoreEngine(
                self.client,
                config=EngineConfig(time_budget_s=1.0),
                monotonic=lambda: next(ticks, 1.1),
            ).run(
                suites,
                target=TARGET,
                **kwargs,
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.engine.Engine", DeadlineEngine)
    monkeypatch.setattr(
        "graphcheck.cli.Neo4jClient",
        lambda profile: FakeClient([QueryResult([{"value": 1}], ("value",), ())]),
    )

    result = runner.invoke(app, ["run"])

    payload = _payload(tmp_path)
    assert result.exit_code == payload["run"]["exit_code"] == 2
    assert payload["run"]["status"] == "partial"
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
