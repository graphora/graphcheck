from pathlib import Path

import yaml

from graphcheck.contracts.check import load_suite

HOSTILE = Path(__file__).parent / "integration" / "hostile"


def test_hostile_manifest_defines_every_required_case_and_command():
    cases = yaml.safe_load((HOSTILE / "cases.yml").read_text(encoding="utf-8"))["cases"]

    assert set(cases) == {
        "llm-kg-builder",
        "public-scale",
        "neo4j-4.4-cluster",
        "apoc-less",
        "empty",
    }
    assert all(
        set(case["expected_exit_codes"]) == {"debug", "profile", "run"} for case in cases.values()
    )
    assert all((HOSTILE / case["suite"]).is_file() for case in cases.values())
    assert (HOSTILE / cases["llm-kg-builder"]["fixture"]).is_file()
    assert (HOSTILE / cases["neo4j-4.4-cluster"]["compose"]).is_file()


def test_scale_dataset_identity_and_published_size_are_pinned():
    scale = yaml.safe_load((HOSTILE / "cases.yml").read_text(encoding="utf-8"))["cases"][
        "public-scale"
    ]

    assert scale["dataset"] == "https://snap.stanford.edu/data/email-EuAll.txt.gz"
    assert scale["sha256"] == "c256f8be57084fe7b2dbe96f99d4d79e56c19228773526058abc99a6fa86e9d9"
    assert (scale["nodes"], scale["relationships"]) == (265_214, 420_045)


def test_llm_fixture_keeps_noisy_schema_features():
    cypher = (HOSTILE / "llm-kg-builder.cypher").read_text(encoding="utf-8")

    assert all(
        token in cypher for token in ("__Entity__", "Odd``Label", "age: 'unknown'", "rank: 'first'")
    )


def test_hostile_suites_are_valid_graphcheck_contracts():
    cases = yaml.safe_load((HOSTILE / "cases.yml").read_text(encoding="utf-8"))["cases"]

    for suite in {case["suite"] for case in cases.values()}:
        assert load_suite((HOSTILE / suite).read_text(encoding="utf-8")).suite


def test_neo4j_44_compose_defines_three_pinned_enterprise_members():
    compose = yaml.safe_load((HOSTILE / "neo4j-44-cluster.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"core1", "core2", "core3"}
    assert all(
        service["image"] == "neo4j:4.4.44-enterprise" for service in compose["services"].values()
    )
