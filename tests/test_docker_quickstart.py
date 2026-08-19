from pathlib import Path

import yaml

from graphcheck.cli import _load_suite_inputs

ROOT = Path(__file__).parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"
FIXTURE_COMMIT = "d2b8c76c2d2940f53f71491703619961a699c293"
SEED_SHA256 = "d955dbf08a3821a53e3b39f4f5234c16d13eb08c33b2a573fe422c85d5dcd90a"
FIXTURE_URL = (
    "https://raw.githubusercontent.com/graphora/graphcheck-fraud-ring-fixture/"
    f"{FIXTURE_COMMIT}/fixtures/fraud-ring/seed.cypher"
)


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_compose_pins_local_neo4j_and_demo_database():
    compose = _compose()
    neo4j = compose["services"]["neo4j"]

    assert neo4j["image"] == "neo4j:5.26.28"
    assert neo4j["environment"] == {
        "NEO4J_AUTH": "neo4j/Password@123",
        "NEO4J_initial_dbms_default__database": "graphcheck-demo",
    }
    assert neo4j["ports"] == ["127.0.0.1:7474:7474", "127.0.0.1:7687:7687"]
    assert neo4j["volumes"] == ["neo4j-data:/data"]
    healthcheck = " ".join(str(part) for part in neo4j["healthcheck"]["test"])
    assert "cypher-shell" in healthcheck
    assert "-u neo4j" in healthcheck
    assert "-p 'Password@123'" in healthcheck
    assert "-d graphcheck-demo" in healthcheck
    assert "RETURN 1" in healthcheck


def test_compose_fetches_and_verifies_the_pinned_canonical_seed():
    compose = _compose()
    fetch = compose["services"]["fixture-fetch"]
    seed = compose["services"]["fixture-seed"]
    fetch_command = fetch["command"][0]
    seed_command = seed["command"][0]

    assert f'fixture_url="{FIXTURE_URL}"' in fetch_command
    assert SEED_SHA256 in fetch_command
    assert "sha256sum -c" in fetch_command
    assert seed["image"] == "neo4j:5.26.28"
    assert seed["depends_on"]["neo4j"]["condition"] == "service_healthy"
    assert seed["depends_on"]["fixture-fetch"]["condition"] == ("service_completed_successfully")
    assert "-d graphcheck-demo" in seed_command
    assert "--fail-fast" in seed_command
    assert fetch["volumes"] == ["fixture-data:/fixture"]
    assert seed["volumes"] == ["fixture-data:/fixture:ro"]


def test_committed_profiles_match_local_quickstart_and_ci():
    profiles = yaml.safe_load((ROOT / "profiles.yml").read_text(encoding="utf-8"))

    assert profiles["default"] == "local"
    assert profiles["profiles"]["local"] == {
        "uri": "bolt://localhost:7687",
        "user": "neo4j",
        "password": "Password@123",
        "password_env": None,
        "database": "graphcheck-demo",
    }
    ci = profiles["profiles"]["ci"]
    assert ci["database"] == "neo4j"
    assert ci["password_env"] == "NEO4J_PASSWORD"
    assert ci.get("password") is None


def test_default_checks_are_baseline_free_and_include_fraud_ring_suite():
    suites = _load_suite_inputs(ROOT / "checks", [])
    discovered = [
        f"{suite.suite.suite}/{check.id}" for suite in suites for check in suite.suite.checks
    ]

    assert discovered == [
        "fraud-ring-conformance/account-no-orphans",
        "fraud-ring-conformance/account-owner-cardinality",
        "graphcheck-action-smoke/smoke-connection-alive",
    ]
    assert all(check.pattern.value != "drift" for suite in suites for check in suite.suite.checks)
    assert (ROOT / "examples" / "checks" / "example.yml").is_file()


def test_only_canonical_fixture_submodule_contains_seed_source():
    assert list(ROOT.rglob("seed.cypher")) == [
        ROOT
        / "vendor"
        / "graphcheck-fraud-ring-fixture"
        / "fixtures"
        / "fraud-ring"
        / "seed.cypher"
    ]
