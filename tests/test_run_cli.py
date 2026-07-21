import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.connection_profiles import write_default_profiles
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import QueryResult
from graphcheck.project import write_default_project
from graphcheck.reporting.writer import json_compatible

runner = CliRunner()
TARGET = RunTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:test-graph",
    capabilities=Capabilities(apoc=False, count_store=True),
)


class FakeClient:
    def __init__(self, results=(), *, probe_error=None):
        self.results = list(results)
        self.probe_error = probe_error
        self.read_calls = []
        self.closed = False

    def probe(self, *, timeout_s=None):
        if self.probe_error is not None:
            raise self.probe_error
        return TARGET

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


def test_artifact_value_normalization_is_deterministic_for_yaml_sets():
    assert json_compatible({"values": {"gamma", "alpha", "beta"}}) == {
        "values": ["alpha", "beta", "gamma"]
    }


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
    assert [check["id"] for check in payload["checks"]] == ["production"]
    report = tmp_path / ".graphcheck" / "runs" / "latest" / "report.html"
    html = report.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "http://" not in html and "https://" not in html
    assert html.count("<script>") == 1
    assert "function filterChecks()" in html
    assert ' src="' not in html and ' href="' not in html
    assert "Checks: 1 | passed 1" in result.stdout
    assert "exit code: 0" in result.stdout
    assert len(client.read_calls) == 1
    assert client.closed is True


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

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, amount):
            progress["updates"].append((amount, self.label))

    def progressbar(**kwargs):
        progress["options"] = kwargs
        bar = FakeProgressBar()
        bar.label = kwargs["label"]
        return bar

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda profile: client)
    monkeypatch.setattr("graphcheck.cli._interactive_stderr", lambda: True)
    monkeypatch.setattr("graphcheck.cli.typer.progressbar", progressbar)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert progress["options"]["length"] == 2
    assert progress["options"]["label"] == "Running graph checks"
    assert progress["updates"] == [
        (1, "Completed progress/first"),
        (1, "Checks complete"),
    ]


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
    assert "Fix: Start Neo4j" in result.stderr
    assert client.closed is True


def test_run_invalid_selector_is_configuration_failure(tmp_path, monkeypatch):
    _project(tmp_path, {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--select", "production"])

    assert result.exit_code == 3
    payload = _payload(tmp_path)
    assert payload["run"]["error"]["code"] == "run.invalid_selector"


def test_run_without_a_project_is_exit_three_with_a_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

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


def test_run_invalid_suite_is_configuration_failure(tmp_path, monkeypatch):
    _project(tmp_path, {"broken.yml": "suite: broken\nunknown: true\n"})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    payload = _payload(tmp_path)
    assert payload["run"]["error"]["code"] == "run.suite_invalid"
