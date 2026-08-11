from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from graphcheck.contracts.results import RedactionPolicy, Results, parse_utc_timestamp
from graphcheck.reporting.writer import load_results

REDACTION_MASK = "[REDACTED]"


def _mask_values(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mask_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_mask_values(item) for item in value]
    return REDACTION_MASK


def redacted_run_id(finished_at: str) -> str:
    """Return a target-neutral identifier for a redacted artifact."""

    timestamp = parse_utc_timestamp(finished_at).strftime("%Y%m%dT%H%M%S%fZ")
    return f"redacted_{timestamp}"


def _mask_error(error: dict[str, Any] | None) -> None:
    if error is not None:
        error["message"] = REDACTION_MASK
        error["fix"] = REDACTION_MASK


def redact_results(data: Results | dict[str, Any] | str | Path) -> Results:
    """Return a validated mask-redacted copy of a results export."""

    source = load_results(data)
    payload = source.model_dump(mode="python", by_alias=True, exclude_none=False)
    payload["run"]["redaction"] = {"policy": RedactionPolicy.MASK, "applied": True}
    payload["run"]["id"] = redacted_run_id(payload["run"]["finished_at"])
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


def verify_redacted_results(data: Results | dict[str, Any] | str | Path) -> Results:
    """Validate that every contract-defined literal surface is masked."""

    results = load_results(data)
    if (
        results.run.redaction.policy is not RedactionPolicy.MASK
        or not results.run.redaction.applied
    ):
        raise ValueError("redaction verification failed: run.redaction is not applied mask mode")
    if results.run.id != redacted_run_id(results.run.finished_at):
        raise ValueError("redaction verification failed: run.id is not target-neutral")
    if results.run.partial_reason is not None:
        _verify_masked(results.run.partial_reason, "run.partial_reason")
    if results.run.error is not None:
        _verify_masked(results.run.error.message, "run.error.message")
        _verify_masked(results.run.error.fix, "run.error.fix")
    for index, check in enumerate(results.checks):
        prefix = f"checks[{index}]"
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
