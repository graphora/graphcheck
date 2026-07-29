from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.performance.gates import (
    assert_required_gates,
    evaluate_record,
    load_reference_budgets,
    write_gate_results,
)
from tests.performance.helpers import BenchmarkRecord, validate_record, write_records

pytestmark = pytest.mark.performance
WARMUPS = 1
SAMPLES = 10
BUDGETS = Path(__file__).with_name("budgets.json")
COMMANDS = {
    "cli-version-cold": (("--version",), 0),
    "cli-help-cold": (("--help",), 0),
    "cli-telemetry-status-cold": (("telemetry", "status"), 0),
    "cli-invalid-command-cold": (("not-a-real-command",), 2),
}


def test_cold_cli_commands_emit_machine_readable_baselines_and_enforce_named_budget(tmp_path):
    environment = os.environ.copy()
    environment["GRAPHCHECK_TELEMETRY_CONFIG"] = str(tmp_path / "telemetry.json")
    environment["GRAPHCHECK_TELEMETRY"] = "0"
    environment.pop("GRAPHCHECK_POSTHOG_API_KEY", None)
    records = [
        _measure_record(benchmark, arguments, expected_exit, environment)
        for benchmark, (arguments, expected_exit) in COMMANDS.items()
    ]
    reference = os.environ.get("GRAPHCHECK_PERFORMANCE_GATE")
    budgets = load_reference_budgets(BUDGETS, reference) if reference else {}
    results = [
        evaluate_record(record, budgets[record.benchmark], gate="cli-cold-start")
        for record in records
        if reference
    ]
    failed = {
        result["benchmark"]
        for result in results
        if result["mode"] == "required" and result["passed"] is not True
    }
    if failed:
        records = [
            (
                _measure_record(
                    record.benchmark,
                    COMMANDS[record.benchmark][0],
                    COMMANDS[record.benchmark][1],
                    environment,
                    prior_samples=record.details["samples_ms"],
                )
                if record.benchmark in failed
                else record
            )
            for record in records
        ]
        results = [
            evaluate_record(record, budgets[record.benchmark], gate="cli-cold-start")
            for record in records
        ]

    configured_output = os.environ.get("GRAPHCHECK_PERFORMANCE_OUTPUT")
    output = write_records(
        records, Path(configured_output) if configured_output else tmp_path / "cli-cold-start.json"
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert {record["benchmark"] for record in payload} == set(COMMANDS)
    for record in payload:
        validate_record(record)
        assert record["samples"] in {SAMPLES, SAMPLES * 2}
        assert record["details"]["warmups"] == WARMUPS * record["details"]["batches"]
        assert len(record["details"]["samples_ms"]) == record["samples"]

    if reference:
        write_gate_results(results, output.with_name("performance-gates.json"))
        assert_required_gates(results)


def _run(command: list[str], environment: dict[str, str]) -> int:
    return subprocess.run(
        command,
        cwd=None,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    ).returncode


def _measure_record(
    benchmark,
    arguments,
    expected_exit,
    environment,
    *,
    prior_samples=(),
):
    command = [
        sys.executable,
        "-c",
        "from graphcheck.bootstrap import cli; cli()",
        *arguments,
    ]
    for _ in range(WARMUPS):
        assert _run(command, environment) == expected_exit
    samples = [*prior_samples]
    for _ in range(SAMPLES):
        started = time.perf_counter_ns()
        exit_code = _run(command, environment)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        assert exit_code == expected_exit
    return BenchmarkRecord.from_samples(
        benchmark,
        samples,
        details={
            "command": ["graphcheck", *arguments],
            "warmups": WARMUPS * (len(samples) // SAMPLES),
            "warmups_per_batch": WARMUPS,
            "warmup_policy": "one fresh-process sample discarded before each measured batch",
            "process_policy": "every warm-up and measured sample uses a fresh process",
            "confirmation_policy": "a required-gate failure adds one confirmation batch",
            "batches": len(samples) // SAMPLES,
            "samples_ms": [round(sample, 3) for sample in samples],
        },
    )
