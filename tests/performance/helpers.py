from __future__ import annotations

import gc
import json
import math
import platform
import statistics
import subprocess
import time
import tracemalloc
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
PLAN_ARGUMENTS = frozenset(
    {
        "dbhits",
        "details",
        "estimatedrows",
        "identifiers",
        "pagecachehits",
        "pagecachemisses",
        "rows",
    }
)
REQUIRED_RECORD_FIELDS = frozenset(
    {
        "benchmark",
        "commit",
        "os",
        "architecture",
        "python",
        "driver",
        "server",
        "cypher",
        "samples",
        "median_ms",
        "p95_ms",
        "maximum_ms",
    }
)


@dataclass(frozen=True)
class BenchmarkRecord:
    benchmark: str
    commit: str
    os: str
    architecture: str
    python: str
    driver: str | None
    server: str | None
    cypher: str | None
    samples: int
    median_ms: float
    p95_ms: float
    maximum_ms: float
    details: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_samples(
        cls,
        benchmark: str,
        samples_ms: Iterable[float],
        *,
        server: str | None = None,
        cypher: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> BenchmarkRecord:
        samples = [max(0.0, float(sample)) for sample in samples_ms]
        if not samples:
            raise ValueError("at least one benchmark sample is required")
        metadata = environment_metadata()
        return cls(
            benchmark=benchmark,
            **metadata,
            server=server,
            cypher=cypher,
            samples=len(samples),
            median_ms=round(statistics.median(samples), 3),
            p95_ms=round(percentile(samples, 0.95), 3),
            maximum_ms=round(max(samples), 3),
            details=dict(details or {}),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "benchmark": self.benchmark,
            "commit": self.commit,
            "os": self.os,
            "architecture": self.architecture,
            "python": self.python,
            "driver": self.driver,
            "server": self.server,
            "cypher": self.cypher,
            "samples": self.samples,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
            "maximum_ms": self.maximum_ms,
            **({"details": self.details} if self.details else {}),
        }


@dataclass(frozen=True)
class QueryTiming:
    client_wall_ms: float
    server_available_after_ms: int | None
    server_consumed_after_ms: int | None


@dataclass(frozen=True)
class AllocationMeasurement:
    retained_bytes: int
    peak_bytes: int


class LazyHighCardinalityResult:
    """A result double that creates rows only as a consumer requests them."""

    def __init__(self, row_count: int, *, payload_bytes: int = 0) -> None:
        if row_count < 0 or payload_bytes < 0:
            raise ValueError("row_count and payload_bytes must be non-negative")
        self.row_count = row_count
        self.yielded = 0
        self.columns = ("index", "payload")
        self.notifications: tuple[dict[str, object], ...] = ()
        self.server_available_after_ms = 1
        self.server_consumed_after_ms = 2
        self.read_guard_ms = 0
        self._payload = "x" * payload_bytes
        self.rows = self._rows()

    def _rows(self) -> Iterator[dict[str, object]]:
        for index in range(self.row_count):
            self.yielded += 1
            yield {"index": index, "payload": self._payload}


def percentile(samples: Iterable[float], quantile: float) -> float:
    values = sorted(float(sample) for sample in samples)
    if not values:
        raise ValueError("at least one sample is required")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between zero and one")
    return values[max(0, math.ceil(quantile * len(values)) - 1)]


def environment_metadata() -> dict[str, object]:
    return {
        "commit": _commit(),
        "os": f"{platform.system()} {platform.release()}".strip(),
        "architecture": platform.machine() or "unknown",
        "python": platform.python_version(),
        "driver": _package_version("neo4j"),
    }


def cypher_version_for_server(server_version: str | None) -> str | None:
    """Report the default Cypher generation separately for current test server lines."""

    if not server_version:
        return None
    major = server_version.split(".", 1)[0]
    return "4.4" if major == "4" else "5" if major == "5" else None


def validate_record(record: Mapping[str, object]) -> None:
    missing = REQUIRED_RECORD_FIELDS - record.keys()
    if missing:
        raise ValueError(f"benchmark record is missing fields: {sorted(missing)}")
    for name in ("benchmark", "commit", "os", "architecture", "python"):
        if not isinstance(record[name], str) or not record[name]:
            raise ValueError(f"{name} must be a non-empty string")
    if isinstance(record["samples"], bool) or not isinstance(record["samples"], int):
        raise ValueError("samples must be an integer")
    if record["samples"] < 1:
        raise ValueError("samples must be positive")
    timings = [record[name] for name in ("median_ms", "p95_ms", "maximum_ms")]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in timings
    ):
        raise ValueError("timings must be finite non-negative milliseconds")
    if not timings[0] <= timings[1] <= timings[2]:
        raise ValueError("timings must be ordered median <= p95 <= maximum")
    for name in ("driver", "server", "cypher"):
        if record[name] is not None and not isinstance(record[name], str):
            raise ValueError(f"{name} must be a string or null")


def write_records(records: Iterable[BenchmarkRecord], path: Path) -> Path:
    payload = [record.as_dict() for record in records]
    for record in payload:
        validate_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def walk_plan(
    plan: object,
    *,
    selected_arguments: Iterable[str] = PLAN_ARGUMENTS,
) -> list[dict[str, object]]:
    """Flatten Neo4j dict/object plan trees without depending on one driver serialization."""

    selected = {name.replace("_", "").lower() for name in selected_arguments}
    operators: list[dict[str, object]] = []

    def visit(node: object) -> None:
        if node is None:
            return
        operator = _value(node, "operator_type", "operatorType", "name")
        arguments = _value(node, "arguments", "args") or {}
        chosen = {
            str(name): _json_value(value)
            for name, value in _mapping_items(arguments)
            if str(name).replace("_", "").lower() in selected
        }
        operators.append({"operator": _operator_name(operator), "arguments": chosen})
        children = _value(node, "children", "plans") or ()
        iterable = (
            children
            if isinstance(children, Iterable) and not isinstance(children, (str, bytes, Mapping))
            else ()
        )
        for child in iterable:
            visit(child)

    visit(plan)
    return operators


def _operator_name(value: object) -> str:
    return str(value or "unknown").partition("@")[0]


def measure_query(call: Callable[[], Any]) -> tuple[Any, QueryTiming]:
    started = time.perf_counter_ns()
    result = call()
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return result, QueryTiming(
        client_wall_ms=round(max(0.0, elapsed_ms), 3),
        server_available_after_ms=_optional_milliseconds(result, "server_available_after_ms"),
        server_consumed_after_ms=_optional_milliseconds(result, "server_consumed_after_ms"),
    )


def measure_allocations(call: Callable[[], Any]) -> tuple[Any, AllocationMeasurement]:
    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    try:
        result = call()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, AllocationMeasurement(
        retained_bytes=max(0, current - before),
        peak_bytes=max(0, peak - before),
    )


def _commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _value(node: object, *names: str) -> object | None:
    if isinstance(node, Mapping):
        for name in names:
            if name in node:
                return node[name]
        return None
    for name in names:
        if hasattr(node, name):
            return getattr(node, name)
    return None


def _mapping_items(value: object) -> Iterable[tuple[object, object]]:
    return value.items() if isinstance(value, Mapping) else ()


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _optional_milliseconds(value: object, name: str) -> int | None:
    timing = getattr(value, name, None)
    valid = isinstance(timing, int) and not isinstance(timing, bool) and timing >= 0
    return timing if valid else None
