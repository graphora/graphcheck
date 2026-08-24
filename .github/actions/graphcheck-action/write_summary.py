#!/usr/bin/env python3
"""Write the Step Summary and emit bounded GitHub workflow annotations."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

ANNOTATION_LIMITS = {"error": 10, "warning": 10}
FINDING_VERDICTS = {"fail", "warn", "errored"}


def main() -> None:
    artifacts = os.environ.get("GRAPHCHECK_ARTIFACTS_DIR", ".graphcheck")
    results_path = Path(artifacts) / "runs" / "latest" / "results.json"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _write_if_configured(summary_path, _missing_results_lines())
        return
    except Exception as exc:
        _write_if_configured(summary_path, _unreadable_results_lines(exc))
        return

    checks = data.get("checks", []) if isinstance(data, Mapping) else []
    checks = checks if isinstance(checks, list) else []
    wanted_locations = {
        (str(check.get("suite_id", "?")), str(check.get("id", "?")))
        for check in checks
        if isinstance(check, Mapping) and check.get("verdict") in FINDING_VERDICTS
    }
    locations = _discover_check_locations(_workspace(), wanted_locations)
    annotation_counts = _emit_annotations(checks, locations)
    _write_if_configured(summary_path, _summary_lines(data, annotation_counts))


def _emit_annotations(
    checks: list[object], locations: Mapping[tuple[str, str], Mapping[str, object]]
) -> dict[str, Counter[str]]:
    emitted: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    for raw in checks:
        if not isinstance(raw, Mapping) or raw.get("verdict") not in FINDING_VERDICTS:
            continue
        level = "warning" if raw.get("severity") == "warn" else "error"
        if emitted[level] >= ANNOTATION_LIMITS[level]:
            dropped[level] += 1
            continue
        suite_id, check_id = str(raw.get("suite_id", "?")), str(raw.get("id", "?"))
        title = _clip(f"GraphCheck: {suite_id}/{check_id}", 255)
        location = _annotation_location(raw, locations.get((suite_id, check_id)))
        print(
            _workflow_command(
                level,
                title,
                _annotation_message(raw, suite_id, check_id),
                location,
            )
        )
        emitted[level] += 1
    if dropped.total():
        print(_truncation_message(emitted, dropped))
    return {"emitted": emitted, "dropped": dropped}


def _annotation_message(check: Mapping[str, object], suite_id: str, check_id: str) -> str:
    evidence = check.get("evidence")
    error = check.get("error")
    finding = (
        evidence.get("message")
        if isinstance(evidence, Mapping)
        else error.get("message")
        if isinstance(error, Mapping)
        else None
    )
    parts = [f"{suite_id}/{check_id}: {finding or '(no finding message provided)'}"]
    pointers = _evidence_pointers(evidence)
    if pointers:
        parts.append(pointers)
    if isinstance(error, Mapping) and error.get("fix"):
        parts.append(f"Fix: {error['fix']}")
    return _clip(" ".join(parts), 8_000)


def _evidence_pointers(evidence: object) -> str | None:
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("elements"), list):
        return None
    elements = [element for element in evidence["elements"] if isinstance(element, Mapping)]
    rendered = [_element_identity(element) for element in elements[:3]]
    rendered = [identity for identity in rendered if identity]
    if not rendered:
        return None
    suffix = f" (+{len(elements) - 3} more pointers)" if len(elements) > 3 else ""
    if evidence.get("truncated"):
        suffix += f" (evidence capped at {evidence.get('cap', len(elements))})"
    return f"Evidence: {'; '.join(rendered)}{suffix}."


def _element_identity(element: Mapping[str, object]) -> str | None:
    identity = element.get("id")
    if identity is None:
        return None
    kind = str(element.get("kind", "element"))
    detail = element.get("type")
    if detail is None and isinstance(element.get("labels"), list):
        detail = ":".join(str(label) for label in element["labels"])
    return f"{kind} {identity}{f' ({detail})' if detail else ''}"


def _annotation_location(
    check: Mapping[str, object], discovered: Mapping[str, object] | None
) -> dict[str, object] | None:
    evidence = check.get("evidence")
    candidates: list[object] = []
    if isinstance(evidence, Mapping):
        candidates.extend(evidence.get(key) for key in ("location", "source_location", "source"))
        elements = evidence.get("elements")
        if isinstance(elements, list):
            candidates.extend(
                element.get(key)
                for element in elements
                if isinstance(element, Mapping)
                for key in ("location", "source_location", "source")
            )
    candidates.extend(check.get(key) for key in ("location", "source_location", "source"))
    candidates.append(discovered)
    return next((location for item in candidates if (location := _normalize_location(item))), None)


def _normalize_location(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    raw_path = value.get("file", value.get("path"))
    raw_line = value.get("line", value.get("start_line"))
    if not isinstance(raw_path, str) or not raw_path.strip() or not _positive_integer(raw_line):
        return None
    path = _repository_path(raw_path)
    if path is None:
        return None
    location: dict[str, object] = {"file": path, "line": int(raw_line)}
    raw_column = value.get("column", value.get("col", value.get("start_column")))
    raw_end_line = value.get("end_line")
    raw_end_column = value.get("end_column")
    if _positive_integer(raw_column):
        location["col"] = int(raw_column)
    if _positive_integer(raw_end_line) and int(raw_end_line) >= int(raw_line):
        location["endLine"] = int(raw_end_line)
    if _positive_integer(raw_end_column) and int(raw_end_line or raw_line) == int(raw_line):
        location["endColumn"] = int(raw_end_column)
    return location


def _repository_path(value: str) -> str | None:
    workspace = _workspace()
    candidate = Path(value)
    candidate = (
        candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    )
    try:
        return candidate.relative_to(workspace).as_posix()
    except ValueError:
        return None


def _discover_check_locations(
    workspace: Path, wanted: set[tuple[str, str]] | None = None
) -> dict[tuple[str, str], dict[str, object]]:
    try:
        config_path = workspace / "graphcheck.yml"
        config = (
            yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        )
        checks_value = config.get("checks", "checks") if isinstance(config, Mapping) else "checks"
        if not isinstance(checks_value, str):
            return {}
        checks_root = (workspace / checks_value).resolve()
        checks_root.relative_to(workspace)
        paths = sorted((*checks_root.rglob("*.yml"), *checks_root.rglob("*.yaml")))
        locations: dict[tuple[str, str], dict[str, object]] = {}
        for path in paths:
            _add_suite_locations(locations, path, workspace)
            if wanted and wanted <= locations.keys():
                break
        return locations
    except Exception:
        return {}


def _add_suite_locations(
    locations: dict[tuple[str, str], dict[str, object]], path: Path, workspace: Path
) -> None:
    root = yaml.compose(path.read_text(encoding="utf-8"))
    if not isinstance(root, MappingNode):
        return
    suite_node = _mapping_node_value(root, "suite")
    suite_id = suite_node.value if isinstance(suite_node, ScalarNode) else path.stem
    relative_path = path.resolve().relative_to(workspace).as_posix()
    for section in ("conformance", "competency", "drift"):
        sequence = _mapping_node_value(root, section)
        if not isinstance(sequence, SequenceNode):
            continue
        for item in sequence.value:
            if not isinstance(item, MappingNode):
                continue
            id_node = _mapping_node_value(item, "id")
            if isinstance(id_node, ScalarNode):
                locations.setdefault(
                    (suite_id, id_node.value),
                    {
                        "file": relative_path,
                        "line": id_node.start_mark.line + 1,
                        "column": id_node.start_mark.column + 1,
                    },
                )


def _mapping_node_value(node: MappingNode, key: str):
    return next(
        (
            value
            for candidate, value in node.value
            if isinstance(candidate, ScalarNode) and candidate.value == key
        ),
        None,
    )


def _workflow_command(
    level: str, title: str, message: str, location: Mapping[str, object] | None
) -> str:
    properties = {**(dict(location) if location else {}), "title": title}
    rendered = ",".join(
        f"{key}={_command_property(str(value))}" for key, value in properties.items()
    )
    return f"::{level} {rendered}::{_command_value(message)}"


def _command_value(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _command_property(value: str) -> str:
    return _command_value(value).replace(":", "%3A").replace(",", "%2C")


def _summary_lines(data: Mapping[str, object], counts: Mapping[str, Counter[str]]) -> list[str]:
    totals = data.get("totals", {}) if isinstance(data.get("totals"), Mapping) else {}
    suites = data.get("suites", []) if isinstance(data.get("suites"), list) else []
    checks = data.get("checks", []) if isinstance(data.get("checks"), list) else []
    run = data.get("run", {}) if isinstance(data.get("run"), Mapping) else {}
    score = data.get("score") if isinstance(data.get("score"), Mapping) else {}
    lines = [
        "## GraphCheck results\n",
        f"**Run status:** `{run.get('status', 'unknown')}` &nbsp;|&nbsp; "
        f"**Exit code:** `{run.get('exit_code', 'unknown')}` &nbsp;|&nbsp; "
        f"**Score:** {score.get('value', 'n/a')}\n",
        f"**Totals:** {totals.get('checks', 0)} checks &mdash; {totals.get('pass', 0)} "
        f"passed, {totals.get('fail', 0)} failed, {totals.get('errored', 0)} errored, "
        f"{totals.get('warn', 0)} warned, {totals.get('skipped', 0)} skipped\n",
    ]
    if suites:
        lines.extend(
            (
                "| Suite | Score | Pass | Fail | Errored | Warn | Skipped |",
                "|---|---|---|---|---|---|---|",
            )
        )
        for suite in suites:
            if not isinstance(suite, Mapping):
                continue
            suite_totals = (
                suite.get("totals", {}) if isinstance(suite.get("totals"), Mapping) else {}
            )
            lines.append(
                f"| {suite.get('id', '?')} | {suite.get('score', '?')} | "
                f"{suite_totals.get('pass', 0)} | {suite_totals.get('fail', 0)} | "
                f"{suite_totals.get('errored', 0)} | {suite_totals.get('warn', 0)} | "
                f"{suite_totals.get('skipped', 0)} |"
            )
        lines.append("")
    findings = [
        check
        for check in checks
        if isinstance(check, Mapping) and check.get("verdict") in FINDING_VERDICTS
    ]
    if findings:
        lines.append("### Failing / warning / errored checks\n")
        for check in findings:
            evidence = check.get("evidence")
            error = check.get("error")
            message = (
                evidence.get("message")
                if isinstance(evidence, Mapping)
                else error.get("message")
                if isinstance(error, Mapping)
                else None
            )
            lines.append(
                f"- **{check.get('name', check.get('id', '?'))}** "
                f"(`{check.get('suite_id', '?')}/{check.get('id', '?')}`, "
                f"verdict: `{check.get('verdict')}`) "
                f"&mdash; {message or '(no evidence message provided)'}"
            )
        lines.append("")
    dropped = counts["dropped"]
    if dropped.total():
        lines.append(f"> {_truncation_message(counts['emitted'], dropped)}\n")
    return lines


def _truncation_message(emitted: Counter[str], dropped: Counter[str]) -> str:
    return (
        f"GraphCheck annotations truncated: emitted {emitted['error']} errors and "
        f"{emitted['warning']} warnings; dropped {dropped.total()} "
        f"({dropped['error']} errors, {dropped['warning']} warnings) because GitHub Actions "
        "permits 10 error and 10 warning annotations per step."
    )


def _missing_results_lines() -> list[str]:
    return [
        "## GraphCheck results\n",
        "No results were produced. The run likely failed before it could execute any checks "
        "(bad config, connection failure, or setup/artifact error).\n",
    ]


def _unreadable_results_lines(exc: Exception) -> list[str]:
    return ["## GraphCheck results\n", f"results.json exists but could not be read: {exc}\n"]


def _write_if_configured(path: str | None, lines: list[str]) -> None:
    if not path:
        print("GITHUB_STEP_SUMMARY not set; annotations were still processed.")
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def _workspace() -> Path:
    return Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


if __name__ == "__main__":
    main()
