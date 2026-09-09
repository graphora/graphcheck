import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from graphcheck.application.artifacts import write_run_artifacts
from graphcheck.cli import app
from graphcheck.contracts.profile import BaselineProfile, profile_fingerprint
from graphcheck.contracts.results import (
    CheckResult,
    Results,
    Totals,
    exit_code,
    score_value,
    totals,
)
from graphcheck.project import write_default_project
from graphcheck.reporting.changes import compare_evidence, load_changes
from graphcheck.reporting.history import ReportRun, report_summary_json
from graphcheck.reporting.redaction import redact_results
from graphcheck.reporting.writer import load_results, results_json

FIXTURES = Path(__file__).parents[1] / "contracts" / "fixtures"
runner = CliRunner()


def _check(check_id, verdict="pass", elements=(), *, cap=100, total=None):
    check = load_results(FIXTURES / "results.complete.json").checks[1].model_dump()
    check.update(
        id=check_id,
        name=check_id,
        verdict=verdict,
        severity="warn" if verdict == "warn" else "error",
    )
    if verdict in {"warn", "fail"}:
        check["evidence"] = {
            "message": "finding",
            "elements": [
                {"kind": kind, "id": element_id}
                for kind, element_id in (elements or [("node", check_id)])
            ],
            "cap": cap,
            "total_count": total if total is not None else max(1, len(elements)),
            "truncated": total is not None and total > len(elements),
        }
    return CheckResult.model_validate(check)


def _run(root, run_id, day, checks, *, baseline=None, previous=None, legacy=False):
    model = load_results(FIXTURES / "results.complete.json")
    model.run.id = run_id
    model.run.started_at = model.run.finished_at = f"2026-09-{day:02}T00:00:00Z"
    model.run.baseline_ref, model.run.previous_run_id = baseline, previous
    model.checks = checks
    model.totals = Totals.model_validate(totals(checks))
    model.run.exit_code = exit_code(model.run.run_status, checks)
    model.score.value = score_value(checks)
    model.suites[0].score = model.score.value
    model.suites[0].totals = model.totals
    model = Results.model_validate(model.model_dump(by_alias=True))
    directory = root / ".graphcheck" / "runs" / run_id
    directory.mkdir(parents=True)
    path = directory / "results.json"
    payload = json.loads(results_json(model))
    if legacy:
        for key in ("previous_run_id", "baseline_ref", "config_hash"):
            payload["run"].pop(key)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ReportRun(directory, path, directory / "report.html", load_results(path))


def _baseline(root, name, extra_nodes=0):
    profile = BaselineProfile.model_validate_json((FIXTURES / "baseline.json").read_text())
    profile.statistics.node_count += extra_nodes
    profile.fingerprint = profile_fingerprint(profile.graph_schema, profile.statistics)
    directory = root / ".graphcheck" / "baselines"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(profile.model_dump_json(by_alias=True), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    write_default_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_changes_joins_outcomes_profile_and_orphan_evidence_deterministically(project):
    _baseline(project, "a.json")
    _baseline(project, "b.json", 1)
    _run(project, "before", 1, [_check("new-orphan"), _check("fixed", "fail")], baseline="a.json")
    _run(
        project,
        "after",
        2,
        [_check("new-orphan", "fail", [("node", "orphan-id")]), _check("fixed")],
        baseline="b.json",
        previous="before",
    )
    command = ["changes", "--json"]
    first, second = runner.invoke(app, command), runner.invoke(app, command)
    assert first.exit_code == second.exit_code == 1
    assert first.stdout_bytes == second.stdout_bytes
    payload = json.loads(first.stdout)
    assert payload["outcomes"]["regressions"][0]["check_id"] == "new-orphan"
    assert payload["outcomes"]["improvements"][0]["check_id"] == "fixed"
    assert payload["profile"]["statistics"]["node_count"]["delta"] == 1
    evidence = {item["check_id"]: item for item in payload["evidence"]}
    assert evidence["new-orphan"]["appeared"] == [{"kind": "node", "id": "orphan-id"}]
    assert evidence["fixed"]["disappeared"] == [{"kind": "node", "id": "fixed"}]
    text = runner.invoke(app, ["changes"])
    assert text.exit_code == 1
    for expected in ("new-orphan: pass -> fail", "fixed: fail -> pass", "(+1,", "+ node orphan-id"):
        assert expected in text.stdout
    assert runner.invoke(app, ["changes", "--since", "previous", "--json"]).stdout == first.stdout


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("pass", "pass", 0),
        ("pass", "fail", 1),
        ("fail", "fail", 0),
        ("fail", "pass", 0),
        ("pass", "warn", 1),
        ("warn", "fail", 1),
    ],
)
def test_exit_codes_measure_regressions_not_existing_findings(project, old, new, code):
    _run(project, "before", 1, [_check("check", old)])
    _run(project, "after", 2, [_check("check", new)], previous="before")
    result = runner.invoke(app, ["changes", "--json"])
    assert result.exit_code == code, result.output
    assert json.loads(result.stdout)["profile_unavailable"]


def test_added_failure_is_a_regression_and_removed_check_is_listed(project):
    _run(project, "before", 1, [_check("removed")])
    _run(project, "after", 2, [_check("added", "fail")], previous="before")
    result = runner.invoke(app, ["changes", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["outcomes"]["removed"][0]["check_id"] == "removed"


def test_previous_follows_link_and_explicit_since_overrides_it(project):
    _run(project, "first", 1, [_check("c")])
    _run(project, "middle", 2, [_check("c")])
    _run(project, "last", 3, [_check("c")], previous="first")
    assert load_changes(project / ".graphcheck/runs").first.id == "first"
    assert load_changes(project / ".graphcheck/runs", "middle").first.id == "middle"
    history = runner.invoke(app, ["report", "--history"])
    assert history.exit_code == 0 and "PREVIOUS RUN ID" in history.stdout
    assert "first" in next(line for line in history.stdout.splitlines() if line.startswith("last"))


def test_legacy_runs_fall_back_to_recency(project):
    _run(project, "first", 1, [_check("c")], legacy=True)
    _run(project, "last", 2, [_check("c")], legacy=True)
    assert load_changes(project / ".graphcheck/runs").first.id == "first"


@pytest.mark.parametrize("since", ["previous", "missing", "only"])
def test_missing_previous_or_invalid_since_is_actionable_json(project, since):
    _run(project, "only", 1, [_check("c")])
    result = runner.invoke(app, ["changes", "--since", since, "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "changes.unavailable"


@pytest.mark.parametrize("cap", [1, 50, 100])
def test_evidence_bounds_total_directions_and_counts_dropped(project, cap):
    before = _run(
        project,
        "first",
        1,
        [_check("c", "fail", [("node", f"old-{i:03}") for i in range(cap)], cap=cap, total=265214)],
    )
    after = _run(
        project,
        "last",
        2,
        [
            _check(
                "c",
                "fail",
                [("node", f"new-{i:03}") for i in reversed(range(cap))],
                cap=cap,
                total=265215,
            )
        ],
    )
    (delta,) = compare_evidence(before, after)
    assert len(delta.appeared) + len(delta.disappeared) == cap
    assert delta.dropped == cap
    assert delta.before_truncated and delta.after_truncated
    assert delta.appeared[0]["id"] == "new-000"


def test_evidence_identity_includes_kind_and_ignores_aggregate_pointers(project):
    before = _run(project, "first", 1, [_check("c", "fail", [("node", "same")], cap=1)])
    after = _run(
        project,
        "last",
        2,
        [_check("c", "fail", [("rel", "same"), ("rel", "same"), ("aggregate", "count")], cap=50)],
    )
    (delta,) = compare_evidence(before, after)
    assert delta.appeared == [{"kind": "rel", "id": "same"}]
    assert delta.cap == delta.dropped == 1


def test_masking_clears_lineage_and_never_compares_masked_ids(project):
    first = _run(project, "first", 1, [_check("c", "fail")], baseline="secret.json")
    second = _run(project, "last", 2, [_check("c", "fail")], previous="first")
    masked = redact_results(second.results)
    assert masked.run.previous_run_id is masked.run.baseline_ref is masked.run.config_hash is None
    record = ReportRun(second.directory, second.results_path, second.report_path, masked)
    assert compare_evidence(first, record) == []


def test_publication_chain_and_compact_summary_are_consistent(project):
    runs = project / ".graphcheck/runs"
    first = load_results(FIXTURES / "results.complete.json")
    second = load_results(FIXTURES / "results.complete.json")
    second.run.finished_at = "2026-09-09T00:00:00Z"
    write_run_artifacts(first, runs)
    write_run_artifacts(second, runs)
    assert second.run.previous_run_id == first.run.id
    assert load_results(runs / "latest/results.json").run.previous_run_id == first.run.id
    assert json.loads(report_summary_json(second))["previous_run_id"] == first.run.id
    for name in ("results.json", "summary.json", "report.html"):
        assert (runs / "latest" / name).read_bytes() == (runs / second.run.id / name).read_bytes()
    assert load_changes(runs).first.id == first.run.id


@pytest.mark.parametrize("reference", ["../outside.json", "missing.json", "invalid.json"])
def test_bad_profile_reference_is_an_error_without_traceback(project, reference):
    _baseline(project, "a.json")
    (project / ".graphcheck/baselines/invalid.json").write_text("{}", encoding="utf-8")
    _run(project, "first", 1, [_check("c")], baseline="a.json")
    _run(project, "last", 2, [_check("c")], baseline=reference, previous="first")
    result = runner.invoke(app, ["changes", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "changes.unavailable"
    assert "Traceback" not in result.output


def test_partial_profile_keeps_outcome_and_evidence_comparison(project):
    _baseline(project, "a.json")
    path = project / ".graphcheck/baselines/a.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(status="partial", partial_reason="profile budget exceeded")
    path.write_text(json.dumps(payload), encoding="utf-8")
    _run(project, "first", 1, [_check("c")], baseline="a.json")
    _run(project, "last", 2, [_check("c", "fail")], baseline="a.json", previous="first")
    result = runner.invoke(app, ["changes", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["profile"] is None and "partial" in payload["profile_unavailable"]
    assert payload["evidence"][0]["appeared"] == [{"kind": "node", "id": "c"}]


@pytest.mark.parametrize("change_profile", [False, True])
def test_changes_rejects_mismatched_database_identity(project, change_profile):
    _baseline(project, "a.json")
    first = _run(project, "first", 1, [_check("c")], baseline="a.json")
    _run(project, "last", 2, [_check("c")], baseline="a.json", previous="first")
    path = project / ".graphcheck/baselines/a.json" if change_profile else first.results_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    (payload if change_profile else payload["run"])["target"]["database"] = "other"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert runner.invoke(app, ["changes", "--json"]).exit_code == 2


def test_latest_publication_is_used_even_when_another_run_finished_later(project):
    import shutil

    _run(project, "first", 1, [_check("c")])
    published = _run(project, "published", 2, [_check("c")], previous="first")
    _run(project, "unpublished", 3, [_check("c", "fail")], previous="published")
    shutil.copytree(published.directory, published.directory.parent / "latest")
    report = load_changes(project / ".graphcheck/runs")
    assert report.first.id == "first" and report.second.id == "published"
    assert report.exit_code == 0


def test_empty_history_reports_error_and_new_link_does_not_fall_back_after_prune(project):
    assert runner.invoke(app, ["changes", "--json"]).exit_code == 2
    _run(project, "first", 1, [_check("c")])
    _run(project, "last", 2, [_check("c")], previous="pruned")
    assert runner.invoke(app, ["changes", "--json"]).exit_code == 2
    assert runner.invoke(app, ["changes", "--since", "first", "--json"]).exit_code == 0
