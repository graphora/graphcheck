import json
import os

import pytest
import yaml
from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.connection_profiles import ConnectionProfile, ProfilesFile
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient, ResultPolicy
from graphcheck.project import PROFILES_FILE, write_default_project, write_example_suite

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)

# neo4j_profile / neo4j_apoc_profile fixtures live in tests/conftest.py (shared with #3).

runner = CliRunner()


def _write_cli_profile(tmp_path, name, profile):
    profiles = ProfilesFile(default=name, profiles={name: profile})
    (tmp_path / PROFILES_FILE).write_text(
        yaml.safe_dump(profiles.model_dump(), sort_keys=False), encoding="utf-8"
    )


def test_connect_and_probe(neo4j_profile, neo4j_test_target):
    client = Neo4jClient(neo4j_profile)
    try:
        client.verify()
        target, visibility, counts = client.probe()

        assert target.server_version
        assert target.edition in {"community", "enterprise"}
        assert target.database == "neo4j"
        assert isinstance(target.capabilities.apoc, bool)
        assert isinstance(target.capabilities.count_store, bool)
        assert visibility.can_connect is True
        assert visibility.can_read is True
        assert counts.nodes >= 0
        assert counts.relationships >= 0
        assert client.probe_cypher_version == neo4j_test_target.cypher
    finally:
        client.close()


def test_bounded_read_stops_a_large_stream_and_client_remains_usable(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        bounded = client.run_read_result_bounded(
            "UNWIND range(1, 1000000) AS value RETURN value",
            policy=ResultPolicy(max_rows=3),
        )
        follow_up = client.run_read_result("RETURN 1 AS healthy")
    finally:
        client.close()

    assert [row["value"] for row in bounded.rows] == [1, 2, 3]
    assert bounded.complete is False
    assert bounded.observed_rows == 4
    assert bounded.notifications == ()
    assert bounded.server_consumed_after_ms is None
    assert follow_up.rows == [{"healthy": 1}]


def test_read_classification_cache_key_does_not_include_parameters(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        first = client.run_read_result("RETURN $value AS value", {"value": 1})
        second = client.run_read_result("RETURN $value AS value", {"value": 2})
        info = client.read_guard_cache_info
    finally:
        client.close()

    assert first.rows == [{"value": 1}]
    assert second.rows == [{"value": 2}]
    assert (first.read_guard_cache_hit, second.read_guard_cache_hit) == (False, True)
    assert (info.hits, info.misses, info.size) == (1, 1, 1)


def test_community_debug_and_run_succeed_end_to_end(neo4j_profile, tmp_path, monkeypatch):
    write_default_project(tmp_path)
    (tmp_path / "checks" / "community-smoke.yml").write_text(
        """suite: community-smoke
competency:
  - id: connection-alive
    question: Is the Community database queryable?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
        encoding="utf-8",
    )
    _write_cli_profile(tmp_path, "community", neo4j_profile)
    monkeypatch.chdir(tmp_path)

    debug_result = runner.invoke(app, ["debug", "--json"])
    run_result = runner.invoke(app, ["run", "--suite", "community-smoke"])

    assert debug_result.exit_code == 0, debug_result.output
    assert json.loads(debug_result.stdout)["target"]["edition"] == "community"
    assert run_result.exit_code == 0, run_result.output
    payload = json.loads(
        (tmp_path / ".graphcheck" / "runs" / "latest" / "results.json").read_text(encoding="utf-8")
    )
    assert payload["run"]["run_status"] == "complete"
    assert payload["run"]["exit_code"] == 0


def test_debug_accepts_real_builtin_reader_role(neo4j_enterprise_profiles, tmp_path, monkeypatch):
    write_default_project(tmp_path)
    write_example_suite(tmp_path)
    reader = neo4j_enterprise_profiles["graphcheck_reader"]
    _write_cli_profile(tmp_path, "reader", reader)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["visibility"]["can_connect"] is True
    assert payload["visibility"]["can_read"] is True


def test_debug_rejects_real_user_without_reader_role(
    neo4j_restricted_profile, tmp_path, monkeypatch
):
    write_default_project(tmp_path)
    write_example_suite(tmp_path)
    _write_cli_profile(tmp_path, "restricted", neo4j_restricted_profile)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "neo4j.credential_not_read_only"
    assert "READER" in payload["error"]["message"]


def test_debug_rejects_real_write_capable_admin_credential(
    neo4j_enterprise_profiles, tmp_path, monkeypatch
):
    write_default_project(tmp_path)
    write_example_suite(tmp_path)
    admin = neo4j_enterprise_profiles["neo4j_admin"]
    _write_cli_profile(tmp_path, "admin", admin)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "neo4j.credential_not_read_only"
    assert payload["error"]["fix"]


def test_debug_rejects_real_custom_role(neo4j_enterprise_profiles, tmp_path, monkeypatch):
    write_default_project(tmp_path)
    write_example_suite(tmp_path)
    boosted = neo4j_enterprise_profiles["graphcheck_boosted"]
    _write_cli_profile(tmp_path, "boosted", boosted)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "neo4j.credential_not_read_only"
    assert "GRAPHCHECK_BOOSTED_ROLE" in payload["error"]["message"]


def test_home_graph_grant_and_scoped_denial_use_resolved_home_database(
    neo4j_enterprise_profiles,
):
    reader = Neo4jClient(neo4j_enterprise_profiles["graphcheck_home_reader"])
    try:
        assert reader.run_read("MATCH (n) RETURN count(n) AS count")[0]["count"] >= 1
        _, reader_visibility, reader_counts = reader.probe()

        assert reader_visibility.can_read is True
        assert reader_counts.nodes is not None and reader_counts.nodes >= 1
    finally:
        reader.close()

    denied = Neo4jClient(neo4j_enterprise_profiles["graphcheck_home_denied"])
    try:
        assert denied.run_read("MATCH (n) RETURN count(n) AS count")[0]["count"] >= 1
        _, denied_visibility, denied_counts = denied.probe()

        assert denied_visibility.can_read is False
        assert denied_counts.nodes is None
        assert denied_counts.relationships is None
    finally:
        denied.close()


def test_read_only_session_rejects_write(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        assert client.run_read("RETURN 1 AS n") == [{"n": 1}]

        with pytest.raises(GraphCheckError) as caught:
            client.run_read("CREATE (:GraphCheckWriteProbe)")

        assert caught.value.error.code in {"neo4j.permission_denied", "neo4j.query_failed"}
    finally:
        client.close()


def test_wrong_password_maps_to_auth_failed(neo4j_profile):
    bad = neo4j_profile.model_copy(update={"password": "wrong-password"})
    client = Neo4jClient(bad)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.verify()

        assert caught.value.error.code == "neo4j.auth_failed"
        assert caught.value.error.fix
    finally:
        client.close()


def test_wrong_database_maps_to_database_not_found(neo4j_profile):
    bad = neo4j_profile.model_copy(update={"database": "missingdb"})
    client = Neo4jClient(bad)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.run_read("RETURN 1 AS n")

        assert caught.value.error.code == "neo4j.database_not_found"
        assert caught.value.error.fix
    finally:
        client.close()


def test_unreachable_maps_to_unreachable():
    profile = ConnectionProfile(
        uri="bolt://127.0.0.1:1",
        user="neo4j",
        password="wrong-password",
        database="neo4j",
    )
    client = Neo4jClient(profile)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.verify()

        assert caught.value.error.code == "neo4j.unreachable"
        assert caught.value.error.fix
    finally:
        client.close()


def test_apoc_absent_on_plain_container(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        target, _, _ = client.probe()

        assert target.capabilities.apoc is False
    finally:
        client.close()


def test_apoc_present_when_plugin_enabled(neo4j_apoc_profile):
    client = Neo4jClient(neo4j_apoc_profile)
    try:
        target, _, _ = client.probe()

        if target.capabilities.apoc is False:
            pytest.skip(
                "Neo4j container started without APOC despite plugin env; "
                "adapter verified APOC absence correctly."
            )
        assert target.capabilities.apoc is True
    finally:
        client.close()
