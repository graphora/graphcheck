from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import yaml
from neo4j import GraphDatabase

from graphcheck.connection_profiles import ConnectionProfile, ProfilesFile
from graphcheck.project import PROFILES_FILE, write_default_project

pytestmark = [
    pytest.mark.hostile,
    pytest.mark.skipif(
        os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
        reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run hostile Neo4j tests",
    ),
]

_HOSTILE = Path(__file__).with_name("hostile")
_SCALE_URL = "https://snap.stanford.edu/data/email-EuAll.txt.gz"
_SCALE_SHA256 = "c256f8be57084fe7b2dbe96f99d4d79e56c19228773526058abc99a6fa86e9d9"
_SCALE_NODES = 265_214
_SCALE_RELATIONSHIPS = 420_045
_NEO4J_44_PASSWORD = "graphcheck-hostile-44"


def _project(root: Path, profile: ConnectionProfile, suite: str) -> None:
    write_default_project(root)
    profiles = ProfilesFile(default="hostile", profiles={"hostile": profile})
    (root / PROFILES_FILE).write_text(
        yaml.safe_dump(profiles.model_dump(), sort_keys=False), encoding="utf-8"
    )
    shutil.copyfile(_HOSTILE / suite, root / "checks" / suite)


def _cli(root: Path, *arguments: str, timeout: int = 150) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GRAPHCHECK_TELEMETRY": "0", "NO_COLOR": "1", "COLUMNS": "120"}
    return subprocess.run(
        [sys.executable, "-c", "from graphcheck.bootstrap import cli; cli()", *arguments],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _assert_safe(result: subprocess.CompletedProcess[str], expected_exit: int) -> None:
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == expected_exit, output
    assert "Traceback (most recent call last)" not in output
    assert not (result.returncode and "Fix:" not in output and "Suggested fix" not in output), (
        output
    )


def _matrix(
    root: Path,
    suite_id: str,
    *,
    expected: tuple[int, int, int],
    timeout: int = 150,
) -> dict[str, subprocess.CompletedProcess[str]]:
    results = {
        "debug": _cli(root, "debug", timeout=timeout),
        "profile": _cli(root, "profile", "--json", timeout=timeout),
        "run": _cli(root, "run", "--suite", suite_id, timeout=timeout),
    }
    for result, exit_code in zip(results.values(), expected, strict=True):
        _assert_safe(result, exit_code)
    assert (root / ".graphcheck" / "runs" / "latest" / "results.json").is_file()
    return results


def _run_payload(root: Path) -> dict[str, object]:
    return json.loads(
        (root / ".graphcheck" / "runs" / "latest" / "results.json").read_text(encoding="utf-8")
    )


@contextmanager
def _seeded_graph(profile: ConnectionProfile, cypher: str | None = None) -> Iterator[None]:
    with GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver:
        with driver.session(database=profile.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            if cypher is not None:
                session.run(cypher).consume()
        try:
            yield
        finally:
            with driver.session(database=profile.database) as session:
                session.run("MATCH (n) DETACH DELETE n").consume()


def test_empty_graph_cli_matrix_is_graceful(neo4j_profile, tmp_path):
    _project(tmp_path, neo4j_profile, "empty.yml")
    with _seeded_graph(neo4j_profile):
        results = _matrix(tmp_path, "hostile-empty", expected=(0, 0, 0))

    assert "Counts: 0 nodes, 0 relationships" in results["debug"].stdout
    profile = json.loads(results["profile"].stdout)
    assert profile["statistics"] == {
        "node_count": 0,
        "relationship_count": 0,
        "property_coverage": [],
    }
    assert "Empty graph:" in results["run"].stdout


def test_llm_kg_builder_cli_matrix_handles_noisy_schema(neo4j_profile, tmp_path):
    _project(tmp_path, neo4j_profile, "llm-kg-builder.yml")
    cypher = (_HOSTILE / "llm-kg-builder.cypher").read_text(encoding="utf-8")
    with _seeded_graph(neo4j_profile, cypher):
        results = _matrix(tmp_path, "hostile-llm-kg-builder", expected=(0, 0, 0))

    profile = json.loads(results["profile"].stdout)
    labels = {item["name"] for item in profile["schema"]["labels"]}
    relationship_types = {item["name"] for item in profile["schema"]["relationship_types"]}
    assert {"__Entity__", "Country / Region", "Odd`Label"} <= labels
    assert {"HAS_ENTITY", "WORKED-WITH", "points to"} <= relationship_types
    assert _run_payload(tmp_path)["run"]["status"] == "complete"


def test_apoc_less_cli_matrix_is_actionable_and_isolated(neo4j_profile, tmp_path):
    _project(tmp_path, neo4j_profile, "apoc-less.yml")
    with _seeded_graph(neo4j_profile):
        results = _matrix(tmp_path, "hostile-apoc-less", expected=(0, 0, 1))

    payload = _run_payload(tmp_path)
    checks = payload["checks"]
    assert "APOC: no" in results["debug"].stdout
    assert [check["verdict"] for check in checks] == ["errored", "pass"]
    assert checks[0]["error"]["code"] == "neo4j.query_failed"
    assert checks[0]["error"]["fix"]


def _free_ports(count: int) -> list[int]:
    sockets = [socket.socket() for _ in range(count)]
    try:
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in sockets]
    finally:
        for listener in sockets:
            listener.close()


@pytest.fixture(scope="session")
def neo4j_44_cluster_profile():
    if os.environ.get("GRAPHCHECK_HOSTILE_NEO4J44") != "1":
        pytest.skip("set GRAPHCHECK_HOSTILE_NEO4J44=1 to run the Neo4j 4.4 cluster case")
    if shutil.which("docker") is None:
        pytest.skip("Docker with Compose is required for the Neo4j 4.4 cluster case")
    compose = _HOSTILE / "neo4j-44-cluster.yml"
    project = f"graphcheck-hostile-{uuid.uuid4().hex[:8]}"
    ports = _free_ports(3)
    env = {
        **os.environ,
        "GRAPHCHECK_CORE1_BOLT": str(ports[0]),
        "GRAPHCHECK_CORE2_BOLT": str(ports[1]),
        "GRAPHCHECK_CORE3_BOLT": str(ports[2]),
    }
    command = ["docker", "compose", "-f", str(compose), "-p", project]
    if subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False
    ).returncode:
        pytest.skip("Docker Compose v2 is required for the Neo4j 4.4 cluster case")
    started = subprocess.run(
        [*command, "up", "--detach", "--wait", "--wait-timeout", "240"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if started.returncode:
        logs = subprocess.run(
            [*command, "logs", "--no-color"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        subprocess.run(
            [*command, "down", "--volumes", "--remove-orphans"],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        pytest.fail(f"Neo4j 4.4 cluster failed to start:\n{started.stderr}\n{logs.stdout[-8000:]}")
    try:
        yield ConnectionProfile(
            uri=f"neo4j://127.0.0.1:{ports[0]}",
            user="neo4j",
            password=_NEO4J_44_PASSWORD,
            database="neo4j",
        )
    finally:
        subprocess.run(
            [*command, "down", "--volumes", "--remove-orphans"],
            env=env,
            capture_output=True,
            timeout=120,
            check=False,
        )


@pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_TARGET") not in {None, "lts-cypher-5"},
    reason="run the Neo4j 4.4 cluster once beside the LTS integration lane",
)
def test_neo4j_44_cluster_cli_matrix_rejects_legacy_server(neo4j_44_cluster_profile, tmp_path):
    _project(tmp_path, neo4j_44_cluster_profile, "empty.yml")

    results = _matrix(tmp_path, "hostile-empty", expected=(1, 1, 3))

    assert "neo4j.unsupported_version" in results["debug"].stderr
    assert "neo4j.unsupported_version" in results["profile"].stderr
    assert _run_payload(tmp_path)["run"]["error"]["code"] == "neo4j.unsupported_version"


def _scale_dataset(root: Path) -> Path:
    configured = os.environ.get("GRAPHCHECK_HOSTILE_DATASET")
    path = Path(configured).resolve() if configured else root / "email-EuAll.txt.gz"
    if not path.is_file():
        request = Request(_SCALE_URL, headers={"User-Agent": "GraphCheck hostile certification"})
        with urlopen(request, timeout=90) as response, path.open("wb") as target:
            shutil.copyfileobj(response, target)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _SCALE_SHA256
    return path


def _load_scale_graph(profile: ConnectionProfile, dataset: Path) -> None:
    query = """
UNWIND $edges AS edge
MERGE (source:HostileEmailAddress {id: edge.source})
MERGE (target:HostileEmailAddress {id: edge.target})
MERGE (source)-[:EMAILED]->(target)
"""
    with (
        GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver,
        driver.session(database=profile.database) as session,
    ):
        session.run("MATCH (n) DETACH DELETE n").consume()
        session.run(
            "CREATE CONSTRAINT hostile_email_id IF NOT EXISTS "
            "FOR (n:HostileEmailAddress) REQUIRE n.id IS UNIQUE"
        ).consume()
        batch: list[dict[str, int]] = []
        with gzip.open(dataset, "rt", encoding="utf-8") as source:
            for line in source:
                if line.startswith("#"):
                    continue
                start, end = (int(value) for value in line.split())
                batch.append({"source": start, "target": end})
                if len(batch) == 5_000:
                    session.run(query, edges=batch).consume()
                    batch.clear()
        if batch:
            session.run(query, edges=batch).consume()
        counts = session.run(
            "MATCH (n:HostileEmailAddress) WITH count(n) AS nodes "
            "MATCH ()-[r:EMAILED]->() RETURN nodes, count(r) AS relationships"
        ).single(strict=True)
        assert counts["nodes"] == _SCALE_NODES
        assert counts["relationships"] == _SCALE_RELATIONSHIPS


@pytest.mark.hostile_scale
def test_public_scale_cli_matrix_is_bounded_and_graceful(neo4j_profile, tmp_path):
    if os.environ.get("GRAPHCHECK_HOSTILE_SCALE") != "1":
        pytest.skip("set GRAPHCHECK_HOSTILE_SCALE=1 to load the public scale dataset")
    _project(tmp_path, neo4j_profile, "public-scale.yml")
    dataset = _scale_dataset(tmp_path)
    try:
        _load_scale_graph(neo4j_profile, dataset)
        results = _matrix(tmp_path, "hostile-public-scale", expected=(0, 0, 0), timeout=240)
        profile = json.loads(results["profile"].stdout)
        assert profile["statistics"]["node_count"] == _SCALE_NODES
        assert profile["statistics"]["relationship_count"] == _SCALE_RELATIONSHIPS
    finally:
        with (
            GraphDatabase.driver(
                neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
            ) as driver,
            driver.session(database=neo4j_profile.database) as session,
        ):
            session.run("MATCH (n) DETACH DELETE n").consume()
            session.run("DROP CONSTRAINT hostile_email_id IF EXISTS").consume()
