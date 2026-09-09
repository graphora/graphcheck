from pathlib import Path

import pytest
import yaml

from graphcheck.application.run import RunRequest, execute_run
from graphcheck.baselines import write_baseline
from graphcheck.connection_profiles import write_default_profiles
from graphcheck.contracts.profile import BaselineProfile
from graphcheck.contracts.results import RunStatus
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.errors import GraphCheckError
from graphcheck.project import write_default_project
from graphcheck.reporting.writer import load_results

FIXTURES = Path(__file__).parents[1] / "contracts" / "fixtures"


def test_runs_pin_existing_profiles_and_hash_effective_config_without_profiling(
    tmp_path, monkeypatch
):
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        Engine, "run", lambda *args, **kwargs: load_results(FIXTURES / "results.clean.json")
    )
    profile = BaselineProfile.model_validate_json((FIXTURES / "baseline.json").read_text())
    saved = write_baseline(profile, tmp_path)
    request = RunRequest(profile=None, suite_ids=[], tags=[], fail_fast=False)
    outputs = []

    def capture(results, runs_dir, **kwargs):
        outputs.append(results)
        return runs_dir / "results.json", runs_dir / "report.html"

    def run():
        return execute_run(request, client_factory=lambda *args: object(), artifact_writer=capture)

    first, second = run(), run()
    assert first.results.run.baseline_ref == second.results.run.baseline_ref == saved.name
    assert first.results.run.config_hash == second.results.run.config_hash
    newer = write_baseline(profile, tmp_path)
    request.concurrency = 4
    third = run()
    assert third.results.run.baseline_ref == newer.name
    assert first.results.run.baseline_ref == saved.name
    assert third.results.run.config_hash != first.results.run.config_hash
    assert third.results.run.config_hash.startswith("sha256:")
    profiles_path = tmp_path / "profiles.yml"
    profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    profiles["profiles"]["local"]["password"] = "another-test-password"
    profiles_path.write_text(yaml.safe_dump(profiles), encoding="utf-8")
    assert run().results.run.config_hash == third.results.run.config_hash
    profiles["profiles"]["local"]["uri"] = "bolt://another-server:7687"
    profiles_path.write_text(yaml.safe_dump(profiles), encoding="utf-8")
    assert run().results.run.config_hash != third.results.run.config_hash


def test_failed_run_still_writes_lineage_and_configuration_hash(tmp_path, monkeypatch):
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fail(*args):
        raise GraphCheckError("run.setup", "setup failed", "Fix setup.")

    outcome = execute_run(RunRequest(None, [], [], False), client_factory=fail)
    assert outcome.artifact_error is None
    results = load_results(outcome.results_path)
    assert results.run.run_status is RunStatus.FAILED
    assert results.run.previous_run_id is results.run.baseline_ref is None
    assert results.run.config_hash.startswith("sha256:")


def test_engine_config_hash_changes_with_selected_suite_contents():
    target = load_results(FIXTURES / "results.clean.json").run.target
    source = (
        "suite: generated\ncompetency:\n- id: c\n  question: q\n  query: RETURN 1\n"
        "  generated: true\n  expect: {rows: {exactly: 1}}\n"
    )
    engine = Engine(object(), config=EngineConfig(max_concurrency=1))
    first = engine.run_yaml(source, target=target)
    second = engine.run_yaml(source, target=target)
    changed = engine.run_yaml(source.replace("RETURN 1", "RETURN 2"), target=target)
    assert first.run.config_hash == second.run.config_hash
    assert first.run.config_hash != changed.run.config_hash


@pytest.mark.parametrize("invalid", [False, True])
def test_profile_loading_failure_still_publishes_lineage(tmp_path, monkeypatch, invalid):
    write_default_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    if invalid:
        (tmp_path / "profiles.yml").write_text("profiles: [", encoding="utf-8")
    outcome = execute_run(RunRequest(None, [], [], False))
    assert outcome.artifact_error is None
    results = load_results(outcome.results_path)
    assert results.run.run_status is RunStatus.FAILED
    assert results.run.previous_run_id is results.run.baseline_ref is None
    assert results.run.config_hash.startswith("sha256:")
