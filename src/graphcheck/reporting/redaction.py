from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from graphcheck.contracts.results import RedactionPolicy, Results, parse_utc_timestamp
from graphcheck.reporting.writer import load_results

REDACTION_MASK = "[REDACTED]"
_ALIAS_PATTERN = re.compile(r"(?:suite|check|tag|label|relationship-type)-[1-9][0-9]*\Z")


def _mask_values(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mask_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_mask_values(item) for item in value]
    return REDACTION_MASK


def _string_literals(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set().union(*(_string_literals(item) for item in value.values()), set())
    if isinstance(value, (list, tuple, set, frozenset)):
        return set().union(*(_string_literals(item) for item in value), set())
    return {value} if isinstance(value, str) and value else set()


def _sensitive_source_literals(payload: dict[str, Any]) -> set[str]:
    run = payload["run"]
    values: list[object] = [
        run["partial_reason"],
        run["selection"]["suites"],
        run["selection"]["tags"],
        run["error"],
    ]
    if run["target"] is not None:
        values.extend(
            (
                run["target"]["database"],
                run["target"]["fingerprint"],
                run["target"]["labels"],
                run["target"]["relationship_types"],
            )
        )
    values.extend((suite["id"], suite["source_sha"]) for suite in payload["suites"])
    for check in payload["checks"]:
        values.extend(
            (
                check["id"],
                check["suite_id"],
                check["name"],
                check["provenance"],
                check["compiled_query"],
                check["params"],
                check["measured"],
                check["expected"],
                check["evidence"],
                check["error"],
            )
        )
    return _string_literals(values)


def _aliases(values: list[str], prefix: str, sensitive: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    candidate = 1
    for value in values:
        if value in aliases:
            continue
        while f"{prefix}-{candidate}" in sensitive:
            candidate += 1
        aliases[value] = f"{prefix}-{candidate}"
        candidate += 1
    return aliases


def _alias_identifiers(payload: dict[str, Any], sensitive: set[str]) -> None:
    selection = payload["run"]["selection"]
    suite_values = [
        *selection["suites"],
        *(suite["id"] for suite in payload["suites"]),
        *(check["suite_id"] for check in payload["checks"]),
    ]
    suite_aliases = _aliases(suite_values, "suite", sensitive)
    tag_aliases = _aliases(selection["tags"], "tag", sensitive)
    selection["suites"] = [suite_aliases[value] for value in selection["suites"]]
    selection["tags"] = [tag_aliases[value] for value in selection["tags"]]
    for suite in payload["suites"]:
        suite["id"] = suite_aliases[suite["id"]]
        suite["source_sha"] = REDACTION_MASK
    check_aliases = _aliases([check["id"] for check in payload["checks"]], "check", sensitive)
    for check in payload["checks"]:
        check["suite_id"] = suite_aliases[check["suite_id"]]
        check["id"] = check_aliases[check["id"]]
    if target := payload["run"]["target"]:
        target["database"] = REDACTION_MASK
        target["fingerprint"] = REDACTION_MASK
        if target["labels"] is not None:
            target["labels"] = list(_aliases(target["labels"], "label", sensitive).values())
        if target["relationship_types"] is not None:
            target["relationship_types"] = list(
                _aliases(target["relationship_types"], "relationship-type", sensitive).values()
            )


def _is_safe_literal_path(path: tuple[str | int, ...]) -> bool:
    if path in {
        ("schema_version",),
        ("run", "started_at"),
        ("run", "finished_at"),
        ("run", "graphcheck_version"),
        ("run", "pack_version"),
        ("run", "run_status"),
        ("run", "redaction", "policy"),
        ("run", "target", "server_version"),
        ("run", "target", "edition"),
        ("run", "error", "code"),
        ("score", "method"),
    }:
        return True
    if (
        len(path) == 3
        and path[0] == "checks"
        and path[2]
        in {
            "pattern",
            "severity",
            "verdict",
            "skip_reason",
            "started_at",
        }
    ):
        return True
    if len(path) == 4 and path[0] == "checks" and path[2:] == ("error", "code"):
        return True
    return (
        len(path) == 6
        and path[0] == "checks"
        and isinstance(path[1], int)
        and path[2:4] == ("evidence", "elements")
        and isinstance(path[4], int)
        and path[5] == "kind"
    )


def _verify_no_sensitive_literals(
    value: object,
    sensitive: set[str],
    path: tuple[str | int, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _verify_no_sensitive_literals(item, sensitive, (*path, str(key)))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _verify_no_sensitive_literals(item, sensitive, (*path, index))
        return
    if (
        isinstance(value, str)
        and value != REDACTION_MASK
        and value in sensitive
        and not _is_safe_literal_path(path)
    ):
        location = "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in path)
        raise ValueError(
            f"redaction verification failed: sensitive source literal at {location.lstrip('.')}"
        )


def redacted_run_id(finished_at: str, sensitive: set[str] | None = None) -> str:
    """Return a target-neutral identifier for a redacted artifact."""

    sensitive = sensitive or set()
    timestamp = parse_utc_timestamp(finished_at).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = f"redacted_{timestamp}"
    if candidate not in sensitive:
        return candidate
    collision = 1
    while f"redacted_collision{collision}_{timestamp}" in sensitive:
        collision += 1
    return f"redacted_collision{collision}_{timestamp}"


def _is_redacted_run_id(value: str, finished_at: str) -> bool:
    timestamp = parse_utc_timestamp(finished_at).strftime("%Y%m%dT%H%M%S%fZ")
    return value == f"redacted_{timestamp}" or bool(
        re.fullmatch(rf"redacted_collision[1-9][0-9]*_{timestamp}", value)
    )


def _mask_error(error: dict[str, Any] | None) -> None:
    if error is not None:
        error["message"] = REDACTION_MASK
        error["fix"] = REDACTION_MASK


def redact_results(data: Results | dict[str, Any] | str | Path) -> Results:
    """Return a validated mask-redacted copy of a results export."""

    source = load_results(data)
    payload = source.model_dump(mode="python", by_alias=True, exclude_none=False)
    sensitive = _sensitive_source_literals(payload)
    payload["run"]["redaction"] = {"policy": RedactionPolicy.MASK, "applied": True}
    payload["run"]["id"] = redacted_run_id(payload["run"]["finished_at"], sensitive)
    _alias_identifiers(payload, sensitive)
    if payload["run"]["partial_reason"] is not None:
        payload["run"]["partial_reason"] = REDACTION_MASK
    _mask_error(payload["run"]["error"])
    for check in payload["checks"]:
        check["name"] = REDACTION_MASK
        if check["provenance"] is not None:
            check["provenance"] = REDACTION_MASK
        if check["compiled_query"] is not None:
            check["compiled_query"] = REDACTION_MASK
        if check["params"] is not None:
            check["params"] = _mask_values(check["params"])
        if check["measured"] is not None:
            check["measured"] = _mask_values(check["measured"])
        check["expected"] = _mask_values(check["expected"])
        if evidence := check["evidence"]:
            evidence["message"] = REDACTION_MASK
            for element in evidence["elements"]:
                element["id"] = REDACTION_MASK
                if element["labels"] is not None:
                    element["labels"] = [REDACTION_MASK for _ in element["labels"]]
                if element["type"] is not None:
                    element["type"] = REDACTION_MASK
        _mask_error(check["error"])
    redacted = Results.model_validate(payload)
    verify_redacted_results(redacted)
    _verify_no_sensitive_literals(
        redacted.model_dump(mode="python", by_alias=True, exclude_none=False), sensitive
    )
    return redacted


def _verify_masked(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _verify_masked(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _verify_masked(item, f"{path}[{index}]")
        return
    if value != REDACTION_MASK:
        raise ValueError(f"redaction verification failed: unmasked value at {path}")


def _verify_alias(value: str, prefix: str, path: str) -> None:
    if not _ALIAS_PATTERN.fullmatch(value) or not value.startswith(f"{prefix}-"):
        raise ValueError(f"redaction verification failed: unmasked identifier at {path}")


def verify_redacted_results(data: Results | dict[str, Any] | str | Path) -> Results:
    """Validate that every contract-defined literal surface is masked."""

    results = load_results(data)
    if (
        results.run.redaction.policy is not RedactionPolicy.MASK
        or not results.run.redaction.applied
    ):
        raise ValueError("redaction verification failed: run.redaction is not applied mask mode")
    if not _is_redacted_run_id(results.run.id, results.run.finished_at):
        raise ValueError("redaction verification failed: run.id is not target-neutral")
    if results.run.partial_reason is not None:
        _verify_masked(results.run.partial_reason, "run.partial_reason")
    if results.run.error is not None:
        _verify_masked(results.run.error.message, "run.error.message")
        _verify_masked(results.run.error.fix, "run.error.fix")
    for index, suite_id in enumerate(results.run.selection.suites):
        _verify_alias(suite_id, "suite", f"run.selection.suites[{index}]")
    for index, tag in enumerate(results.run.selection.tags):
        _verify_alias(tag, "tag", f"run.selection.tags[{index}]")
    if results.run.target is not None:
        _verify_masked(results.run.target.database, "run.target.database")
        _verify_masked(results.run.target.fingerprint, "run.target.fingerprint")
        if results.run.target.labels is not None:
            for index, label in enumerate(results.run.target.labels):
                _verify_alias(label, "label", f"run.target.labels[{index}]")
        if results.run.target.relationship_types is not None:
            for index, rel_type in enumerate(results.run.target.relationship_types):
                _verify_alias(
                    rel_type,
                    "relationship-type",
                    f"run.target.relationship_types[{index}]",
                )
    for index, suite in enumerate(results.suites):
        _verify_alias(suite.id, "suite", f"suites[{index}].id")
        _verify_masked(suite.source_sha, f"suites[{index}].source_sha")
    for index, check in enumerate(results.checks):
        prefix = f"checks[{index}]"
        _verify_alias(check.id, "check", f"{prefix}.id")
        _verify_alias(check.suite_id, "suite", f"{prefix}.suite_id")
        _verify_masked(check.name, f"{prefix}.name")
        if check.provenance is not None:
            _verify_masked(check.provenance, f"{prefix}.provenance")
        if check.compiled_query is not None:
            _verify_masked(check.compiled_query, f"{prefix}.compiled_query")
        if check.params is not None:
            _verify_masked(check.params, f"{prefix}.params")
        if check.measured is not None:
            _verify_masked(check.measured, f"{prefix}.measured")
        _verify_masked(check.expected, f"{prefix}.expected")
        if check.evidence is not None:
            _verify_masked(check.evidence.message, f"{prefix}.evidence.message")
            for element_index, element in enumerate(check.evidence.elements):
                element_prefix = f"{prefix}.evidence.elements[{element_index}]"
                _verify_masked(element.id, f"{element_prefix}.id")
                if element.labels is not None:
                    _verify_masked(element.labels, f"{element_prefix}.labels")
                if element.type is not None:
                    _verify_masked(element.type, f"{element_prefix}.type")
        if check.error is not None:
            _verify_masked(check.error.message, f"{prefix}.error.message")
            _verify_masked(check.error.fix, f"{prefix}.error.fix")
    return results
