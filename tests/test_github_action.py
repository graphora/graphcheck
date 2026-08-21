from __future__ import annotations

import runpy
import shutil
from pathlib import Path

ROOT = Path(__file__).parents[1]
ACTION_SCRIPT = ROOT / ".github" / "actions" / "graphcheck-action" / "write_summary.py"
RESULTS_FIXTURE = ROOT / "tests" / "contracts" / "fixtures" / "results.clean.json"


def test_github_action_summary_reads_schema_2_run_status(tmp_path, monkeypatch):
    artifacts = tmp_path / ".graphcheck"
    latest = artifacts / "runs" / "latest"
    latest.mkdir(parents=True)
    shutil.copyfile(RESULTS_FIXTURE, latest / "results.json")
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GRAPHCHECK_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    runpy.run_path(str(ACTION_SCRIPT), run_name="__main__")

    rendered = summary.read_text(encoding="utf-8")
    assert "**Run status:** `complete`" in rendered
    assert "unknown" not in rendered
