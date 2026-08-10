from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import neo4j
from neo4j import GraphDatabase

from graphcheck import __version__
from graphcheck.connection_profiles import ConnectionProfile, validate_profile_uri
from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError

READ_GUARD_CACHE_CAPACITY = 256
WRITE_PRIVILEGE_ACTIONS = frozenset(
    {
        "ALL GRAPH PRIVILEGES",
        "CREATE",
        "DELETE",
        "MERGE",
        "REMOVE LABEL",
        "SET LABEL",
        "SET PROPERTY",
        "WRITE",
    }
)
WRITE_CAPABLE_BUILTIN_ROLES = frozenset({"ADMIN", "ARCHITECT", "EDITOR", "PUBLISHER"})


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
class ProbeMetrics:
    round_trips: int
    elapsed_ms: int
    cache_hit: bool
    request_durations_ms: tuple[int, ...] = ()


@dataclass(frozen=True)
class SupportVersions:
    graphcheck: str
    neo4j_driver: str
    neo4j_server: str
    cypher: str


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
    probe_metrics: ProbeMetrics | None = None
    versions: SupportVersions | None = None

    def as_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
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
        if self.probe_metrics is not None:
            payload["probe"] = {
                "round_trips": self.probe_metrics.round_trips,
                "elapsed_ms": self.probe_metrics.elapsed_ms,
                "cache_hit": self.probe_metrics.cache_hit,
            }
        if self.versions is not None:
            payload["versions"] = {
                "graphcheck": self.versions.graphcheck,
                "neo4j_driver": self.versions.neo4j_driver,
                "neo4j_server": self.versions.neo4j_server,
                "cypher": self.versions.cypher,
            }
        return payload


@dataclass(frozen=True)
class QueryResult:
    """Rich read result for engine callers that need graph identity and query metadata."""

    rows: list[dict[str, Any]]
    columns: tuple[str, ...]
    notifications: tuple[dict[str, Any], ...]
    server_available_after_ms: int | None = None
    server_consumed_after_ms: int | None = None
    read_guard_ms: int | None = None
    read_guard_cache_hit: bool | None = None
    complete: bool = True
    observed_rows: int = 0
    limit: int | None = None


@dataclass(frozen=True)
class ResultPolicy:
    """Bound retained rows while making incomplete consumption explicit."""

    max_rows: int | None = None
    require_complete: bool = False

    def __post_init__(self) -> None:
        if self.max_rows is not None and (
            isinstance(self.max_rows, bool)
            or not isinstance(self.max_rows, int)
            or self.max_rows < 1
        ):
            raise ValueError("max_rows must be a positive integer or None")


@dataclass(frozen=True)
class ReadGuardCacheInfo:
    """Query-free, per-client read-classification cache metrics."""

    max_size: int
    size: int
    in_flight: int
    hits: int
    misses: int


class _ReadClassificationCache:
    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._entries: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._inflight: dict[tuple[str, str], threading.Event] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._closed = False

    def ensure_read(
        self,
        session: object,
        query: str,
        params: dict[str, object],
        *,
        database: str,
        deadline: float | None,
        attach_timeout: bool,
    ) -> bool:
        key = (database, query)
        while True:
            with self._lock:
                if key in self._entries:
                    self._entries.move_to_end(key)
                    self._hits += 1
                    return True
                if self._closed:
                    pending, owner = None, True
                    self._misses += 1
                else:
                    pending = self._inflight.get(key)
                    owner = pending is None
                    if owner:
                        pending = threading.Event()
                        self._inflight[key] = pending
                        self._misses += 1
            if owner:
                break
            assert pending is not None
            pending.wait(_remaining_timeout(deadline))

        try:
            _assert_server_classified_read(
                session,
                query,
                params,
                timeout_s=_remaining_timeout(deadline),
                attach_timeout=attach_timeout,
            )
        except BaseException:
            if pending is not None:
                with self._lock:
                    if self._inflight.get(key) is pending:
                        self._inflight.pop(key)
                    pending.set()
            raise
        if pending is not None:
            with self._lock:
                if not self._closed and self._inflight.get(key) is pending:
                    self._entries[key] = None
                    self._entries.move_to_end(key)
                    if len(self._entries) > self._max_size:
                        self._entries.popitem(last=False)
                    self._inflight.pop(key)
                pending.set()
        return False

    def info(self) -> ReadGuardCacheInfo:
        with self._lock:
            return ReadGuardCacheInfo(
                max_size=self._max_size,
                size=len(self._entries),
                in_flight=len(self._inflight),
                hits=self._hits,
                misses=self._misses,
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._entries.clear()
            pending = tuple(self._inflight.values())
            self._inflight.clear()
            self._hits = self._misses = 0
            for event in pending:
                event.set()


class _EarlyResultStop(Exception):
    def __init__(self, result: QueryResult) -> None:
        self.result = result


class Neo4jClient:
    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        max_concurrency: int = 1,
        read_guard_cache_capacity: int = READ_GUARD_CACHE_CAPACITY,
    ) -> None:
        validate_profile_uri(profile.uri)
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be a positive integer")
        if (
            isinstance(read_guard_cache_capacity, bool)
            or not isinstance(read_guard_cache_capacity, int)
            or read_guard_cache_capacity < 1
        ):
            raise ValueError("read_guard_cache_capacity must be a positive integer")
        self._profile = profile
        self._read_classifications = _ReadClassificationCache(read_guard_cache_capacity)
        self._probe_lock = threading.Lock()
        self._probe_inflight: threading.Event | None = None
        self._probe_result: tuple[RunTarget, Visibility, Counts] | None = None
        self._probe_request_durations_ms: list[int] | None = None
        self._last_probe_metrics: ProbeMetrics | None = None
        self._probe_cypher_version: str | None = None
        try:
            self._driver = GraphDatabase.driver(
                profile.uri,
                auth=(profile.user, profile.password),
                max_connection_pool_size=max_concurrency,
                connection_timeout=10.0,
                connection_acquisition_timeout=10.0,
                fetch_size=1000,
                max_transaction_retry_time=0.0,
            )
        except Exception as exc:
            raise map_neo4j_error(exc, profile) from exc

    def close(self) -> None:
        self._read_classifications.close()
        with self._probe_lock:
            self._probe_result = None
            self._probe_cypher_version = None
            if self._probe_inflight is not None:
                self._probe_inflight.set()
                self._probe_inflight = None
        self._driver.close()

    @property
    def read_guard_cache_info(self) -> ReadGuardCacheInfo:
        return self._read_classifications.info()

    @property
    def last_probe_metrics(self) -> ProbeMetrics | None:
        return self._last_probe_metrics

    @property
    def probe_cypher_version(self) -> str | None:
        return self._probe_cypher_version

    @contextmanager
    def read_transaction(self, *, timeout_s: float | None = None):
        """Yield a planner-verified reader whose queries share one read snapshot."""

        deadline = _timeout_deadline(timeout_s)
        try:
            with (
                self._driver.session(
                    database=self._profile.database,
                    default_access_mode=neo4j.READ_ACCESS,
                ) as session,
                session.begin_transaction(timeout=_remaining_timeout(deadline)) as transaction,
            ):
                yield _TransactionReader(
                    transaction,
                    self._profile.database,
                    deadline,
                    self._read_classifications,
                )
        except GraphCheckError:
            raise
        except Exception as exc:
            raise map_neo4j_error(exc, self._profile) from exc

    def verify(self) -> None:
        try:
            self._driver.verify_connectivity()
        except Exception as exc:
            raise map_neo4j_error(exc, self._profile) from exc

    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run a read and return the frozen SPEC-03 plain-row shape."""

        # This compatibility API is also used by C2's fixed metadata/probe statements. Neo4j
        # classifies some read-only DBMS procedures as query type ``s``, so planner classification
        # would reject the connector's own probes. Customer-authored C1 execution uses the rich,
        # planner-verified ``run_read_result`` path below.
        result = self._run_read_result(query, params, timeout_s=timeout_s, verify_read=False)
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
        """Run a planner-verified read while preserving graph values and metadata.

        ``READ_ACCESS`` is retained for correct cluster routing, but Neo4j documents that access
        mode alone is not an access-control boundary.  The server therefore plans the statement
        first and GraphCheck executes it only when Neo4j classifies it as read-only.
        """

        return self._run_read_result(query, params, timeout_s=timeout_s, verify_read=True)

    def run_read_result_bounded(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        policy: ResultPolicy,
        timeout_s: float | None = None,
        stop_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> QueryResult:
        """Run a planner-verified read without retaining more rows than ``policy`` allows."""

        return self._run_read_result(
            query,
            params,
            timeout_s=timeout_s,
            verify_read=True,
            policy=policy,
            stop_when=stop_when,
        )

    def _run_read_result(
        self,
        query: str,
        params: dict[str, object] | None,
        *,
        timeout_s: float | None,
        verify_read: bool,
        policy: ResultPolicy | None = None,
        stop_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> QueryResult:
        try:
            deadline = _timeout_deadline(timeout_s)
            with self._driver.session(
                database=self._profile.database, default_access_mode=neo4j.READ_ACCESS
            ) as session:
                values = params or {}
                read_guard_ms: int | None = None
                read_guard_cache_hit: bool | None = None
                if verify_read:
                    guard_started = time.monotonic()
                    read_guard_cache_hit = _ensure_server_classified_read(
                        session,
                        query,
                        values,
                        cache=self._read_classifications,
                        database=self._profile.database,
                        deadline=deadline,
                    )
                    read_guard_ms = max(0, round((time.monotonic() - guard_started) * 1000))
                driver_query = (
                    neo4j.Query(query, timeout=_remaining_timeout(deadline))
                    if timeout_s is not None
                    else query
                )
                result = session.run(driver_query, values)
                columns = _result_columns(result)
                rows: list[dict[str, Any]] = []
                observed_rows = 0
                complete = True
                for record in result:
                    observed_rows += 1
                    row = _raw_record(record)
                    limit_reached = (
                        policy is not None
                        and policy.max_rows is not None
                        and len(rows) >= policy.max_rows
                    )
                    if limit_reached:
                        complete = False
                        _cancel_result(result)
                        if policy.require_complete:
                            raise _result_limit_exceeded(policy.max_rows)
                        break
                    rows.append(row)
                    if stop_when is not None and stop_when(row):
                        complete = False
                        _cancel_result(result)
                        break
                if not columns and rows:
                    # Lightweight test doubles and third-party wrappers sometimes expose only an
                    # iterator. Real Neo4j Results always provide keys().
                    columns = tuple(rows[0])
                if not complete:
                    raise _EarlyResultStop(
                        QueryResult(
                            rows=rows,
                            columns=columns,
                            notifications=(),
                            read_guard_ms=read_guard_ms,
                            read_guard_cache_hit=read_guard_cache_hit,
                            complete=False,
                            observed_rows=observed_rows,
                            limit=policy.max_rows if policy is not None else None,
                        )
                    )
                consume = getattr(result, "consume", None)
                summary = consume() if complete and callable(consume) else None
                notifications = _summary_notifications(summary)
                _raise_for_missing_schema_reference(notifications)
                return QueryResult(
                    rows=rows,
                    columns=columns,
                    notifications=notifications,
                    server_available_after_ms=_summary_timing(summary, "result_available_after"),
                    server_consumed_after_ms=_summary_timing(summary, "result_consumed_after"),
                    read_guard_ms=read_guard_ms,
                    read_guard_cache_hit=read_guard_cache_hit,
                    complete=complete,
                    observed_rows=observed_rows,
                    limit=policy.max_rows if policy is not None else None,
                )
        except _EarlyResultStop as stopped:
            return stopped.result
        except GraphCheckError:
            raise
        except Exception as exc:
            raise map_neo4j_error(exc, self._profile) from exc

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
                text = _explain_query(query)
                driver_query = (
                    neo4j.Query(text, timeout=timeout_s) if timeout_s is not None else text
                )
                result = session.run(driver_query, params or {})
                return result.consume().plan
        except Exception as exc:
            raise map_neo4j_error(exc, self._profile) from exc

    def probe(self, *, timeout_s: float | None = None) -> tuple[RunTarget, Visibility, Counts]:
        deadline = _timeout_deadline(timeout_s)
        probe_lock = self._ensure_probe_state()
        while True:
            with probe_lock:
                if self._probe_result is not None:
                    self._last_probe_metrics = ProbeMetrics(0, 0, True)
                    return self._probe_result
                owner = self._probe_inflight is None
                if owner:
                    self._probe_inflight = threading.Event()
                pending = self._probe_inflight
            if owner:
                break
            assert pending is not None
            if not pending.wait(_remaining_timeout(deadline)):
                _remaining_timeout(deadline)

        started = time.monotonic()
        self._probe_request_durations_ms = []
        try:
            result = self._probe_live(deadline)
            metrics = ProbeMetrics(
                round_trips=len(self._probe_request_durations_ms),
                elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
                cache_hit=False,
                request_durations_ms=tuple(self._probe_request_durations_ms),
            )
            with probe_lock:
                self._probe_result = result
                self._last_probe_metrics = metrics
            return result
        finally:
            self._probe_request_durations_ms = None
            with probe_lock:
                pending = self._probe_inflight
                self._probe_inflight = None
                if pending is not None:
                    pending.set()

    def _ensure_probe_state(self) -> threading.Lock:
        if not hasattr(self, "_probe_lock"):
            self._probe_lock = threading.Lock()
            self._probe_inflight = None
            self._probe_result = None
            self._probe_request_durations_ms = None
            self._last_probe_metrics = None
            self._probe_cypher_version = None
        return self._probe_lock

    def _probe_live(self, deadline: float | None) -> tuple[RunTarget, Visibility, Counts]:
        # The first bounded metadata query establishes connectivity. Calling
        # verify_connectivity() here would add an unbounded Bolt round trip outside this deadline.
        version, edition = _call_with_timeout(self._server_info, deadline)
        _ensure_supported_server(version)
        self._probe_cypher_version = _call_with_timeout(
            lambda **kwargs: self._cypher_version(version, **kwargs), deadline
        )
        can_show_procedures = True
        apoc = False
        try:
            apoc = _call_with_timeout(self._apoc_usable, deadline)
        except GraphCheckError as exc:
            if exc.error.code == "neo4j.permission_denied":
                can_show_procedures = False
            elif not _is_apoc_absent_error(exc):
                raise

        can_read = True
        try:
            remaining = _remaining_timeout(deadline)
            can_read = (
                self._can_read(edition)
                if remaining is None
                else self._can_read(edition, timeout_s=remaining)
            )
        except GraphCheckError as exc:
            if exc.error.code == "neo4j.permission_denied":
                can_read = False
            else:
                raise

        counts = Counts(nodes=None, relationships=None)
        labels: tuple[str, ...] = ()
        relationship_types: tuple[str, ...] = ()
        count_store = False
        if can_read:
            try:
                counts = _call_with_timeout(self._counts, deadline)
            except GraphCheckError as exc:
                if exc.error.code == "neo4j.permission_denied":
                    can_read = False
                else:
                    raise
            else:
                labels, relationship_types = _call_with_timeout(self._schema_tokens, deadline)
                count_store = _call_with_timeout(self._count_store_usable, deadline)

        target = RunTarget(
            database=self._profile.database,
            server_version=version,
            edition=edition,
            fingerprint=_fingerprint(labels, relationship_types, counts),
            capabilities=Capabilities(apoc=apoc, count_store=count_store),
        )
        _remaining_timeout(deadline)
        return target, Visibility(True, can_read, can_show_procedures), counts

    def verify_read_only_credential(self, *, timeout_s: float | None = None) -> None:
        """Fail when Neo4j reports any granted graph-write privilege for this user."""

        try:
            rows = _run_read_with_timeout(
                self,
                "SHOW USER PRIVILEGES YIELD access, action, graph, role "
                "RETURN access, action, graph, role",
                timeout_s,
            )
        except GraphCheckError as exc:
            raise GraphCheckError(
                "neo4j.credential_read_only_unverified",
                "Neo4j could not return the configured user's reported privileges.",
                "Use Neo4j Enterprise with a native user that can inspect its own privileges and "
                "has only ACCESS and MATCH, then run `graphcheck debug` again.",
            ) from exc
        if not rows:
            raise GraphCheckError(
                "neo4j.credential_read_only_unverified",
                "Neo4j did not return the configured user's reported privileges.",
                "Allow the user to inspect its own privileges, or use a native Neo4j user with "
                "only ACCESS and MATCH, then run `graphcheck debug` again.",
            )
        granted_writes = sorted(
            {
                str(row.get("action", "")).replace("_", " ").upper()
                for row in rows
                if str(row.get("access", "")).upper() == "GRANTED"
                and str(row.get("action", "")).replace("_", " ").upper() in WRITE_PRIVILEGE_ACTIONS
            }
        )
        write_roles = sorted(
            {
                str(row.get("role", "")).upper()
                for row in rows
                if str(row.get("role", "")).upper() in WRITE_CAPABLE_BUILTIN_ROLES
            }
        )
        if granted_writes or write_roles:
            details = [*granted_writes, *(f"ROLE {role}" for role in write_roles)]
            raise GraphCheckError(
                "neo4j.credential_not_read_only",
                "The configured Neo4j credential has granted write-capable or administrative "
                "privileges "
                f"({', '.join(details)}) and is not server-enforced read-only.",
                "Create a dedicated Neo4j user with only ACCESS and MATCH (or READ/TRAVERSE), "
                "update `user` and its password in profiles.yml, then run `graphcheck debug` "
                "again.",
            )

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

    def _cypher_version(self, server_version: str, *, timeout_s: float | None = None) -> str:
        if not _supports_cypher_25(server_version):
            return "5"
        try:
            rows = _timed_probe_request(
                self,
                lambda: self.run_read(
                    "SHOW DATABASES YIELD name, defaultLanguage "
                    "WHERE name = $database RETURN defaultLanguage",
                    {"database": self._profile.database},
                    **({"timeout_s": timeout_s} if timeout_s is not None else {}),
                ),
            )
        except GraphCheckError as exc:
            if exc.error.code in {"neo4j.permission_denied", "neo4j.query_failed"}:
                return "unknown"
            raise
        value = str(rows[0].get("defaultLanguage", "")).upper() if rows else ""
        return "25" if value.endswith("25") else "5" if value.endswith("5") else "unknown"

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

    def _can_read(self, edition: str, *, timeout_s: float | None = None) -> bool:
        # Community Edition users have implied administrator privileges. Enterprise
        # graph security can instead hide all graph data and return an empty result,
        # so a successful MATCH is not sufficient evidence of read visibility.
        if edition != "enterprise":
            return True

        deadline = _timeout_deadline(timeout_s)
        rows = _run_read_with_timeout(
            self,
            "SHOW USER PRIVILEGES YIELD access, action, graph, resource, segment "
            "RETURN access, action, graph, resource, segment",
            _remaining_timeout(deadline),
        )
        configured_database = self._profile.database.lower()
        home_database_names = (
            self._home_database_names(timeout_s=_remaining_timeout(deadline))
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

    def _home_database_names(self, *, timeout_s: float | None = None) -> set[str]:
        rows = _run_read_with_timeout(self, "SHOW HOME DATABASE", timeout_s)
        if not rows:
            return set()

        names = {str(rows[0].get("name", "")).lower()}
        aliases = rows[0].get("aliases", [])
        if isinstance(aliases, list):
            names.update(str(alias).lower() for alias in aliases)
        names.discard("")
        return names

    def _counts(self, *, timeout_s: float | None = None) -> Counts:
        rows = _run_read_with_timeout(
            self,
            "CALL { MATCH (n) RETURN count(n) AS nodes } "
            "CALL { MATCH ()-[r]->() RETURN count(r) AS relationships } "
            "RETURN nodes, relationships",
            timeout_s,
        )
        return Counts(nodes=int(rows[0]["nodes"]), relationships=int(rows[0]["relationships"]))

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
            plan = _timed_probe_request(
                self,
                lambda: (
                    self.explain_read("MATCH (n) RETURN count(n) AS count")
                    if timeout_s is None
                    else self.explain_read(
                        "MATCH (n) RETURN count(n) AS count",
                        timeout_s=timeout_s,
                    )
                ),
            )
        except GraphCheckError:
            return False
        return _plan_has_operator(plan, "NodeCountFromCountStore")


class _TransactionReader:
    """Read-result facade over one explicit Neo4j transaction."""

    def __init__(
        self,
        transaction: object,
        database: str,
        deadline: float | None,
        read_classifications: _ReadClassificationCache,
    ) -> None:
        self._transaction = transaction
        self._database = database
        self._deadline = deadline
        self._read_classifications = read_classifications

    def run_read_result(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> QueryResult:
        values = params or {}
        local_deadline = _timeout_deadline(timeout_s)
        deadline = (
            local_deadline
            if self._deadline is None
            else self._deadline
            if local_deadline is None
            else min(self._deadline, local_deadline)
        )
        try:
            guard_started = time.monotonic()
            # Transaction.run rejects neo4j.Query objects; the transaction-level timeout was
            # already attached by begin_transaction(), so both EXPLAIN and execution stay strings.
            read_guard_cache_hit = _ensure_server_classified_read(
                self._transaction,
                query,
                values,
                cache=self._read_classifications,
                database=self._database,
                deadline=deadline,
                attach_timeout=False,
            )
            read_guard_ms = max(0, round((time.monotonic() - guard_started) * 1000))
            result = self._transaction.run(query, values)
            rows = [_raw_record(record) for record in result]
            columns = _result_columns(result) or (tuple(rows[0]) if rows else ())
            consume = getattr(result, "consume", None)
            summary = consume() if callable(consume) else None
            notifications = _summary_notifications(summary)
            _raise_for_missing_schema_reference(notifications)
            return QueryResult(
                rows=rows,
                columns=columns,
                notifications=notifications,
                server_available_after_ms=_summary_timing(summary, "result_available_after"),
                server_consumed_after_ms=_summary_timing(summary, "result_consumed_after"),
                read_guard_ms=read_guard_ms,
                read_guard_cache_hit=read_guard_cache_hit,
                observed_rows=len(rows),
            )
        except GraphCheckError:
            raise
        except Exception as exc:
            raise map_neo4j_error(exc) from exc


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
    return _timed_probe_request(
        client,
        lambda: (
            client.run_read(query)
            if timeout_s is None
            else client.run_read(query, timeout_s=timeout_s)
        ),
    )


def _timed_probe_request(client: Neo4jClient, operation: Callable[[], Any]) -> Any:
    durations = getattr(client, "_probe_request_durations_ms", None)
    if durations is None:
        return operation()
    started = time.monotonic()
    try:
        return operation()
    finally:
        durations.append(max(0, round((time.monotonic() - started) * 1000)))


def _supports_cypher_25(server_version: str) -> bool:
    try:
        year, month = (int(part) for part in server_version.split(".", 2)[:2])
    except (TypeError, ValueError):
        return False
    return (year, month) >= (2025, 6)


def _ensure_supported_server(server_version: str) -> None:
    try:
        major = int(server_version.split(".", 1)[0])
    except (AttributeError, ValueError):
        major = 0
    if major == 5 or major >= 2025:
        return
    raise GraphCheckError(
        "neo4j.unsupported_version",
        f"Neo4j Server {server_version} is outside GraphCheck's supported server lines.",
        "Upgrade to Neo4j Server 5.26 LTS or a documented calendar-version target.",
    )


def _support_versions(client: object, target: RunTarget) -> SupportVersions:
    return SupportVersions(
        graphcheck=__version__,
        neo4j_driver=str(getattr(neo4j, "__version__", "unknown")),
        neo4j_server=target.server_version,
        cypher=str(getattr(client, "probe_cypher_version", None) or "unknown"),
    )


def init_trace(profile_name: str, profile: ConnectionProfile) -> DebugTrace:
    client = Neo4jClient(profile)
    try:
        target, visibility, counts = client.probe()
        _verify_audit_credential(client)
        return DebugTrace(
            profile=profile_name,
            target=target,
            visibility=visibility,
            counts=counts,
            probe_metrics=getattr(client, "last_probe_metrics", None),
            versions=_support_versions(client, target),
        )
    finally:
        client.close()


def debug_trace(profile_name: str, profile: ConnectionProfile) -> DebugTrace:
    client = Neo4jClient(profile)
    try:
        target, visibility, counts = client.probe()
        _verify_audit_credential(client)
        return DebugTrace(
            profile=profile_name,
            target=target,
            visibility=visibility,
            counts=counts,
            probe_metrics=getattr(client, "last_probe_metrics", None),
            versions=_support_versions(client, target),
        )
    finally:
        client.close()


def _verify_audit_credential(client: object) -> None:
    verify = getattr(client, "verify_read_only_credential", None)
    if callable(verify):
        verify()


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


def _cancel_result(result: object) -> None:
    """Discard a partial stream without asking the driver to drain it."""

    cancel = getattr(result, "cancel", None) or getattr(result, "_cancel", None)
    if callable(cancel):
        cancel()


def _result_limit_exceeded(limit: int) -> GraphCheckError:
    return GraphCheckError(
        "engine.result_limit_exceeded",
        f"The query result exceeded the configured safety ceiling of {limit} rows.",
        "Narrow the query or increase engine.result_row_limit after reviewing its memory cost.",
    )


def _explain_query(query: str) -> str:
    stripped = query.lstrip()
    for prefix in ("CYPHER 5", "CYPHER 25"):
        if stripped == prefix:
            return f"{prefix} EXPLAIN"
        if stripped.startswith(f"{prefix}\n") or stripped.startswith(f"{prefix} "):
            return f"{prefix} EXPLAIN {stripped[len(prefix) :].lstrip()}"
    return f"EXPLAIN {query}"


def _assert_server_classified_read(
    session: object,
    query: str,
    params: dict[str, object],
    *,
    timeout_s: float | None,
    attach_timeout: bool = True,
) -> None:
    """Fail closed unless Neo4j's planner classifies the statement as read-only."""

    run = getattr(session, "run", None)
    if not callable(run):
        raise GraphCheckError(
            "neo4j.read_guard_unavailable",
            "The Neo4j session cannot perform the server-side read-only preflight.",
            "Use the supported Neo4j driver and a dedicated read-only database credential.",
        )
    text = _explain_query(query)
    driver_query = (
        neo4j.Query(text, timeout=timeout_s) if attach_timeout and timeout_s is not None else text
    )
    result = run(driver_query, params)
    consume = getattr(result, "consume", None)
    if not callable(consume):
        raise GraphCheckError(
            "neo4j.read_guard_unavailable",
            "Neo4j did not return a query summary for the read-only preflight.",
            "Use the supported Neo4j driver and a server version that reports query type.",
        )
    summary = consume()
    query_type = str(getattr(summary, "query_type", "")).lower()
    if query_type == "r":
        return
    if query_type in {"w", "rw", "s"}:
        raise GraphCheckError(
            "neo4j.write_rejected",
            "GraphCheck rejected a query that Neo4j classified as write-capable.",
            "Replace the query with read-only Cypher and use a credential without write "
            "privileges.",
        )
    raise GraphCheckError(
        "neo4j.read_guard_unavailable",
        f"Neo4j returned unknown query type {query_type!r} for the read-only preflight.",
        "Use a supported Neo4j server/driver and a dedicated read-only database credential.",
    )


def _ensure_server_classified_read(
    session: object,
    query: str,
    params: dict[str, object],
    *,
    cache: _ReadClassificationCache,
    database: str,
    deadline: float | None,
    attach_timeout: bool = True,
) -> bool:
    return cache.ensure_read(
        session,
        query,
        params,
        database=database,
        deadline=deadline,
        attach_timeout=attach_timeout,
    )


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
        # Driver 5.22+ exposes non-deprecated GQL status objects for both GQL-aware servers and
        # older servers whose notifications it polyfills. Only notification statuses belong in
        # this result field; success/no-data statuses are deliberately omitted.
        statuses = getattr(summary, "gql_status_objects", ())
        if isinstance(statuses, (list, tuple)):
            raw.extend(status for status in statuses if getattr(status, "is_notification", False))
    if not raw:
        # Compatibility with older drivers and the deliberately small fakes used by tests.
        for attribute in ("notifications", "summary_notifications"):
            value = getattr(summary, attribute, ())
            if isinstance(value, (list, tuple)):
                raw.extend(value)

    return tuple(
        notification for item in raw if (notification := _notification_dict(item)) is not None
    )


def _summary_timing(summary: object | None, name: str) -> int | None:
    value = getattr(summary, name, None) if summary is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return round(value)


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
    values: dict[str, Any] = {}
    for key, attributes in {
        "code": ("code", "gql_status"),
        "title": ("title",),
        "description": ("description", "status_description"),
        "severity": ("raw_severity", "severity_level"),
        "category": ("raw_classification", "category"),
        "position": ("position",),
    }.items():
        for attribute in attributes:
            value = getattr(notification, attribute, None)
            if value is not None:
                values[key] = value
                break
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
            "engine.schema_reference_missing",
            f"Neo4j query references a {kind} that is not present in the database: {detail}",
            f"Correct the {kind} in the check query, or create/populate it, then rerun.",
        )


def _missing_schema_kind(notification: Mapping[str, Any]) -> str | None:
    code = str(notification.get("code") or notification.get("neo4j_code") or "").lower()
    if code.endswith("unknownlabelwarning"):
        return "label"
    if code.endswith("unknownrelationshiptypewarning"):
        return "relationship type"
    if code.endswith("unknownpropertykeywarning"):
        return "property key"
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
    if "property" in text and (
        "unknown property" in text
        or "property key is not in the database" in text
        or "property key is not available" in text
    ):
        return "property key"
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


def map_neo4j_error(exc: Exception, profile: ConnectionProfile | None = None) -> GraphCheckError:
    name = exc.__class__.__name__
    causes: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        causes.extend((current.__class__.__name__, str(current)))
        current = current.__cause__ or current.__context__
    message = str(exc)
    lowered = " ".join(causes).lower()
    neo4j_code = str(getattr(exc, "code", "")).lower()
    if "transactiontimedout" in neo4j_code or ("transaction" in lowered and "timed out" in lowered):
        return GraphCheckTimeoutError(
            "neo4j.query_failed",
            "Neo4j timed out the read-only query before it completed.",
            "Narrow the check, enable sampling, or increase the run time budget.",
        )
    if name in {"AuthError", "TokenExpired"} or "security.unauthorized" in neo4j_code:
        return GraphCheckError(
            "neo4j.auth_failed",
            "Neo4j rejected the configured credentials.",
            "Update `user` and `password`/`password_env` in profiles.yml, then run "
            "`graphcheck debug` again.",
        )
    tls_tokens = ("ssl", "tls", "certificate", "encrypted connection", "handshake")
    connection_error = name in {"ServiceUnavailable", "SessionExpired"} or any(
        token in lowered for token in ("sslerror", "sslcertverificationerror", "boltsecurityerror")
    )
    if profile is not None and connection_error and any(token in lowered for token in tls_tokens):
        return GraphCheckError(
            "neo4j.tls_mismatch",
            "The Neo4j endpoint's TLS mode or certificate does not match the configured URI.",
            "Use `bolt://` for a direct non-TLS local server, `neo4j+s://` for CA-signed TLS, "
            "or `neo4j+ssc://` for an explicitly trusted self-signed endpoint; then run "
            "`graphcheck debug` again.",
        )
    if "database.databasenotfound" in neo4j_code or (
        "database" in lowered
        and ("not found" in lowered or "does not exist" in lowered or "unavailable" in lowered)
    ):
        database = profile.database if profile is not None else "configured"
        return GraphCheckError(
            "neo4j.database_not_found",
            f"Neo4j database {database!r} was not found or is unavailable.",
            "Set `database` in profiles.yml to an existing online database (often `neo4j`), "
            "or create/start that database, then run `graphcheck debug` again.",
        )
    if name in {"ServiceUnavailable", "SessionExpired"}:
        return GraphCheckError(
            "neo4j.unreachable",
            "Neo4j is unreachable at the configured Bolt URI.",
            "Start Neo4j, verify the host and port in `uri`, then run `graphcheck debug` again.",
        )
    if "security.forbidden" in neo4j_code or "permission" in lowered or "forbidden" in lowered:
        return GraphCheckError(
            "neo4j.permission_denied",
            "Neo4j denied a read or probe query for the configured user.",
            "Grant the dedicated user ACCESS plus MATCH (or READ/TRAVERSE) on the configured "
            "database, then run `graphcheck debug` again.",
        )
    return GraphCheckError(
        "neo4j.query_failed",
        f"Neo4j query failed: {message}",
        "Run `graphcheck debug --json` for the failing profile and check the configured database.",
    )
