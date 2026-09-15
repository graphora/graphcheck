"""Measure all core/PII checks on a preloaded graph with the default two workers.

The parent watchdog bounds the benchmark, independently of Neo4j transaction timeouts.
This is an engine benchmark: graph loading and CLI report rendering are excluded.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.engine.runner import Engine, EngineConfig, SuiteInput
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.packs.catalog import builtin_pack_catalog

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path(__file__).with_name("graph_size_suite.yml")
WATCHDOG_SECONDS = 330


def peak_rss_bytes(pid: int | None = None) -> int:
    if sys.platform != "win32":
        if pid is not None:
            raise ValueError("--server-pid currently requires a local Windows server.")
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    handle = kernel.GetCurrentProcess() if pid is None else kernel.OpenProcess(0x410, False, pid)
    try:
        if not handle or not psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.peak if pid is None else counters.working)
    finally:
        if pid is not None and handle:
            kernel.CloseHandle(handle)


def checked_suite() -> SuiteInput:
    suite = SuiteInput.from_yaml(SUITE.read_text(encoding="utf-8"))
    required = {
        name for name, item in builtin_pack_catalog().checks.items() if item.pack in {"core", "pii"}
    }
    actual = [check.spec.check for check in suite.suite.checks]
    if set(actual) != required or len(actual) != len(required):
        raise ValueError("Benchmark must contain every core and PII check exactly once.")
    for check in suite.suite.checks:
        if check.spec.check != "dangling_rels":
            CypherCompiler().compile(check, sample_seed=0)
    return suite


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class EventLog:
    def __init__(self, path: Path):
        self.path = path

    def emit(self, event) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {**event.model_dump(mode="json"), "client_peak_rss_bytes": peak_rss_bytes()}
                )
                + "\n"
            )


class ServerMemory:
    def __init__(self, pid: int | None):
        self.pid = pid
        self.samples: list[int] = []
        self.errors: list[str] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.collect, daemon=True)

    def collect(self) -> None:
        if self.pid is None:
            return
        while True:
            try:
                self.samples.append(peak_rss_bytes(self.pid))
            except (OSError, ValueError) as exc:
                self.errors.append(type(exc).__name__)
                return
            if self.stop.wait(0.1):
                return


def measure(args) -> dict:
    suite = checked_suite()
    config = EngineConfig(enforce_size_limit=not args.allow_unsupported_size)
    profile = ConnectionProfile(
        uri=os.environ["GRAPHCHECK_PERFORMANCE_URI"],
        user=os.environ.get("GRAPHCHECK_PERFORMANCE_USER", "graphcheck"),
        password=os.environ["GRAPHCHECK_PERFORMANCE_PASSWORD"],
        database=args.database,
    )
    client = Neo4jClient(profile, max_concurrency=config.max_concurrency)
    try:
        counts = client.run_read(
            "CALL { MATCH (n) RETURN count(n) AS nodes } "
            "MATCH ()-[r]->() RETURN nodes, count(r) AS relationships",
            timeout_s=30,
        )[0]
        if counts != {"nodes": args.nodes, "relationships": args.nodes * 95 // 100}:
            raise ValueError(f"Fixture count mismatch: {counts}")
        memory = ServerMemory(args.server_pid)
        memory.thread.start()
        try:
            started = time.perf_counter()
            results = Engine(client, config=config, event_sink=EventLog(args.events)).run([suite])
            wall_ms = (time.perf_counter() - started) * 1000
        finally:
            memory.stop.set()
            memory.thread.join(timeout=1)
        payload = results.model_dump(mode="json")
        checks = payload["checks"]
        events = [json.loads(line) for line in args.events.read_text().splitlines()]
        timeout_sequences = {
            event["check_sequence"]
            for event in events
            if event.get("outcome") == "timeout" and event.get("check_sequence") is not None
        }
        return {
            "execution_status": "measured",
            "counts": counts,
            "engine_wall_ms": round(wall_ms, 3),
            "client_peak_rss_bytes": peak_rss_bytes(),
            "server_peak_rss_bytes": max(memory.samples, default=None),
            "server_rss_samples_bytes": memory.samples,
            "server_memory_errors": memory.errors,
            "server_memory_scope": "Whole Neo4j process working set sampled every 100ms during run",
            "memory_scope": "OS process-lifetime peak RSS of Python worker; excludes Neo4j",
            "sampled_checks": [check["id"] for check in checks if check["estimate"] is not False],
            "timeout_check_sequences": sorted(timeout_sequences),
            "timed_out_checks": [
                check["id"]
                for check in checks
                if check["error"] and check["error"]["code"] == "engine.timeout"
            ],
            "skipped_checks": [
                {"id": check["id"], "reason": check["skip_reason"], "expected": check["expected"]}
                for check in checks
                if check["verdict"] == "skipped"
            ],
            "results": payload,
        }
    finally:
        client.close()


def main() -> int:
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-pid", type=int, help="Local Windows Neo4j PID for RSS sampling")
    parser.add_argument(
        "--allow-unsupported-size",
        action="store_true",
        help="Explicitly bypass the size guard to measure a stress workload",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.events = args.output.with_suffix(".events.jsonl")
    if args.worker:
        try:
            measurement = measure(args)
        except Exception as exc:
            measurement = {
                "execution_status": "error",
                "engine_wall_ms": None,
                "client_peak_rss_bytes": peak_rss_bytes(),
                "diagnostic": {
                    "code": "benchmark.failed",
                    "message": type(exc).__name__,
                    "fix": "Check the fixture, connection and reader permissions.",
                },
            }
            if hasattr(exc, "error"):
                measurement["diagnostic"] = exc.error.model_dump(mode="json")
        write_json(args.output, measurement)
        return 0 if measurement["execution_status"] == "measured" else 3

    checked_suite()
    if args.output.exists() or args.events.exists():
        parser.error("Choose a new output path; benchmark evidence is never overwritten.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    config = EngineConfig()
    metadata = {
        "schema_version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "suite_sha256": hashlib.sha256(SUITE.read_bytes()).hexdigest(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "neo4j_driver": version("neo4j"),
        "engine_source_sha256": {
            name: hashlib.sha256((ROOT / "src" / "graphcheck" / name).read_bytes()).hexdigest()
            for name in (
                "engine/runner.py",
                "engine/limits.py",
                "engine/core_pack.py",
                "engine/pii_pack.py",
                "neo4j_adapter.py",
            )
        },
        "requested_nodes": args.nodes,
        "database": args.database,
        "concurrency": config.max_concurrency,
        "engine_budget_seconds": config.time_budget_s,
        "watchdog_seconds": WATCHDOG_SECONDS,
        "size_limit_enforced": not args.allow_unsupported_size,
        "sampling": {
            "exhaustive_limit": config.sampling.exhaustive_limit,
            "sample_size": config.sampling.sample_size,
            "seed": config.sampling.seed,
        },
    }
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"],
            timeout=WATCHDOG_SECONDS,
            check=False,
        )
        measurement = (
            json.loads(args.output.read_text(encoding="utf-8"))
            if args.output.exists()
            else {
                "execution_status": "worker_failed",
                "engine_wall_ms": None,
                "client_peak_rss_bytes": None,
                "results": None,
                "diagnostic": {
                    "code": "benchmark.worker_failed",
                    "message": "Worker exited without a measurement artifact.",
                    "fix": "Inspect process and Neo4j health, then rerun.",
                },
            }
        )
        metadata["worker_exit_code"] = completed.returncode
    except subprocess.TimeoutExpired:
        measurement = {
            "execution_status": "watchdog_timeout",
            "engine_wall_ms": None,
            "client_peak_rss_bytes": None,
            "results": None,
            "diagnostic": {
                "code": "benchmark.watchdog_timeout",
                "message": f"Worker exceeded {WATCHDOG_SECONDS} seconds and was killed.",
                "fix": "Inspect the event log and Neo4j health; reduce the workload.",
            },
        }
    metadata["process_wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    write_json(args.output, {**metadata, **measurement})
    if diagnostic := measurement.get("diagnostic"):
        print(f"{diagnostic['code']}: {diagnostic['message']}\nFix: {diagnostic['fix']}")
    print(args.output)
    return 0 if measurement["execution_status"] == "measured" else 3


if __name__ == "__main__":
    raise SystemExit(main())
