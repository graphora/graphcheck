from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.connection_profiles import ConnectionProfile


@dataclass(frozen=True)
class Counts:
    nodes: int
    relationships: int


@dataclass(frozen=True)
class Visibility:
    can_connect: bool
    can_read: bool
    can_show_procedures: bool


@dataclass(frozen=True)
class DebugTrace:
    profile: str
    target: RunTarget
    visibility: Visibility
    counts: Counts

    def as_json(self) -> dict[str, object]:
        return {
            "ok": True,
            "profile": self.profile,
            "target": self.target.model_dump(),
            "visibility": {
                "can_connect": self.visibility.can_connect,
                "can_read": self.visibility.can_read,
                "can_show_procedures": self.visibility.can_show_procedures,
            },
            "counts": {"nodes": self.counts.nodes, "relationships": self.counts.relationships},
        }


class Neo4jClient:
    def __init__(self, profile: ConnectionProfile) -> None:
        try:
            import neo4j
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise GraphCheckError(
                "neo4j.driver_missing",
                "The Neo4j Python driver is not installed.",
                "Run `uv sync --group dev`, then run `graphcheck debug` again.",
            ) from exc
        self._neo4j = neo4j
        self._profile = profile
        self._driver = GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password))

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        try:
            self._driver.verify_connectivity()
        except Exception as exc:
            raise map_neo4j_error(exc) from exc

    def run_read(self, query: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        try:
            with self._driver.session(
                database=self._profile.database, default_access_mode=self._neo4j.READ_ACCESS
            ) as session:
                result = session.run(query, params or {})
                return [record.data() for record in result]
        except Exception as exc:
            raise map_neo4j_error(exc) from exc

    def explain_read(self, query: str, params: dict[str, object] | None = None) -> object:
        try:
            with self._driver.session(
                database=self._profile.database, default_access_mode=self._neo4j.READ_ACCESS
            ) as session:
                result = session.run(f"EXPLAIN {query}", params or {})
                return result.consume().plan
        except Exception as exc:
            raise map_neo4j_error(exc) from exc

    def probe(self) -> tuple[RunTarget, Visibility, Counts]:
        self.verify()
        version, edition = self._server_info()
        can_show_procedures = True
        apoc = False
        try:
            apoc = self._apoc_usable()
        except GraphCheckError as exc:
            if exc.error.code == "neo4j.permission_denied":
                can_show_procedures = False
            else:
                apoc = False
        counts = self._counts()
        target = RunTarget(
            database=self._profile.database,
            server_version=version,
            edition=edition,
            fingerprint=_fingerprint(self._profile.uri, self._profile.database, version),
            capabilities=Capabilities(apoc=apoc, count_store=self._count_store_usable()),
        )
        return target, Visibility(True, True, can_show_procedures), counts

    def _server_info(self) -> tuple[str, str]:
        rows = self.run_read(
            "CALL dbms.components() YIELD versions, edition "
            "RETURN versions[0] AS version, edition AS edition"
        )
        if not rows:
            raise GraphCheckError(
                "neo4j.query_failed",
                "Neo4j did not return server component metadata.",
                "Check that the configured user can execute dbms.components().",
            )
        return str(rows[0]["version"]), str(rows[0]["edition"]).lower()

    def _apoc_usable(self) -> bool:
        rows = self.run_read("CALL apoc.version() YIELD version RETURN version")
        return bool(rows)

    def _counts(self) -> Counts:
        nodes = self.run_read("MATCH (n) RETURN count(n) AS count")[0]["count"]
        relationships = self.run_read("MATCH ()-[r]->() RETURN count(r) AS count")[0]["count"]
        return Counts(nodes=int(nodes), relationships=int(relationships))

    def _count_store_usable(self) -> bool:
        try:
            plan = self.explain_read("MATCH (n) RETURN count(n) AS count")
        except GraphCheckError:
            return False
        return _plan_has_operator(plan, "NodeCountFromCountStore")


def debug_trace(profile_name: str, profile: ConnectionProfile) -> DebugTrace:
    client = Neo4jClient(profile)
    try:
        target, visibility, counts = client.probe()
        return DebugTrace(profile=profile_name, target=target, visibility=visibility, counts=counts)
    finally:
        client.close()


def error_json(profile_name: str, error: CheckError) -> dict[str, object]:
    return {"ok": False, "profile": profile_name, "error": error.model_dump()}


def _fingerprint(uri: str, database: str, version: str) -> str:
    raw = f"{uri}|{database}|{version}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _plan_has_operator(plan: object, operator: str) -> bool:
    if plan is None:
        return False
    if isinstance(plan, dict):
        current = plan.get("operator_type") or plan.get("operatorType") or plan.get("name")
        children = plan.get("children", [])
    else:
        current = getattr(plan, "operator_type", None) or getattr(plan, "operatorType", None)
        children = getattr(plan, "children", [])
    if current and operator in str(current):
        return True
    return any(_plan_has_operator(child, operator) for child in children or [])


def map_neo4j_error(exc: Exception) -> GraphCheckError:
    name = exc.__class__.__name__
    message = str(exc)
    if name in {"AuthError", "TokenExpired"}:
        return GraphCheckError(
            "neo4j.auth_failed",
            "Neo4j rejected the configured credentials.",
            "Edit profiles.yml with the password from Neo4j Desktop, then run `graphcheck debug`.",
        )
    if name in {"ServiceUnavailable", "SessionExpired"}:
        return GraphCheckError(
            "neo4j.unreachable",
            "Neo4j is unreachable at the configured Bolt URI.",
            "Start Neo4j Desktop, check the Bolt URI in profiles.yml, then run `graphcheck debug`.",
        )
    if "database" in message.lower() and "not found" in message.lower():
        return GraphCheckError(
            "neo4j.database_not_found",
            "The configured Neo4j database was not found.",
            "Update the database in profiles.yml, or create/start that database in Neo4j.",
        )
    if "permission" in message.lower() or "forbidden" in message.lower():
        return GraphCheckError(
            "neo4j.permission_denied",
            "Neo4j denied a read or probe query for the configured user.",
            "Grant read/procedure access to the user, then run `graphcheck debug`.",
        )
    return GraphCheckError(
        "neo4j.query_failed",
        f"Neo4j query failed: {message}",
        "Run `graphcheck debug --json` for the failing profile and check the configured database.",
    )
