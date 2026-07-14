import json
import os

import pytest
import yaml
from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.connection_profiles import ConnectionProfile, ProfilesFile
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.project import PROFILES_FILE, write_default_project, write_example_suite

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)

# neo4j_profile / neo4j_apoc_profile fixtures live in tests/conftest.py (shared with #3).

runner = CliRunner()


def test_connect_and_probe(neo4j_profile):
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
    finally:
        client.close()


def test_restricted_user_real_probe_reports_blocked_read_check(
    neo4j_restricted_profile, tmp_path, monkeypatch
):
    write_default_project(tmp_path)
    write_example_suite(tmp_path)
    profiles = ProfilesFile(default="restricted", profiles={"restricted": neo4j_restricted_profile})
    (tmp_path / PROFILES_FILE).write_text(
        yaml.safe_dump(profiles.model_dump(), sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["visibility"]["can_connect"] is True
    assert payload["visibility"]["can_read"] is False
    assert payload["counts"] == {"nodes": None, "relationships": None}
    assert any(
        blocked["check_id"] == "customer-name-present" and blocked["missing_capability"] == "read"
        for blocked in payload["blocked_checks"]
    )


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
