from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from graphcheck.engine.compiler import CypherCompiler
from tools.benchmark_graph_size import ROOT, checked_suite, peak_rss_bytes
from tools.generate_graph_size import generate


def test_full_pack_suite_keeps_real_sampling_defaults_and_temporal_fields():
    checks = {check.id: check for check in checked_suite().suite.checks}
    assert len(checks) == 14
    for name in ("hub_outlier", "pii_name_match", "pii_value_match"):
        assert checks[name].spec.with_["sample_size"] is None
        assert CypherCompiler().compile(checks[name]).params["sample_size"] == 1000
    assert checks["temporal_sanity"].spec.with_["end_property"] == "settled_at"
    assert "dangling_rels" in checks


def test_worker_failure_is_recorded_without_fabricated_timings_or_secrets(tmp_path):
    output = tmp_path / "failed.json"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GRAPHCHECK_PERFORMANCE_")
    }
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "benchmark_graph_size.py"),
            "--nodes",
            "100000",
            "--database",
            "unconfigured",
            "--output",
            str(output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(output.read_text())
    assert result.returncode == 3
    assert payload["execution_status"] == "error"
    assert payload["engine_wall_ms"] is None
    assert "Fix:" in result.stdout
    assert payload["diagnostic"]["message"] == "KeyError"


def test_generator_rejects_nonempty_database_without_writing():
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query):
            assert query == "MATCH (n) RETURN count(n) AS count"
            return self

        def single(self):
            return {"count": 1}

    class Driver:
        def session(self, **kwargs):
            return Session()

    with pytest.raises(ValueError, match="existing data is never deleted"):
        generate(Driver(), "graphcheck-benchmark-test", 100_000)


def test_client_memory_uses_os_measurement():
    assert peak_rss_bytes() > 0


@pytest.mark.parametrize("timeout", [False, True])
def test_supervisor_preserves_failure_artifact_if_worker_does_not_finish(
    tmp_path, monkeypatch, timeout
):
    from tools import benchmark_graph_size as rig

    output = tmp_path / "supervised.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_graph_size.py",
            "--nodes",
            "100000",
            "--database",
            "test",
            "--output",
            str(output),
        ],
    )

    def run_worker(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        return subprocess.CompletedProcess(args[0], 7)

    monkeypatch.setattr(rig.subprocess, "check_output", lambda *args, **kwargs: "commit")
    monkeypatch.setattr(rig.subprocess, "run", run_worker)
    assert rig.main() == 3
    payload = json.loads(output.read_text())
    assert payload["execution_status"] == ("watchdog_timeout" if timeout else "worker_failed")
    assert payload["engine_wall_ms"] is None
    assert payload["results"] is None
    assert payload["diagnostic"]["fix"]


def test_published_measurements_contain_every_selected_check():
    paths = list((ROOT / "tools" / "graph-size-benchmark-results").glob("*-run*.json"))
    assert paths
    for path in paths:
        record = json.loads(path.read_text())
        if record["execution_status"] != "measured":
            continue
        assert record["concurrency"] == 2
        assert len(record["results"]["checks"]) == 14, Path(path).name
        assert record["counts"]["nodes"] == record["requested_nodes"]
        for check in record["results"]["checks"]:
            assert (check["duration_ms"] is None) == (check["verdict"] == "skipped")
