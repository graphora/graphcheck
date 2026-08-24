from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
ACTION = ROOT / ".github" / "actions" / "graphcheck-action"
FIXTURES = ROOT / "tests" / "contracts" / "fixtures"


def _module(name: str):
    path = ACTION / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_results(root: Path, payload: dict, artifacts: str = ".graphcheck") -> None:
    latest = root / artifacts / "runs" / "latest"
    latest.mkdir(parents=True)
    (latest / "results.json").write_text(json.dumps(payload), encoding="utf-8")


def _check(
    check_id: str,
    *,
    verdict: str,
    severity: str,
    message: str = "finding",
) -> dict:
    return {
        "id": check_id,
        "suite_id": "suite",
        "name": check_id,
        "verdict": verdict,
        "severity": severity,
        "evidence": (
            {
                "message": message,
                "elements": [{"kind": "node", "id": f"node-{check_id}", "labels": ["Node"]}],
                "truncated": False,
                "cap": 50,
            }
            if verdict in {"fail", "warn"}
            else None
        ),
        "error": (
            {"code": "engine.query_failed", "message": message, "fix": "Fix the query."}
            if verdict == "errored"
            else None
        ),
    }


def test_summary_emits_additive_error_and_warning_annotations_with_yaml_locations(
    tmp_path, monkeypatch, capsys
):
    payload = json.loads((FIXTURES / "results.complete.json").read_text(encoding="utf-8"))
    _write_results(tmp_path, payload)
    (tmp_path / "graphcheck.yml").write_text("checks: checks\n", encoding="utf-8")
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "customer.yml").write_text(
        "suite: customer-360\n"
        "competency:\n"
        "  - id: cq-001\n"
        "    question: Which accounts?\n"
        "    query: RETURN 1\n"
        "    expect: {rows: {exactly: 1}}\n"
        "conformance:\n"
        "  - id: account-no-orphans\n"
        "    check: no_orphans\n"
        "    with: {label: Account}\n",
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    _module("write_summary").main()

    output = capsys.readouterr().out
    assert "::error file=checks/customer.yml,line=3" in output
    assert "title=GraphCheck%3A customer-360/cq-001" in output
    assert "Evidence: node 4:abc:12 (Account)" in output
    assert "::warning file=checks/customer.yml,line=8" in output
    rendered_summary = summary.read_text(encoding="utf-8")
    assert "## GraphCheck results" in rendered_summary
    assert "### Failing / warning / errored checks" in rendered_summary
    assert "account-no-orphans" in rendered_summary


def test_errored_check_gets_check_level_annotation_and_workflow_values_are_escaped(
    tmp_path, monkeypatch, capsys
):
    payload = {"run": {}, "totals": {}, "suites": [], "score": None, "checks": []}
    payload["checks"] = [
        _check(
            "broken,check",
            verdict="errored",
            severity="error",
            message="bad % query\n::warning::injected",
        )
    ]
    _write_results(tmp_path, payload)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    _module("write_summary").main()

    output = capsys.readouterr().out
    assert "::error title=GraphCheck%3A suite/broken%2Ccheck::" in output
    assert "bad %25 query%0A::warning::injected" in output
    assert "Fix: Fix the query." in output
    assert output.count("::warning ") == 0
    assert "annotations were still processed" in output


def test_warn_severity_errored_check_emits_warning(tmp_path, monkeypatch, capsys):
    payload = {
        "run": {},
        "totals": {},
        "suites": [],
        "score": None,
        "checks": [_check("soft-error", verdict="errored", severity="warn")],
    }
    _write_results(tmp_path, payload)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    _module("write_summary").main()

    output = capsys.readouterr().out
    assert "::warning title=GraphCheck%3A suite/soft-error::" in output
    assert "::error " not in output


def test_explicit_evidence_location_is_preferred(tmp_path, monkeypatch, capsys):
    check = _check("located", verdict="fail", severity="error")
    check["evidence"]["location"] = {
        "path": "checks/source.yml",
        "start_line": 12,
        "start_column": 4,
    }
    payload = {"run": {}, "totals": {}, "suites": [], "score": None, "checks": [check]}
    _write_results(tmp_path, payload)
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "source.yml").write_text("# source\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    _module("write_summary").main()

    assert "::error file=checks/source.yml,line=12,col=4" in capsys.readouterr().out


def test_annotations_respect_per_level_limits_and_report_drops(tmp_path, monkeypatch, capsys):
    checks = [
        *[_check(f"error-{index}", verdict="fail", severity="error") for index in range(12)],
        *[_check(f"warn-{index}", verdict="warn", severity="warn") for index in range(13)],
    ]
    payload = {"run": {}, "totals": {}, "suites": [], "score": None, "checks": checks}
    _write_results(tmp_path, payload)
    summary = tmp_path / "summary.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    _module("write_summary").main()

    output = capsys.readouterr().out
    assert output.count("::error ") == 10
    assert output.count("::warning ") == 10
    truncation = "dropped 5 (2 errors, 3 warnings)"
    assert truncation in output
    assert truncation in summary.read_text(encoding="utf-8")


def test_prepare_action_resolves_artifacts_and_generates_profile(tmp_path, monkeypatch):
    environment, output = tmp_path / "github-env", tmp_path / "github-output"
    (tmp_path / "graphcheck.yml").write_text(
        "project: graphcheck\nchecks: checks\nartifacts: build/graphcheck\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "GC_PROFILE": "ci",
        "GC_URI": "bolt://localhost:7687",
        "GC_USER": "neo4j",
        "GC_DATABASE": "neo4j",
        "GC_CONCURRENCY": "4",
        "GC_UPLOAD_ARTIFACTS": "on-failure",
        "GITHUB_ENV": str(environment),
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(name, value)

    assert _module("prepare_action").main() == 0

    assert environment.read_text(encoding="utf-8") == (
        "GRAPHCHECK_ARTIFACTS_DIR=build/graphcheck\n"
    )
    assert output.read_text(encoding="utf-8") == "generated_profiles=true\n"
    profile = yaml.safe_load((tmp_path / "profiles.yml").read_text(encoding="utf-8"))
    assert profile["profiles"]["ci"] == {
        "uri": "bolt://localhost:7687",
        "user": "neo4j",
        "password": None,
        "password_env": "NEO4J_PASSWORD",
        "database": "neo4j",
    }


def test_prepare_action_rejects_invalid_performance_inputs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("GC_CONCURRENCY", "0")
    monkeypatch.setenv("GC_UPLOAD_ARTIFACTS", "sometimes")

    assert _module("prepare_action").main() == 1
    assert "concurrency must be a positive integer" in capsys.readouterr().err


def test_prepare_action_rejects_invalid_artifact_upload_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("GC_CONCURRENCY", "")
    monkeypatch.setenv("GC_UPLOAD_ARTIFACTS", "sometimes")

    assert _module("prepare_action").main() == 1
    assert "upload-artifacts must be one of" in capsys.readouterr().err


def test_action_uses_cached_wheel_install_and_exposes_performance_inputs():
    text = (ACTION / "action.yml").read_text(encoding="utf-8")

    assert "uses: astral-sh/setup-uv@v5" in text
    assert "enable-cache: true" in text
    assert "prune-cache: false" in text
    assert 'uv pip install --python "$venv" --only-binary :all:' in text
    assert "GC_CONCURRENCY: ${{ inputs.concurrency }}" in text
    assert 'args+=(--concurrency "$GC_CONCURRENCY")' in text
    assert "inputs.upload-artifacts != 'never'" in text
    assert "inputs.upload-artifacts == 'always'" in text
    assert "prepare_action.py" in text
    assert "generate_profile.py" not in text
