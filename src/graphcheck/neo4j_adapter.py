from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping
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


@dataclass(frozen=True)
class QueryResult:
    """Rich read result for engine callers that need graph identity and query metadata."""

    rows: list[dict[str, Any]]
    columns: tuple[str, ...]
    notifications: tuple[dict[str, Any], ...]


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

    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run a read and return the frozen SPEC-03 plain-row shape."""

        result = self.run_read_result(query, params, timeout_s=timeout_s)
        # Record.data() is the legacy, opinionated conversion promised by SPEC-03. Rebuilding a
        # Record from each raw row preserves that behavior while the rich path keeps Node and
        # Relationship objects intact for C1 evidence extraction.
        return [neo4j.Record(row.items()).data() for row in result.rows]

    def run_read_result(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> QueryResult:
        """Run a read while preserving raw graph values, columns, and summary notifications."""

        try:
            with self._driver.session(
                database=self._profile.database, default_access_mode=neo4j.READ_ACCESS
            ) as session:
                driver_query = (
                    neo4j.Query(query, timeout=timeout_s) if timeout_s is not None else query
                )
                result = session.run(driver_query, params or {})
                columns = _result_columns(result)
                rows = [_raw_record(record) for record in result]
                if not columns and rows:
                    # Lightweight test doubles and third-party wrappers sometimes expose only an
                    # iterator. Real Neo4j Results always provide keys().
                    columns = tuple(rows[0])
                consume = getattr(result, "consume", None)
                summary = consume() if callable(consume) else None
                notifications = _summary_notifications(summary)
                _raise_for_missing_schema_reference(notifications)
                return QueryResult(rows=rows, columns=columns, notifications=notifications)
        except GraphCheckError:
            raise
        except Exception as exc:
            raise map_neo4j_error(exc) from exc

    def explain_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> object:
        try:
            with self._driver.session(
                database=self._profile.database, default_access_mode=neo4j.READ_ACCESS
            ) as session:
                text = f"EXPLAIN {query}"
                driver_query = (
                    neo4j.Query(text, timeout=timeout_s) if timeout_s is not None else text
                )
                result = session.run(driver_query, params or {})
                return result.consume().plan
        except Exception as exc:
            raise map_neo4j_error(exc) from exc

    def probe(self, *, timeout_s: float | None = None) -> tuple[RunTarget, Visibility, Counts]:
        deadline = _timeout_deadline(timeout_s)
        # The first bounded metadata query establishes connectivity. Calling
        # verify_connectivity() here would add an unbounded Bolt round trip outside this deadline.
        version, edition = _call_with_timeout(self._server_info, deadline)
        can_show_procedures = True
        apoc = False
        try:
            apoc = _call_with_timeout(self._apoc_usable, deadline)
        except GraphCheckError as exc:
            if exc.error.code == "neo4j.permission_denied":
                can_show_procedures = False
            elif not _is_apoc_absent_error(exc):
                raise
        counts = _call_with_timeout(self._counts, deadline)
        labels, relationship_types = _call_with_timeout(self._schema_tokens, deadline)

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

    def _server_info(self, *, timeout_s: float | None = None) -> tuple[str, str]:
        rows = _run_read_with_timeout(
            self,
            "CALL dbms.components() YIELD versions, edition "
            "RETURN versions[0] AS version, edition AS edition",
            timeout_s,
        )
        if not rows:
            raise GraphCheckError(
                "neo4j.query_failed",
                "Neo4j did not return server component metadata.",
                "Check that the configured user can execute dbms.components().",
            )
        return str(rows[0]["version"]), str(rows[0]["edition"]).lower()

    def _apoc_usable(self, *, timeout_s: float | None = None) -> bool:
        deadline = _timeout_deadline(timeout_s)
        try:
            rows = _run_read_with_timeout(
                self,
                "CALL apoc.version() YIELD version RETURN version",
                _remaining_timeout(deadline),
            )
        except GraphCheckError as exc:
            if not _is_apoc_absent_error(exc):
                raise
            rows = _run_read_with_timeout(
                self,
                "SHOW PROCEDURES YIELD name "
                "WHERE name STARTS WITH 'apoc.' RETURN count(*) AS count",
                _remaining_timeout(deadline),
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

    def _schema_tokens(
        self, *, timeout_s: float | None = None
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        rows = _run_read_with_timeout(
            self,
            "CALL { CALL db.labels() YIELD label RETURN collect(label) AS labels } "
            "CALL { CALL db.relationshipTypes() YIELD relationshipType "
            "       RETURN collect(relationshipType) AS relationship_types } "
            "RETURN labels, relationship_types",
            timeout_s,
        )
        if len(rows) != 1:
            raise GraphCheckError(
                "neo4j.query_failed",
                "Neo4j did not return a graph schema inventory for fingerprinting.",
                "Check that the configured user can call db.labels() and db.relationshipTypes().",
            )
        labels = rows[0].get("labels")
        relationship_types = rows[0].get("relationship_types")
        if not isinstance(labels, list) or not isinstance(relationship_types, list):
            raise GraphCheckError(
                "neo4j.query_failed",
                "Neo4j returned an invalid graph schema inventory for fingerprinting.",
                "Run `graphcheck debug --json` and verify schema procedure access.",
            )
        return (
            tuple(sorted({str(label) for label in labels})),
            tuple(sorted({str(rel_type) for rel_type in relationship_types})),
        )

    def _count_store_usable(self, *, timeout_s: float | None = None) -> bool:
        try:
            plan = (
                self.explain_read("MATCH (n) RETURN count(n) AS count")
                if timeout_s is None
                else self.explain_read(
                    "MATCH (n) RETURN count(n) AS count",
                    timeout_s=timeout_s,
                )
            )
        except GraphCheckError:
            return False
        return _plan_has_operator(plan, "NodeCountFromCountStore")


def _timeout_deadline(timeout_s: float | None) -> float | None:
    if timeout_s is None:
        return None
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise GraphCheckError(
            "engine.timeout",
            "The read-only connector time budget was exhausted.",
            "Narrow the operation or increase the run time budget.",
        )
    return time.monotonic() + float(timeout_s)


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GraphCheckError(
            "engine.timeout",
            "The read-only connector time budget was exhausted.",
            "Narrow the operation or increase the run time budget.",
        )
    return remaining


def _call_with_timeout(method: Any, deadline: float | None) -> Any:
    timeout_s = _remaining_timeout(deadline)
    return method() if timeout_s is None else method(timeout_s=timeout_s)


def _run_read_with_timeout(
    client: Neo4jClient,
    query: str,
    timeout_s: float | None,
) -> list[dict[str, Any]]:
    return (
        client.run_read(query) if timeout_s is None else client.run_read(query, timeout_s=timeout_s)
    )
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


def _result_columns(result: object) -> tuple[str, ...]:
    keys = getattr(result, "keys", None)
    if not callable(keys):
        return ()
    return tuple(str(key) for key in keys())


def _raw_record(record: object) -> dict[str, Any]:
    """Copy a record without Record.data()'s lossy graph-value conversion."""

    if isinstance(record, Mapping):
        return dict(record)
    items = getattr(record, "items", None)
    if callable(items):
        return dict(items())
    raise TypeError(f"query result record does not expose mapping items: {type(record).__name__}")


def _summary_notifications(summary: object | None) -> tuple[dict[str, Any], ...]:
    if summary is None:
        return ()

    metadata = getattr(summary, "metadata", None)
    raw: list[object] = []
    if isinstance(metadata, Mapping):
        if isinstance(metadata.get("notifications"), list):
            raw.extend(metadata["notifications"])
        if isinstance(metadata.get("statuses"), list):
            raw.extend(
                notification
                for status in metadata["statuses"]
                if (notification := _notification_from_status(status)) is not None
            )
    if not raw:
        # Compatibility with Neo4j 5.x summaries and the deliberately small fakes used by tests.
        for attribute in ("notifications", "summary_notifications"):
            value = getattr(summary, attribute, ())
            if isinstance(value, (list, tuple)):
                raw.extend(value)

    return tuple(
        notification for item in raw if (notification := _notification_dict(item)) is not None
    )


def _notification_from_status(status: object) -> dict[str, Any] | None:
    if not isinstance(status, Mapping) or "neo4j_code" not in status:
        return None
    notification = {key: status[key] for key in ("title", "description") if key in status}
    notification["code"] = status["neo4j_code"]
    diagnostic = status.get("diagnostic_record")
    if isinstance(diagnostic, Mapping):
        for notification_key, diagnostic_key in (
            ("severity", "_severity"),
            ("category", "_classification"),
            ("position", "_position"),
        ):
            if diagnostic_key in diagnostic:
                notification[notification_key] = diagnostic[diagnostic_key]
    return notification


def _notification_dict(notification: object) -> dict[str, Any] | None:
    if isinstance(notification, Mapping):
        return dict(notification)
    values = {
        key: value
        for key in (
            "code",
            "title",
            "description",
            "severity_level",
            "category",
            "position",
        )
        if (value := getattr(notification, key, None)) is not None
    }
    return values or None


def _raise_for_missing_schema_reference(notifications: tuple[dict[str, Any], ...]) -> None:
    for notification in notifications:
        kind = _missing_schema_kind(notification)
        if kind is None:
            continue
        detail = str(
            notification.get("description") or notification.get("title") or notification.get("code")
        )
        raise GraphCheckError(
            "neo4j.query_failed",
            f"Neo4j query references a {kind} that is not present in the database: {detail}",
            f"Correct the {kind} in the check query, or create/populate it, then rerun.",
        )


def _missing_schema_kind(notification: Mapping[str, Any]) -> str | None:
    code = str(notification.get("code") or notification.get("neo4j_code") or "").lower()
    if code.endswith("unknownlabelwarning"):
        return "label"
    if code.endswith("unknownrelationshiptypewarning"):
        return "relationship type"
    text = " ".join(str(notification.get(key) or "") for key in ("title", "description")).lower()
    if "label" in text and (
        "unknown label" in text
        or "label is not in the database" in text
        or "label is not available" in text
    ):
        return "label"
    if "relationship type" in text and (
        "unknown relationship type" in text
        or "relationship type is not in the database" in text
        or "relationship type is not available" in text
    ):
        return "relationship type"
    return None


def _fingerprint(
    labels: tuple[str, ...],
    relationship_types: tuple[str, ...],
    counts: Counts,
) -> str:
    """Hash graph structure and counts, never connection coordinates."""

    payload = {
        "labels": sorted(set(labels)),
        "relationship_types": sorted(set(relationship_types)),
        "node_count": counts.nodes,
        "relationship_count": counts.relationships,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


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
    if "transactiontimedout" in neo4j_code or ("transaction" in lowered and "timed out" in lowered):
        return GraphCheckError(
            "neo4j.query_failed",
            "Neo4j timed out the read-only query before it completed.",
            "Narrow the check, enable sampling, or increase the run time budget.",
        )
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
