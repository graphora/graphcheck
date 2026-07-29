from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from tests.performance.helpers import BenchmarkRecord, validate_record, write_records

pytestmark = pytest.mark.performance
WARMUPS = 1
SAMPLES = 10
COMMANDS = {
    "cli-version-cold": (("--version",), 0),
    "cli-help-cold": (("--help",), 0),
    "cli-telemetry-status-cold": (("telemetry", "status"), 0),
    "cli-invalid-command-cold": (("not-a-real-command",), 2),
}


def test_cold_cli_commands_emit_machine_readable_baselines(tmp_path):
    environment = os.environ.copy()
    environment["GRAPHCHECK_TELEMETRY_CONFIG"] = str(tmp_path / "telemetry.json")
    environment["GRAPHCHECK_TELEMETRY"] = "0"
    environment.pop("GRAPHCHECK_POSTHOG_API_KEY", None)
    records = []

    for benchmark, (arguments, expected_exit) in COMMANDS.items():
        command = [
            sys.executable,
            "-c",
            "from graphcheck.cli import cli; cli()",
            *arguments,
        ]
        for _ in range(WARMUPS):
            assert _run(command, environment) == expected_exit
        samples = []
        for _ in range(SAMPLES):
            started = time.perf_counter_ns()
            exit_code = _run(command, environment)
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
            assert exit_code == expected_exit
        records.append(
            BenchmarkRecord.from_samples(
                benchmark,
                samples,
                details={
                    "command": ["graphcheck", *arguments],
                    "warmups": WARMUPS,
                    "warmup_policy": "one fresh-process sample discarded before measured runs",
                    "process_policy": "every warm-up and measured sample uses a fresh process",
                },
            )
        )

    output = write_records(records, tmp_path / "cli-cold-start.json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert {record["benchmark"] for record in payload} == set(COMMANDS)
    for record in payload:
        validate_record(record)
        assert record["samples"] == SAMPLES
        assert record["details"]["warmups"] == WARMUPS


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
