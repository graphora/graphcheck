from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import yaml
from neo4j import GraphDatabase

from graphcheck.connection_profiles import ConnectionProfile, ProfilesFile
from graphcheck.contracts.check import load_suite
from graphcheck.project import PROFILES_FILE, write_default_project

pytestmark = [
    pytest.mark.hostile,
    pytest.mark.skipif(
        os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
        reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run hostile Neo4j tests",
    ),
]

_HOSTILE = Path(__file__).with_name("hostile")
_CASES = yaml.safe_load((_HOSTILE / "cases.yml").read_text(encoding="utf-8"))["cases"]
_COMMANDS = ("debug", "profile", "run")


def _project(root: Path, profile: ConnectionProfile, suite: str) -> None:
    write_default_project(root)
    profiles = ProfilesFile(default="hostile", profiles={"hostile": profile})
    (root / PROFILES_FILE).write_text(
        yaml.safe_dump(profiles.model_dump(), sort_keys=False), encoding="utf-8"
    )
    shutil.copyfile(_HOSTILE / suite, root / "checks" / suite)


def _prepare_case(root: Path, profile: ConnectionProfile, name: str) -> None:
    _project(root, profile, str(_CASES[name]["suite"]))


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


def _case_matrix(
    root: Path, name: str, *, timeout: int = 150
) -> dict[str, subprocess.CompletedProcess[str]]:
    case = _CASES[name]
    suite_id = load_suite((_HOSTILE / str(case["suite"])).read_text(encoding="utf-8")).suite
    exits = case["expected_exit_codes"]
    return _matrix(
        root,
        suite_id,
        expected=tuple(int(exits[command]) for command in _COMMANDS),
        timeout=timeout,
    )


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
    _prepare_case(tmp_path, neo4j_profile, "empty")
    with _seeded_graph(neo4j_profile):
        results = _case_matrix(tmp_path, "empty")

    assert "Counts: 0 nodes, 0 relationships" in results["debug"].stdout
    profile = json.loads(results["profile"].stdout)
    assert profile["statistics"] == {
        "node_count": 0,
        "relationship_count": 0,
        "property_coverage": [],
    }
    assert "Empty graph:" in results["run"].stdout


def test_llm_kg_builder_cli_matrix_handles_noisy_schema(neo4j_profile, tmp_path):
    case = _CASES["llm-kg-builder"]
    _prepare_case(tmp_path, neo4j_profile, "llm-kg-builder")
    cypher = (_HOSTILE / str(case["fixture"])).read_text(encoding="utf-8")
    with _seeded_graph(neo4j_profile, cypher):
        results = _case_matrix(tmp_path, "llm-kg-builder")

    profile = json.loads(results["profile"].stdout)
    labels = {item["name"] for item in profile["schema"]["labels"]}
    relationship_types = {item["name"] for item in profile["schema"]["relationship_types"]}
    assert {"__Entity__", "Country / Region", "Odd`Label"} <= labels
    assert {"HAS_ENTITY", "WORKED-WITH", "points to"} <= relationship_types
    assert _run_payload(tmp_path)["run"]["status"] == "complete"


def test_apoc_less_cli_matrix_is_actionable_and_isolated(neo4j_profile, tmp_path):
    _prepare_case(tmp_path, neo4j_profile, "apoc-less")
    with _seeded_graph(neo4j_profile):
        results = _case_matrix(tmp_path, "apoc-less")

    payload = _run_payload(tmp_path)
    checks = payload["checks"]
    assert "APOC: no" in results["debug"].stdout
    assert [check["verdict"] for check in checks] == ["errored", "pass"]
    assert checks[0]["error"]["code"] == "neo4j.query_failed"
    assert checks[0]["error"]["fix"]


def _assert_neo4j_44_topology(profile: ConnectionProfile, timeout: int = 90) -> None:
    expected_bolt_addresses = {f"bolt://core{index}:7687" for index in range(1, 4)}
    deadline = time.monotonic() + timeout
    last_observation: object = "cluster overview was not returned"
    while time.monotonic() < deadline:
        try:
            with (
                GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver,
                driver.session(database=profile.database) as session,
            ):
                rows = session.run(
                    "CALL dbms.cluster.overview() YIELD id, addresses, databases "
                    "RETURN id, addresses, databases"
                ).data()
            members = [
                row
                for row in rows
                if isinstance(row.get("databases"), dict) and profile.database in row["databases"]
            ]
            roles = [str(row["databases"][profile.database]).upper() for row in members]
            member_ids = {str(row["id"]) for row in members}
            bolt_addresses = {
                str(address)
                for row in members
                for address in row.get("addresses", [])
                if str(address).startswith("bolt://")
            }
            if (
                len(members) == 3
                and len(member_ids) == 3
                and roles.count("LEADER") == 1
                and roles.count("FOLLOWER") == 2
                and bolt_addresses == expected_bolt_addresses
            ):
                return
            last_observation = {
                "member_ids": sorted(member_ids),
                "roles": roles,
                "bolt_addresses": sorted(bolt_addresses),
            }
        except Exception as error:  # pragma: no cover - retained in the eventual test failure
            last_observation = f"{type(error).__name__}: {error}"
        time.sleep(2)
    pytest.fail(
        "Neo4j 4.4 did not form the expected three-member cluster with one leader and two "
        f"followers; last observation: {last_observation}"
    )


@pytest.fixture(scope="session")
def neo4j_44_cluster_profile():
    case = _CASES["neo4j-4.4-cluster"]
    enable_env = str(case["enable_env"])
    if os.environ.get(enable_env) != "1":
        pytest.skip(f"set {enable_env}=1 to run the Neo4j 4.4 cluster case")
    if shutil.which("docker") is None:
        pytest.skip("Docker with Compose is required for the Neo4j 4.4 cluster case")
    compose = _HOSTILE / str(case["compose"])
    project = f"graphcheck-hostile-{uuid.uuid4().hex[:8]}"
    env = os.environ.copy()
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
        published = subprocess.run(
            [*command, "port", "core1", "7687"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if published.returncode:
            pytest.fail(f"Could not discover Docker-assigned core1 port: {published.stderr}")
        profile = ConnectionProfile(
            uri=f"bolt://127.0.0.1:{published.stdout.strip().rsplit(':', 1)[-1]}",
            user="neo4j",
            password=str(case["password"]),
            database="neo4j",
        )
        _assert_neo4j_44_topology(profile)
        yield profile
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
    _prepare_case(tmp_path, neo4j_44_cluster_profile, "neo4j-4.4-cluster")

    results = _case_matrix(tmp_path, "neo4j-4.4-cluster")

    assert "neo4j.unsupported_version" in results["debug"].stderr
    assert "neo4j.unsupported_version" in results["profile"].stderr
    assert _run_payload(tmp_path)["run"]["error"]["code"] == "neo4j.unsupported_version"


def _scale_dataset(root: Path) -> Path:
    case = _CASES["public-scale"]
    configured = os.environ.get("GRAPHCHECK_HOSTILE_DATASET")
    path = Path(configured).resolve() if configured else root / "email-EuAll.txt.gz"
    if not path.is_file():
        request = Request(
            str(case["dataset"]), headers={"User-Agent": "GraphCheck hostile certification"}
        )
        with urlopen(request, timeout=90) as response, path.open("wb") as target:
            shutil.copyfileobj(response, target)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
    return path


def _load_scale_graph(profile: ConnectionProfile, dataset: Path) -> None:
    case = _CASES["public-scale"]
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
        assert counts["nodes"] == case["nodes"]
        assert counts["relationships"] == case["relationships"]


@pytest.mark.hostile_scale
def test_public_scale_cli_matrix_is_bounded_and_graceful(neo4j_profile, tmp_path):
    case = _CASES["public-scale"]
    enable_env = str(case["enable_env"])
    if os.environ.get(enable_env) != "1":
        pytest.skip(f"set {enable_env}=1 to load the public scale dataset")
    _prepare_case(tmp_path, neo4j_profile, "public-scale")
    dataset = _scale_dataset(tmp_path)
    try:
        _load_scale_graph(neo4j_profile, dataset)
        results = _case_matrix(tmp_path, "public-scale", timeout=240)
        profile = json.loads(results["profile"].stdout)
        assert profile["statistics"]["node_count"] == case["nodes"]
        assert profile["statistics"]["relationship_count"] == case["relationships"]
    finally:
        with (
            GraphDatabase.driver(
                neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
            ) as driver,
            driver.session(database=neo4j_profile.database) as session,
        ):
            session.run("MATCH (n) DETACH DELETE n").consume()
            session.run("DROP CONSTRAINT hostile_email_id IF EXISTS").consume()
