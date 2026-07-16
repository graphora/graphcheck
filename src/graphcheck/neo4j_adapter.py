from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import neo4j
from neo4j import GraphDatabase

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.errors import GraphCheckError


@dataclass(frozen=True)
class Counts:
    nodes: int | None
    relationships: int | None


@dataclass(frozen=True)
class Visibility:
    can_connect: bool
    can_read: bool
    can_show_procedures: bool


@dataclass(frozen=True)
class BlockedCheck:
    suite: str
    check_id: str
    check: str
    missing_capability: str
    fix: str

    def as_json(self) -> dict[str, str]:
        return {
            "suite": self.suite,
            "check_id": self.check_id,
            "check": self.check,
            "missing_capability": self.missing_capability,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class DebugTrace:
    profile: str
    target: RunTarget
    visibility: Visibility
    counts: Counts
    blocked_checks: tuple[BlockedCheck, ...] = ()

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
            "blocked_checks": [blocked.as_json() for blocked in self.blocked_checks],
        }


class Neo4jClient:
    def __init__(self, profile: ConnectionProfile) -> None:
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
                database=self._profile.database, default_access_mode=neo4j.READ_ACCESS
            ) as session:
                result = session.run(query, params or {})
                return [record.data() for record in result]
        except Exception as exc:
            raise map_neo4j_error(exc) from exc

    def explain_read(self, query: str, params: dict[str, object] | None = None) -> object:
        try:
            with self._driver.session(
                database=self._profile.database, default_access_mode=neo4j.READ_ACCESS
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
            elif not _is_apoc_absent_error(exc):
                raise

        can_read = True
        try:
            can_read = self._can_read(edition)
        except GraphCheckError as exc:
            if exc.error.code == "neo4j.permission_denied":
                can_read = False
            else:
                raise

        counts = Counts(nodes=None, relationships=None)
        count_store = False
        if can_read:
            try:
                counts = self._counts()
            except GraphCheckError as exc:
                if exc.error.code == "neo4j.permission_denied":
                    can_read = False
                else:
                    raise
            else:
                count_store = self._count_store_usable()

        target = RunTarget(
            database=self._profile.database,
            server_version=version,
            edition=edition,
            fingerprint=_fingerprint(self._profile.uri, self._profile.database, version),
            capabilities=Capabilities(apoc=apoc, count_store=count_store),
        )
        return target, Visibility(True, can_read, can_show_procedures), counts

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
        try:
            rows = self.run_read("CALL apoc.version() YIELD version RETURN version")
        except GraphCheckError as exc:
            if not _is_apoc_absent_error(exc):
                raise
            rows = self.run_read(
                "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(*) AS count"
            )
            return bool(rows and int(rows[0]["count"]) > 0)
        return bool(rows)

    def _can_read(self, edition: str) -> bool:
        # Community Edition users have implied administrator privileges. Enterprise
        # graph security can instead hide all graph data and return an empty result,
        # so a successful MATCH is not sufficient evidence of read visibility.
        if edition != "enterprise":
            return True

        rows = self.run_read(
            "SHOW USER PRIVILEGES YIELD access, action, graph, resource, segment "
            "RETURN access, action, graph, resource, segment"
        )
        configured_database = self._profile.database.lower()
        home_database_names = (
            self._home_database_names()
            if any(str(row.get("graph", "")).upper() == "HOME" for row in rows)
            else set()
        )
        relevant = []
        for row in rows:
            graph = str(row.get("graph", "")).lower()
            if graph == "home":
                if configured_database not in home_database_names:
                    continue
            elif graph not in {"*", configured_database}:
                continue
            relevant.append(
                {
                    key: str(row.get(key, "")).upper()
                    for key in ("access", "action", "resource", "segment")
                }
            )

        if any(
            privilege["access"] == "DENIED" and privilege["action"] in {"MATCH", "READ", "TRAVERSE"}
            for privilege in relevant
        ):
            return False

        def has_full_grant(entity_segment: str) -> bool:
            full_segments = {entity_segment, "ELEMENT(*)", "ELEMENTS(*)"}

            def granted(action: str, resource: str) -> bool:
                return any(
                    privilege["access"] == "GRANTED"
                    and privilege["action"] == action
                    and privilege["resource"] == resource
                    and privilege["segment"] in full_segments
                    for privilege in relevant
                )

            return granted("MATCH", "ALL_PROPERTIES") or (
                granted("TRAVERSE", "GRAPH") and granted("READ", "ALL_PROPERTIES")
            )

        return has_full_grant("NODE(*)") and has_full_grant("RELATIONSHIP(*)")

    def _home_database_names(self) -> set[str]:
        rows = self.run_read("SHOW HOME DATABASE")
        if not rows:
            return set()

        names = {str(rows[0].get("name", "")).lower()}
        aliases = rows[0].get("aliases", [])
        if isinstance(aliases, list):
            names.update(str(alias).lower() for alias in aliases)
        names.discard("")
        return names

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


def init_trace(profile_name: str, profile: ConnectionProfile) -> DebugTrace:
    client = Neo4jClient(profile)
    try:
        target, visibility, counts = client.probe()
        return DebugTrace(profile=profile_name, target=target, visibility=visibility, counts=counts)
    finally:
        client.close()


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


def _is_apoc_absent_error(exc: GraphCheckError) -> bool:
    if exc.error.code != "neo4j.query_failed":
        return False
    message = exc.error.message.lower()
    return "apoc.version" in message and (
        "no procedure" in message
        or "procedure not found" in message
        or "unknown procedure" in message
        or "not registered" in message
    )


def map_neo4j_error(exc: Exception) -> GraphCheckError:
    name = exc.__class__.__name__
    message = str(exc)
    lowered = message.lower()
    neo4j_code = str(getattr(exc, "code", "")).lower()
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
    if "database" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return GraphCheckError(
            "neo4j.database_not_found",
            "The configured Neo4j database was not found.",
            "Update the database in profiles.yml, or create/start that database in Neo4j.",
        )
    if "security.forbidden" in neo4j_code or "permission" in lowered or "forbidden" in lowered:
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
