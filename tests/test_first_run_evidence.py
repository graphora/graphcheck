from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "first_run_evidence.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )


def _results(path: Path, *, checks: int = 2, skipped: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "run": {
                    "run_status": "complete",
                    "exit_code": 0,
                    "graphcheck_version": "0.2.0",
                    "target": {"server_version": "5.26.28"},
                },
                "totals": {"checks": checks, "skipped": skipped},
                "score": {"value": 100},
            }
        ),
        encoding="utf-8",
    )


def _record(tmp_path: Path, *, checks: int = 2, skipped: int = 0):
    results = tmp_path / "results.json"
    report = tmp_path / "report.html"
    output = tmp_path / "timing.json"
    _results(results, checks=checks, skipped=skipped)
    report.write_text("<html></html>", encoding="utf-8")
    completed = _run(
        "record",
        "--results",
        str(results),
        "--report",
        str(report),
        "--output",
        str(output),
        "--platform",
        "linux-container",
        "--trial",
        "1",
        "--run-exit-code",
        "0",
        "--started-at-ns",
        "1700000000000000000",
        "--finished-at-ns",
        "1700000010000000000",
        "--install-ns",
        "4000000000",
        "--fixture-ns",
        "1000000000",
        "--init-ns",
        "2000000000",
        "--run-ns",
        "3000000000",
        "--total-ns",
        "10000000000",
        "--python-version",
        "3.12.0",
        "--runner-os",
        "Linux",
        "--runner-image",
        "ubuntu-24.04-container",
        "--tested-github-sha",
        "merge123",
        "--pull-request-head-sha",
        "head123",
        "--github-event-name",
        "pull_request",
    )
    return completed, json.loads(output.read_text(encoding="utf-8"))


def test_record_accepts_a_scored_result_with_executed_checks(tmp_path):
    completed, evidence = _record(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert evidence["valid"] is True
    assert evidence["tested_github_sha"] == "merge123"
    assert evidence["pull_request_head_sha"] == "head123"
    assert "synthetic merge commit" in evidence["provenance_note"]
    assert evidence["executed_checks"] == 2
    assert evidence["score"] == 100
    assert evidence["timings_seconds"]["total"] == 10


def test_record_rejects_an_all_skipped_result_without_a_traceback(tmp_path):
    completed, evidence = _record(tmp_path, checks=2, skipped=2)

    assert completed.returncode == 1
    assert evidence["valid"] is False
    assert "at least one executed check" in completed.stderr
    assert "Fix:" in completed.stderr
    assert "Traceback" not in completed.stderr


def _sample(path: Path, platform: str, trial: int, seconds: float) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "timing.json").write_text(
        json.dumps(
            {
                "platform": platform,
                "trial": trial,
                "valid": True,
                "github_event_name": "pull_request",
                "tested_github_sha": "merge123",
                "pull_request_head_sha": "head123",
                "timings_seconds": {"total": seconds},
            }
        ),
        encoding="utf-8",
    )


def test_summarize_records_and_enforces_each_platform_median(tmp_path):
    samples, output = tmp_path / "samples", tmp_path / "summary"
    observations = {
        "linux-container": (100, 200, 300),
        "macos": (50, 60, 70),
        "wsl": (899, 899.5, 1000),
    }
    for platform, values in observations.items():
        for trial, seconds in enumerate(values, 1):
            _sample(samples / f"{platform}-{trial}", platform, trial, seconds)

    completed = _run("summarize", "--input", str(samples), "--output", str(output))
    summary = json.loads((output / "first-run-summary.json").read_text(encoding="utf-8"))

    assert completed.returncode == 0, completed.stdout
    assert summary["passed"] is True
    assert summary["tested_github_sha"] == "merge123"
    assert summary["pull_request_head_sha"] == "head123"
    assert summary["platforms"]["linux-container"]["median_seconds"] == 200
    assert summary["platforms"]["wsl"]["median_seconds"] == 899.5
    assert "Overall: **PASS**" in (output / "first-run-summary.md").read_text()


def test_summarize_fails_when_a_platform_median_reaches_the_budget(tmp_path):
    samples, output = tmp_path / "samples", tmp_path / "summary"
    for platform in ("linux-container", "macos", "wsl"):
        for trial, seconds in enumerate((899, 900, 901), 1):
            _sample(samples / f"{platform}-{trial}", platform, trial, seconds)

    completed = _run("summarize", "--input", str(samples), "--output", str(output))
    summary = json.loads((output / "first-run-summary.json").read_text(encoding="utf-8"))

    assert completed.returncode == 1
    assert summary["passed"] is False
    assert all(not result["under_budget"] for result in summary["platforms"].values())
    assert "is not under 900s" in completed.stdout


def test_first_run_script_records_evidence_before_profile_smoke_check():
    script = (SCRIPT.parent / "first-run.sh").read_text(encoding="utf-8")

    assert script.index('run_stage "first-result validation"') < script.index(
        'run_stage "graphcheck profile smoke check"'
    )
