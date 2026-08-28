from pathlib import Path

import yaml

from graphcheck.contracts.check import load_suite

HOSTILE = Path(__file__).parents[1] / "integration" / "hostile"
CASES = yaml.safe_load((HOSTILE / "cases.yml").read_text(encoding="utf-8"))["cases"]


def test_hostile_manifest_defines_every_required_case_and_command():
    assert set(CASES) == {
        "llm-kg-builder",
        "public-scale",
        "neo4j-4.4-cluster",
        "apoc-less",
        "empty",
    }
    assert all(
        set(case["expected_exit_codes"]) == {"debug", "profile", "run"} for case in CASES.values()
    )
    assert all((HOSTILE / case["suite"]).is_file() for case in CASES.values())
    assert (HOSTILE / CASES["llm-kg-builder"]["fixture"]).is_file()
    assert (HOSTILE / CASES["neo4j-4.4-cluster"]["compose"]).is_file()


def test_manifest_runtime_mapping_selects_real_tests_and_declares_lanes():
    integration_source = (HOSTILE.parent / "test_hostile_graphs.py").read_text(encoding="utf-8")
    runner_source = (HOSTILE.parents[2] / "tools" / "run_hostile_graphs.py").read_text(
        encoding="utf-8"
    )

    assert {case["lane"] for case in CASES.values()} == {"fast", "legacy", "scale"}
    assert len({case["pytest_test"] for case in CASES.values()}) == len(CASES)
    assert all(f"def {case['pytest_test']}(" in integration_source for case in CASES.values())
    assert "cases.yml" in runner_source
    assert "TESTS =" not in runner_source


def test_scale_dataset_identity_and_published_size_are_pinned():
    scale = CASES["public-scale"]

    assert scale["dataset"] == "https://snap.stanford.edu/data/email-EuAll.txt.gz"
    assert scale["sha256"] == "c256f8be57084fe7b2dbe96f99d4d79e56c19228773526058abc99a6fa86e9d9"
    assert (scale["nodes"], scale["relationships"]) == (265_214, 420_045)


def test_llm_fixture_keeps_noisy_schema_features():
    cypher = (HOSTILE / "llm-kg-builder.cypher").read_text(encoding="utf-8")

    assert all(
        token in cypher for token in ("__Entity__", "Odd``Label", "age: 'unknown'", "rank: 'first'")
    )


def test_hostile_suites_are_valid_graphcheck_contracts():
    for suite in {case["suite"] for case in CASES.values()}:
        assert load_suite((HOSTILE / suite).read_text(encoding="utf-8")).suite


def test_neo4j_44_compose_defines_three_pinned_enterprise_members():
    case = CASES["neo4j-4.4-cluster"]
    compose = yaml.safe_load((HOSTILE / case["compose"]).read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"core1", "core2", "core3"}
    assert all(service["image"] == case["server"] for service in compose["services"].values())
    assert all(service["ports"] == ["127.0.0.1::7687"] for service in compose["services"].values())
    assert {
        service["environment"]["NEO4J_dbms_connector_bolt_advertised__address"]
        for service in compose["services"].values()
    } == {"core1:7687", "core2:7687", "core3:7687"}
