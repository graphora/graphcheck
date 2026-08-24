#!/usr/bin/env python3
"""Record and aggregate clean-environment first-run evidence using only the stdlib."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = "1"
DEFAULT_PLATFORMS = ("linux-container", "macos", "wsl")


def _seconds(nanoseconds: int) -> float:
    return round(nanoseconds / 1_000_000_000, 3)


def _timestamp(nanoseconds: int) -> str:
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, UTC).isoformat()


def _read_results(path: Path, errors: list[str]) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"results.json could not be read: {type(exc).__name__}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append("results.json must contain a JSON object")
        return None
    return payload


def _result_facts(
    payload: dict[str, object] | None, run_exit_code: int, errors: list[str]
) -> tuple[int | None, int | float | None, str | None, str | None]:
    if payload is None:
        return None, None, None, None
    run = payload.get("run")
    totals = payload.get("totals")
    score = payload.get("score")
    if not isinstance(run, dict):
        errors.append("results.json is missing the run object")
        run = {}
    if not isinstance(totals, dict):
        errors.append("results.json is missing the totals object")
        totals = {}
    selected, skipped = totals.get("checks"), totals.get("skipped")
    executed = (
        selected - skipped
        if isinstance(selected, int)
        and not isinstance(selected, bool)
        and isinstance(skipped, int)
        and not isinstance(skipped, bool)
        else None
    )
    if executed is None or executed < 1:
        errors.append("results.json must contain at least one executed check")
    if run.get("status") != "complete":
        errors.append("results.json run.status must be complete")
    if run_exit_code not in {0, 1, 2}:
        errors.append("a complete scored run must exit with code 0, 1, or 2")
    if run.get("exit_code") != run_exit_code:
        errors.append("the command exit code does not match results.json")
    score_value = score.get("value") if isinstance(score, dict) else None
    if (
        isinstance(score_value, bool)
        or not isinstance(score_value, (int, float))
        or not math.isfinite(score_value)
    ):
        errors.append("results.json must contain a finite numeric score")
        score_value = None
    elif not 0 <= score_value <= 100:
        errors.append("results.json score must be between 0 and 100")
    target = run.get("target")
    return (
        executed,
        score_value,
        run.get("graphcheck_version") if isinstance(run.get("graphcheck_version"), str) else None,
        (
            target.get("server_version")
            if isinstance(target, dict) and isinstance(target.get("server_version"), str)
            else None
        ),
    )


def record(args: argparse.Namespace) -> int:
    errors: list[str] = []
    if args.trial < 1:
        errors.append("trial must be a positive integer")
    if args.budget_seconds < 1:
        errors.append("budget_seconds must be a positive integer")
    if args.finished_at_ns < args.started_at_ns:
        errors.append("finished_at must not precede started_at")
    for name in ("install", "fixture", "init", "run", "total"):
        if getattr(args, f"{name}_ns") < 0:
            errors.append(f"{name} timing must be non-negative")
    results_path, report_path, output_path = map(Path, (args.results, args.report, args.output))
    payload = _read_results(results_path, errors)
    if not report_path.is_file() or report_path.stat().st_size < 1:
        errors.append("report.html is missing or empty")
    executed, score, graphcheck_version, neo4j_version = _result_facts(
        payload, args.run_exit_code, errors
    )
    timings = {
        name: _seconds(getattr(args, f"{name}_ns"))
        for name in ("install", "fixture", "init", "run", "total")
    }
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "platform": args.platform,
        "trial": args.trial,
        "commit_sha": os.environ.get("GITHUB_SHA"),
        "runner_os": args.runner_os,
        "runner_image": args.runner_image,
        "python_version": args.python_version,
        "graphcheck_version": graphcheck_version,
        "neo4j_version": neo4j_version,
        "timing_boundary": "clean-wheel-install-start_to_valid-init-run-result",
        "database_ready_before_install": False,
        "profile_smoke_after_timing": True,
        "started_at": _timestamp(args.started_at_ns),
        "finished_at": _timestamp(args.finished_at_ns),
        "timings_seconds": timings,
        "budget_seconds": args.budget_seconds,
        "within_individual_budget": timings["total"] < args.budget_seconds,
        "run_exit_code": args.run_exit_code,
        "executed_checks": executed,
        "score": score,
        "valid": not errors,
        "validation_errors": errors,
        "artifacts": {"results": str(results_path), "report": str(report_path)},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"First-run evidence invalid: {error}", file=sys.stderr)
        print(
            f"Fix: inspect the command logs and {output_path}, correct the failure, and retry.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Valid first result: {executed} executed check(s), score {score}, "
        f"install-to-result {timings['total']:.3f}s."
    )
    return 0


def prepare_fixture(args: argparse.Namespace) -> int:
    from neo4j import GraphDatabase

    deadline, last_error = time.monotonic() + args.timeout_seconds, None
    while time.monotonic() < deadline:
        try:
            with GraphDatabase.driver(
                args.uri,
                auth=(args.user, args.password),
                connection_timeout=1,
            ) as driver:
                driver.verify_connectivity()
                driver.execute_query(
                    "MERGE (:Customer {name: 'Ada'})",
                    database_=args.database,
                )
            print(f"Prepared first-run fixture at {args.uri}/{args.database}.")
            return 0
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    print(
        "First-run fixture setup failed: Neo4j did not become ready "
        f"within {args.timeout_seconds} seconds ({type(last_error).__name__}: {last_error}).\n"
        "Fix: verify that Neo4j is running, Bolt is reachable, and the configured credentials "
        "are correct.",
        file=sys.stderr,
    )
    return 1


def _load_samples(root: Path) -> tuple[dict[tuple[str, int], dict[str, object]], list[str]]:
    samples: dict[tuple[str, int], dict[str, object]] = {}
    errors: list[str] = []
    for path in sorted(root.rglob("timing.json")):
        try:
            sample = json.loads(path.read_text(encoding="utf-8"))
            key = (str(sample["platform"]), int(sample["trial"]))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{path}: unreadable timing evidence ({type(exc).__name__}: {exc})")
            continue
        if key in samples:
            errors.append(f"duplicate timing evidence for {key[0]} trial {key[1]}")
        else:
            samples[key] = sample
    return samples, errors


def summarize(args: argparse.Namespace) -> int:
    root, output_dir = Path(args.input), Path(args.output)
    samples, errors = _load_samples(root)
    commit_shas = sorted(
        {
            value
            for sample in samples.values()
            if isinstance((value := sample.get("commit_sha")), str) and value
        }
    )
    if len(commit_shas) > 1:
        errors.append("timing evidence contains more than one commit SHA")
    platforms: dict[str, dict[str, object]] = {}
    for platform_name in args.platforms:
        observations: list[dict[str, object]] = []
        for trial in range(1, args.trials + 1):
            sample = samples.get((platform_name, trial))
            if sample is None:
                errors.append(f"missing timing evidence for {platform_name} trial {trial}")
                continue
            timings = sample.get("timings_seconds")
            total = timings.get("total") if isinstance(timings, dict) else None
            if sample.get("valid") is not True:
                errors.append(f"{platform_name} trial {trial} did not produce a valid result")
            if (
                isinstance(total, bool)
                or not isinstance(total, (int, float))
                or not math.isfinite(total)
                or total < 0
            ):
                errors.append(f"{platform_name} trial {trial} has invalid total timing")
                continue
            observations.append({"trial": trial, "total_seconds": total})
        median_seconds = (
            round(statistics.median(item["total_seconds"] for item in observations), 3)
            if len(observations) == args.trials
            else None
        )
        under_budget = median_seconds is not None and median_seconds < args.budget_seconds
        if median_seconds is not None and not under_budget:
            errors.append(
                f"{platform_name} median {median_seconds:.3f}s is not under {args.budget_seconds}s"
            )
        platforms[platform_name] = {
            "samples": observations,
            "median_seconds": median_seconds,
            "under_budget": under_budget,
        }
    passed = not errors
    summary = {
        "schema_version": SCHEMA_VERSION,
        "metric": "clean-wheel-install-start_to_valid-init-run-result",
        "commit_sha": commit_shas[0] if len(commit_shas) == 1 else None,
        "budget_seconds": args.budget_seconds,
        "trials_per_platform": args.trials,
        "platforms": platforms,
        "passed": passed,
        "errors": errors,
    }
    lines = ["# GraphCheck first-run evidence", ""]
    if len(commit_shas) == 1:
        lines.extend((f"Commit: `{commit_shas[0]}`", ""))
    lines.extend(
        (
            "Budget: median install-to-first-valid-result "
            f"**under {args.budget_seconds // 60} minutes**.",
            "",
            "| Platform | Samples (seconds) | Median (seconds) | Result |",
            "| --- | ---: | ---: | --- |",
        )
    )
    for name, result in platforms.items():
        sample_text = (
            ", ".join(str(item["total_seconds"]) for item in result["samples"]) or "missing"
        )
        median_text = (
            str(result["median_seconds"]) if result["median_seconds"] is not None else "n/a"
        )
        lines.append(
            f"| {name} | {sample_text} | {median_text} | "
            f"{'PASS' if result['under_budget'] else 'FAIL'} |"
        )
    if errors:
        lines.extend(("", "## Errors", "", *(f"- {error}" for error in errors)))
    lines.extend(("", f"Overall: **{'PASS' if passed else 'FAIL'}**", ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "first-run-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "first-run-summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if passed else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    record_parser = commands.add_parser("record")
    for name in (
        "results",
        "report",
        "output",
        "platform",
        "python-version",
        "runner-os",
        "runner-image",
    ):
        record_parser.add_argument(f"--{name}", required=True)
    for name in (
        "started-at-ns",
        "finished-at-ns",
        "install-ns",
        "fixture-ns",
        "init-ns",
        "run-ns",
        "total-ns",
    ):
        record_parser.add_argument(f"--{name}", type=int, required=True)
    record_parser.add_argument("--trial", type=int, required=True)
    record_parser.add_argument("--run-exit-code", type=int, required=True)
    record_parser.add_argument("--budget-seconds", type=int, default=900)
    record_parser.set_defaults(handler=record)
    fixture_parser = commands.add_parser("prepare-fixture")
    fixture_parser.add_argument("--uri", default="bolt://localhost:7687")
    fixture_parser.add_argument("--user", default="neo4j")
    fixture_parser.add_argument("--password", default="graphora")
    fixture_parser.add_argument("--database", default="neo4j")
    fixture_parser.add_argument("--timeout-seconds", type=int, default=120)
    fixture_parser.set_defaults(handler=prepare_fixture)
    summary_parser = commands.add_parser("summarize")
    summary_parser.add_argument("--input", required=True)
    summary_parser.add_argument("--output", required=True)
    summary_parser.add_argument("--platforms", nargs="+", default=list(DEFAULT_PLATFORMS))
    summary_parser.add_argument("--trials", type=int, default=3)
    summary_parser.add_argument("--budget-seconds", type=int, default=900)
    summary_parser.set_defaults(handler=summarize)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        return args.handler(args)
    except Exception as exc:
        print(
            f"First-run evidence tooling failed: {type(exc).__name__}: {exc}\n"
            "Fix: verify the evidence arguments and files, then retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
