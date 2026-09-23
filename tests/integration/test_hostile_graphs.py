from __future__ import annotations

import gzip
import hashlib
import json
import os
import runpy
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
from graphcheck.packs.graphrag import GRAPHRAG_CHECK_NAMES
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


def _assert_safe(
    result: subprocess.CompletedProcess[str], expected_exit: int, *, data_failure: bool = False
) -> None:
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == expected_exit, output
    assert "Traceback (most recent call last)" not in output
    if result.returncode and not data_failure:
        assert "Fix:" in output or "Suggested fix" in output, output


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
    payload = _run_payload(root)
    data_failure = (
        payload["run"]["run_status"] == "complete"
        and any(check["verdict"] == "fail" for check in payload["checks"])
        and all(check["error"] is None for check in payload["checks"])
    )
    for (command, result), exit_code in zip(results.items(), expected, strict=True):
        _assert_safe(result, exit_code, data_failure=command == "run" and data_failure)
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


def _schema_object_names(session, show_command: str) -> set[str]:
    return {
        record["name"] for record in session.run(f"{show_command} YIELD name RETURN name").data()
    }


@contextmanager
def _seeded_graph(profile: ConnectionProfile, cypher: str | None = None) -> Iterator[None]:
    with GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver:
        with driver.session(database=profile.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            # Snapshot schema state so exit can drop only what this test itself introduces --
            # never touching schema objects independently managed by other fixtures.
            before_constraints = _schema_object_names(session, "SHOW CONSTRAINTS")
            before_indexes = _schema_object_names(session, "SHOW INDEXES")
            if cypher is not None:
                session.run(cypher).consume()
        try:
            yield
        finally:
            with driver.session(database=profile.database) as session:
                session.run("MATCH (n) DETACH DELETE n").consume()
                for name in _schema_object_names(session, "SHOW CONSTRAINTS") - before_constraints:
                    session.run(f"DROP CONSTRAINT `{name}` IF EXISTS").consume()
                for name in _schema_object_names(session, "SHOW INDEXES") - before_indexes:
                    session.run(f"DROP INDEX `{name}` IF EXISTS").consume()


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
        "degree_distribution": [],
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
    assert _run_payload(tmp_path)["run"]["run_status"] == "complete"


def _assert_graphrag_fixture(profile, checks, *, clean, label_explosion_min_population=50):
    assert set(checks) == set(GRAPHRAG_CHECK_NAMES)
    with (
        GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver,
        driver.session(database=profile.database) as session,
    ):
        node_count = session.run("MATCH (n) RETURN count(n) AS count").single(strict=True)["count"]
        min_population = label_explosion_min_population
        # This fixture family always carries singleton case/slash-variant labels (Person,
        # person, Machine / Concept, etc.), clean or planted, so label_explosion always
        # fails once the population floor is cleared -- never "pass" on this fixture.
        label_explosion_expected = "skipped" if node_count < min_population else "fail"
        for name, check in checks.items():
            if name == "label_explosion":
                assert check["verdict"] == label_explosion_expected
                if label_explosion_expected == "skipped":
                    assert check["error"] is None and check["measured"] is None
                continue
            assert check["verdict"] == ("pass" if clean else "fail")
        # These two features remain fixture assertions, not additional pack checks.
        singleton = session.run(
            "MATCH (n:__Entity__:SingletonTopic) RETURN count(n) AS count"
        ).single(strict=True)["count"]
        assert singleton == (0 if clean else 1)
        uncovered = session.run(
            "MATCH (n:Chunk) WHERE NOT EXISTS { MATCH (n)-[:HAS_ENTITY]->(:__Entity__) } "
            "RETURN n.id AS id"
        )
        assert {row["id"] for row in uncovered} == (
            set()
            if clean
            else {
                "chunk-orphan",
                "chunk-missing-embedding",
                "chunk-wrong-dimension",
                "chunk-zero-embedding",
                "chunk-nan-embedding",
            }
        )
        if clean:
            assert checks["chunk_coverage"]["measured"] == {
                "population": 2,
                "conforming_count": 2,
                "violation_count": 0,
                "violations": 0,
                "coverage": 1.0,
            }
            return
        node_ids = dict(
            session.run(
                "MATCH (n) RETURN elementId(n) AS element_id, coalesce(n.id, n.fileName) AS id"
            ).values()
        )
        expected = {
            "orphan_chunks": {"chunk-orphan", "no-chunks.txt"},
            "entity_without_provenance": {"unprovenanced-entity"},
        }
        for name, planted in expected.items():
            assert checks[name]["measured"]["violations"] == len(planted)
            assert {
                node_ids[row["node_id"]] for row in checks[name]["measured"]["findings"]
            } == planted
            assert all(row["missing_path"] for row in checks[name]["measured"]["findings"])
        dangling = checks["dangling_extraction_relationships"]["measured"]
        assert dangling["violations"] == 1
        planted_rel = session.run("MATCH ()-[r:MENTORED]->() RETURN elementId(r) AS id").single(
            strict=True
        )["id"]
        assert dangling["findings"][0]["rel_id"] == planted_rel
        assert {node_ids[value] for value in dangling["findings"][0]["missing_node_ids"]} == {
            "unprovenanced-entity"
        }
        duplicates = checks["near_duplicate_entities"]["measured"]["findings"]
        assert len(duplicates) == 1 and duplicates[0]["normalized_key"] == "adalovelace"
        assert {node_ids[value] for value in duplicates[0]["node_ids"]} == {
            "Ada Lovelace",
            "ada_lovelace",
        }
        embeddings = checks["embedding_consistency"]["measured"]
        assert embeddings["violations"] == 4 and embeddings["expected_dimension"] == 4
        assert {
            node_ids[row["node_id"]]: (row["defect"], row["dimension"])
            for row in embeddings["findings"]
        } == {
            "chunk-missing-embedding": ("missing", None),
            "chunk-wrong-dimension": ("wrong_dimension", 3),
            "chunk-zero-embedding": ("zero", 4),
            "chunk-nan-embedding": ("nan", 4),
        }
        chunk_coverage = checks["chunk_coverage"]["measured"]
        assert chunk_coverage["population"] == 7
        assert chunk_coverage["conforming_count"] == 2
        assert chunk_coverage["violation_count"] == 5
        assert chunk_coverage["coverage"] == pytest.approx(2 / 7)
        uncovered_ids = {
            node_ids[element["id"]] for element in checks["chunk_coverage"]["evidence"]["elements"]
        }
        assert uncovered_ids == {
            "chunk-orphan",
            "chunk-missing-embedding",
            "chunk-wrong-dimension",
            "chunk-zero-embedding",
            "chunk-nan-embedding",
        }
        if label_explosion_expected == "fail":
            label_explosion = checks["label_explosion"]["measured"]
            assert label_explosion["violations"] == 13
            findings = {
                (finding["item_kind"], finding["name"]) for finding in label_explosion["findings"]
            }
            assert findings == {
                ("label", "Person"),
                ("label", "person"),
                ("label", "Machine / Concept"),
                ("label", "Country"),
                ("label", "Country / Region"),
                ("label", "SingletonTopic"),
                ("label", "Odd`Label"),
                ("relationship_type", "FIRST_CHUNK"),
                ("relationship_type", "NEXT_CHUNK"),
                ("relationship_type", "WORKED-WITH"),
                ("relationship_type", "LOCATED_IN"),
                ("relationship_type", "MENTORED"),
                ("relationship_type", "points to"),
            }
        assert all(
            check["evidence"]["elements"]
            for name, check in checks.items()
            if check["verdict"] != "skipped"
        )


def _graphrag_case(root, profile, name, *, clean):
    _prepare_case(root, profile, name)
    fixture = (_HOSTILE / _CASES[name]["fixture"]).read_text(encoding="utf-8")
    with _seeded_graph(profile, fixture):
        _case_matrix(root, name)
        checks = {check["id"]: check for check in _run_payload(root)["checks"]}
        _assert_graphrag_fixture(profile, checks, clean=clean, label_explosion_min_population=15)


def test_graphrag_hostile_pack_finds_every_planted_defect(neo4j_profile, tmp_path):
    _graphrag_case(tmp_path, neo4j_profile, "graphrag-planted", clean=False)


def test_graphrag_hostile_clean_pack_passes(neo4j_profile, tmp_path):
    _graphrag_case(tmp_path, neo4j_profile, "graphrag-clean", clean=True)


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
def test_public_scale_cli_matrix_is_bounded_and_graceful(
    neo4j_profile, tmp_path, record_testsuite_property
):
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
        drift_verdicts = {
            check["id"]: check["verdict"]
            for check in _run_payload(tmp_path)["checks"]
            if check["id"].startswith("public-graph-")
        }
        assert drift_verdicts["public-graph-schema-inventory"] == "pass"
        assert drift_verdicts["public-graph-degree-max"] == "pass"
        suite_path = tmp_path / "checks" / str(case["suite"])
        suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
        suite["conformance"] = [
            {
                "id": "bounded-evidence",
                "check": "completeness",
                "severity": "error",
                "with": {
                    "label": "HostileEmailAddress",
                    "property": "graphcheck_changes_probe",
                    "threshold": 1.0,
                },
            }
        ]
        suite_path.write_text(yaml.safe_dump(suite), encoding="utf-8")
        with (
            GraphDatabase.driver(
                neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
            ) as driver,
            driver.session(database=neo4j_profile.database) as session,
        ):
            session.run(
                "MATCH (n:HostileEmailAddress) WITH n ORDER BY elementId(n) DESC LIMIT 1 "
                "SET n.graphcheck_changes_probe = true"
            ).consume()
            assert _cli(tmp_path, "run", timeout=240).returncode == 1
            session.run(
                "MATCH (n:HostileEmailAddress) WITH n ORDER BY elementId(n) LIMIT 100 "
                "SET n.graphcheck_changes_probe = true"
            ).consume()
        assert _cli(tmp_path, "run", timeout=240).returncode == 1
        changes = _cli(tmp_path, "changes", "--json")
        repeated = _cli(tmp_path, "changes", "--json")
        assert changes.returncode == repeated.returncode == 0
        assert changes.stdout == repeated.stdout
        assert len(changes.stdout.encode("utf-8")) < 100_000
        delta = next(
            item
            for item in json.loads(changes.stdout)["evidence"]
            if item["check_id"] == "bounded-evidence"
        )
        assert len(delta["appeared"]) + len(delta["disappeared"]) <= delta["cap"] == 100
        assert delta["dropped"] == 100
        assert delta["before_truncated"] and delta["after_truncated"]
        # Exercise every pack check over the same 265,214-node / 420,045-edge graph.
        with (
            GraphDatabase.driver(
                neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
            ) as driver,
            driver.session(database=neo4j_profile.database) as session,
        ):
            session.run(
                "MATCH (n:HostileEmailAddress) "
                "SET n.name = 'email-' + toString(n.id), n.embedding = [1.0, 0.0, 0.0, 0.0]"
            ).consume()
            planted_ids = [
                row["id"]
                for row in session.run(
                    "MATCH (n:HostileEmailAddress) RETURN n.id AS id ORDER BY id LIMIT 4"
                )
            ]
            session.run(
                "MATCH (n:HostileEmailAddress) WHERE n.id IN $ids "
                "SET n.embedding = CASE n.id WHEN $ids[0] THEN null "
                "WHEN $ids[1] THEN [1.0, 2.0, 3.0] WHEN $ids[2] THEN [0.0, 0.0, 0.0, 0.0] "
                "ELSE [1.0, 0.0 / 0.0, 0.0, 0.0] END",
                ids=planted_ids,
            ).consume()
        pack_suite = yaml.safe_load((_HOSTILE / case["pack_suite"]).read_text(encoding="utf-8"))
        pack_suite["suite"] = "graphrag-scale"
        for check in pack_suite["conformance"]:
            check["with"] = {
                **check["with"],
                "document_label": "HostileEmailAddress",
                "chunk_label": "HostileEmailAddress",
                "entity_label": "HostileEmailAddress",
                "document_chunk_rel": "EMAILED",
                "chunk_entity_rel": "EMAILED",
            }
            if check["check"] == "near_duplicate_entities":
                check["with"].update(sample_size=1000, threshold=1.0)
        (tmp_path / "checks/graphrag-scale.yml").write_text(
            yaml.safe_dump(pack_suite), encoding="utf-8"
        )
        started = time.monotonic()
        run = _cli(
            tmp_path, "run", "--suite", "graphrag-scale", timeout=case["pack_timeout_seconds"]
        )
        elapsed = time.monotonic() - started
        record_testsuite_property("graphrag_runtime_seconds", round(elapsed, 3))
        record_testsuite_property("graphrag_nodes", case["nodes"])
        record_testsuite_property("graphrag_relationships", case["relationships"])
        assert elapsed < case["pack_timeout_seconds"]
        _assert_safe(run, case["pack_expected_exit_code"], data_failure=True)
        checks = {check["id"]: check for check in _run_payload(tmp_path)["checks"]}
        assert set(checks) == set(GRAPHRAG_CHECK_NAMES)
        for check in checks.values():
            assert check["verdict"] in {"pass", "fail"}, check
        check = checks["near_duplicate_entities"]
        assert check["verdict"] == "pass", check["error"]
        assert check["measured"]["population"] == case["nodes"]
        assert 0 < check["measured"]["sample_size"] <= 1000
        assert check["estimate"]["sample_size"] <= 1000 and check["estimate"]["ci"] is None
        embeddings = checks["embedding_consistency"]["measured"]
        assert embeddings["population"] == case["nodes"] and embeddings["violations"] == 4
        assert embeddings["expected_dimension"] == 4
        assert {row["defect"] for row in embeddings["findings"]} == {
            "missing",
            "wrong_dimension",
            "zero",
            "nan",
        }
        print(
            f"GraphRAG hostile pack: {case['nodes']} nodes, "
            f"{case['relationships']} relationships in {elapsed:.2f}s"
        )

    finally:
        with (
            GraphDatabase.driver(
                neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
            ) as driver,
            driver.session(database=neo4j_profile.database) as session,
        ):
            session.run("MATCH (n) DETACH DELETE n").consume()
            session.run("DROP CONSTRAINT hostile_email_id IF EXISTS").consume()


def test_fraud_ring_changes_join_new_fixed_count_and_orphan_deltas(neo4j_profile, tmp_path):
    root = Path(__file__).parents[2]
    fixture = root / "tests/fixtures/external/fraud-ring"
    split = runpy.run_path(str(fixture / "tests/cypher_utils.py"))["split_statements"]
    _project(tmp_path, neo4j_profile, "empty.yml")
    (tmp_path / "checks/empty.yml").unlink()
    suite = yaml.safe_load(
        (root / "examples/fraud-ring/checks/fraud-ring-conformance.yml").read_text(encoding="utf-8")
    )
    suite["competency"] = [
        {
            "id": "customer-tax-id-fixed",
            "question": "Is the tax ID present?",
            "query": "MATCH (c:Customer {id: 'CUST-1'}) WHERE c.tax_id IS NULL "
            "RETURN elementId(c) AS node_element_id",
            "expect": {"rows": {"exactly": 0}},
        }
    ]
    (tmp_path / "checks/fraud-ring.yml").write_text(yaml.safe_dump(suite), encoding="utf-8")
    with (
        _seeded_graph(neo4j_profile),
        GraphDatabase.driver(
            neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
        ) as driver,
        driver.session(database=neo4j_profile.database) as session,
    ):
        for statement in split(
            (fixture / "fixtures/fraud-ring/seed-clean.cypher").read_text(encoding="utf-8")
        ):
            session.run(statement).consume()
        session.run("MATCH (c:Customer {id: 'CUST-1'}) REMOVE c.tax_id").consume()
        assert _cli(tmp_path, "profile", "--json", timeout=240).returncode == 0
        assert _cli(tmp_path, "run").returncode == 1
        before = _run_payload(tmp_path)
        assert "changes" not in json.loads(
            (tmp_path / ".graphcheck/runs/latest/summary.json").read_text()
        )
        assert before["run"]["run_status"] == "complete", before
        assert {check["id"]: check["verdict"] for check in before["checks"]} == {
            "account-no-orphans": "pass",
            "account-owner-cardinality": "pass",
            "customer-tax-id-fixed": "fail",
        }, before["checks"]
        orphan_id = session.run(
            "MATCH (c:Customer {id: 'CUST-1'}) SET c.tax_id = '100000001' "
            "CREATE (n:Account {id: 'ACC-NEW-ORPHAN'}) "
            "RETURN elementId(n) AS id"
        ).single(strict=True)["id"]
        assert _cli(tmp_path, "profile", "--json", timeout=240).returncode == 0
        assert _cli(tmp_path, "run").returncode == 1
        after = _run_payload(tmp_path)
        assert after["run"]["run_status"] == "complete", after
        assert {check["id"]: check["verdict"] for check in after["checks"]} == {
            "account-no-orphans": "fail",
            "account-owner-cardinality": "fail",
            "customer-tax-id-fixed": "pass",
        }, after["checks"]
        assert after["run"]["previous_run_id"] == before["run"]["id"]
        assert after["run"]["baseline_ref"] != before["run"]["baseline_ref"]
        summary = json.loads((tmp_path / ".graphcheck/runs/latest/summary.json").read_text())[
            "changes"
        ]
        assert summary["previous_run_id"] == before["run"]["id"]
        assert {item["check_id"] for item in summary["new_failures"]} == {
            "account-no-orphans",
            "account-owner-cardinality",
        }
        assert [item["check_id"] for item in summary["fixed_checks"]] == ["customer-tax-id-fixed"]
        assert summary["count_deltas"]["nodes"]["delta"] == 1
        text = _cli(tmp_path, "changes")
        first, second = _cli(tmp_path, "changes", "--json"), _cli(tmp_path, "changes", "--json")
        assert text.returncode == first.returncode == second.returncode == 1
        assert first.stdout == second.stdout
        assert "account-no-orphans: pass -> fail" in text.stdout
        assert "customer-tax-id-fixed: fail -> pass" in text.stdout
        assert "(+1," in text.stdout and orphan_id in text.stdout
        payload = json.loads(first.stdout)
        assert payload["profile"]["statistics"]["node_count"]["delta"] == 1
        delta = next(
            item for item in payload["evidence"] if item["check_id"] == "account-no-orphans"
        )
        assert delta["appeared"] == [{"kind": "node", "id": orphan_id}]
        assert delta["disappeared"] == [] and delta["dropped"] == 0
