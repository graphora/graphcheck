"""Correctness checks for shared completeness scans and per-node PII enumeration."""

import math
import os

import pytest
import yaml
from neo4j import GraphDatabase

from graphcheck.contracts.check import load_suite
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.engine.pii_pack import _SAMPLE_NODE_MULTIPLIER
from graphcheck.engine.runner import Engine
from graphcheck.engine.sampling import CYPHER_SAMPLE_MODULUS, cypher_hash_value
from graphcheck.neo4j_adapter import Neo4jClient

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="requires the disposable supported-server matrix",
)


@pytest.fixture(params=[0, 1, 16])
def property_graph(neo4j_profile, request):
    with (
        GraphDatabase.driver(
            neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
        ) as driver,
        driver.session(database=neo4j_profile.database) as session,
    ):
        rows = [
            {
                f"p{prop}": f"value-{node}-{prop}" if (node + prop) % 3 else None
                for prop in reversed(range(32))
            }
            | {"email": f"user{node}@example.org", "number": node, "array": [1, 2]}
            for node in reversed(range(request.param))
        ]
        try:
            session.run(
                "UNWIND $rows AS row CREATE (n:SharedQueryFixture) SET n = row", rows=rows
            ).consume()
            yield neo4j_profile, session, request.param
        finally:
            session.run("MATCH (n:SharedQueryFixture) DETACH DELETE n").consume()


@pytest.mark.parametrize("size", [2, 8, 32])
def test_completeness_batch_matches_individual_queries(property_graph, size):
    profile, session, count = property_graph
    checks = load_suite(
        yaml.safe_dump(
            {
                "suite": "properties",
                "conformance": [
                    {
                        "id": f"c{i}",
                        "check": "completeness",
                        "with": {
                            "label": "SharedQueryFixture",
                            "property": f"p{i % 8}",
                            "threshold": 0.5,
                        },
                    }
                    for i in range(size)
                ],
            }
        )
    )
    compiler = CypherCompiler()
    batch = compiler.compile_completeness_batch(checks.checks)
    rows = session.run(batch.query, batch.params).data()
    for check, (_, column) in zip(checks.checks, batch.members, strict=True):
        compiled = compiler.compile(check)
        individual = session.run(compiled.query, compiled.params).data()
        assert rows[0]["population"] == individual[0]["population"] == count
        assert rows[0][column] == individual[0]["conforming_count"]
    client = Neo4jClient(profile)
    try:
        result = Engine(client).run_suite(checks)
        assert len(result.checks) == size
        if count:
            assert all(check.error is None for check in result.checks)
            assert all(check.measured["population"] == count for check in result.checks)
    finally:
        client.close()


@pytest.mark.parametrize("seed", [0, 17, 321])
@pytest.mark.parametrize(
    "template,properties",
    [
        ("pii_name_match", []),
        ("pii_value_match", []),
        ("pii_value_match", ["p2", "email", "p1", "number", "array"]),
    ],
)
def test_pii_candidates_match_original_sampling_algorithm(
    property_graph, seed, template, properties
):
    _, session, _ = property_graph
    config = {"label": "SharedQueryFixture", "sample_size": 5, "patterns": ["email"]}
    if properties:
        config["properties"] = properties
    check = load_suite(
        yaml.safe_dump(
            {"suite": "pii", "conformance": [{"id": "pii", "check": template, "with": config}]}
        )
    ).checks[0]
    compiled = CypherCompiler().compile(check, sample_seed=seed)
    nodes = session.run(
        "CYPHER 5 MATCH (n:SharedQueryFixture) "
        "RETURN id(n) AS node_id, elementId(n) AS element_id, properties(n) AS properties"
    ).data()
    candidates = []
    for node in nodes:
        names = sorted(
            name
            for name in (properties or node["properties"])
            if node["properties"].get(name) is not None
            and (template == "pii_name_match" or isinstance(node["properties"][name], str))
        )
        for index, name in enumerate(names):
            key = (
                (node["node_id"] % CYPHER_SAMPLE_MODULUS) * _SAMPLE_NODE_MULTIPLIER + index
            ) % CYPHER_SAMPLE_MODULUS
            candidates.append((key, node["element_id"], name, node["properties"][name]))
    population = len(candidates)
    params = compiled.params
    hashes = {name: value for name, value in params.items() if name.startswith("sample_hash_")}
    gate_hashes = {
        name.replace("sample_gate_hash_", "sample_hash_"): value
        for name, value in params.items()
        if name.startswith("sample_gate_hash_")
    }
    gate_population = params["sample_size"] * params["sample_gate_multiplier"]
    selected = [
        item
        for item in candidates
        if population <= gate_population
        or cypher_hash_value(item[0], gate_hashes)
        < math.ceil(CYPHER_SAMPLE_MODULUS * gate_population / population)
    ]
    selected.sort(key=lambda item: (cypher_hash_value(item[0], hashes), item[1], item[2]))
    expected = [
        {
            "evidence": {"kind": "node", "id": element_id, "labels": ["SharedQueryFixture"]},
            "property": name,
            **({"value": value} if template == "pii_value_match" else {}),
        }
        for _, element_id, name, value in selected[: params["sample_size"]]
    ]
    actual = session.run(compiled.query, params).data()[0]
    assert actual["population"] == population
    assert actual["sample_size"] == len(expected)
    assert actual["candidates"] == expected
