"""Regenerate derived benchmark evidence from committed JSON; use --check in CI."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "graph-size-benchmark-results"
FIELDS = (
    "run",
    "nodes",
    "relationships",
    "check",
    "pack",
    "duration_ms",
    "verdict",
    "sampled",
    "sample_size",
    "population",
    "error_code",
    "skip_reason",
)
COMPLETED = {"pass", "fail", "warn"}


def derived_artifacts(directory: Path = RESULTS) -> dict[Path, str]:
    records = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*-run*.json"))
    ]
    if not records:
        raise ValueError(f"No benchmark measurements found in {directory}")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    totals = [
        "| Raw run | Nodes | Engine seconds | Process seconds | Python peak MiB | "
        "Neo4j observed peak MiB | Outcome |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    successful_sizes, failures = [], []
    for path, record in records:
        if record["execution_status"] != "measured":
            raise ValueError(f"{path.name} has no completed measurement to summarize")
        checks = record["results"]["checks"]
        completed = sum(check["verdict"] in COMPLETED for check in checks)
        errored = sum(check["verdict"] == "errored" for check in checks)
        skipped = sum(check["verdict"] == "skipped" for check in checks)
        if completed and all(
            check["verdict"] in COMPLETED
            or (check["id"] == "dangling_rels" and check["skip_reason"] == "unsupported")
            for check in checks
        ):
            successful_sizes.append(record["counts"]["nodes"])
        server_peak = record.get("server_peak_rss_bytes")
        server = "Not collected" if server_peak is None else f"{server_peak / 1048576:.2f}"
        totals.append(
            f"| [{path.stem}]({path.name}) | {record['counts']['nodes']:,} | "
            f"{record['engine_wall_ms'] / 1000:.3f} | {record['process_wall_ms'] / 1000:.3f} | "
            f"{record['client_peak_rss_bytes'] / 1048576:.2f} | {server} | "
            f"{completed} completed; {errored} errored; {skipped} skipped |"
        )
        for check in checks:
            estimate, measured = check["estimate"], check["measured"] or {}
            writer.writerow(
                {
                    "run": path.name,
                    **record["counts"],
                    "check": check["id"],
                    "pack": "pii" if check["id"].startswith("pii_") else "core",
                    "duration_ms": check["duration_ms"],
                    "verdict": check["verdict"],
                    "sampled": estimate is not False,
                    "sample_size": estimate["sample_size"]
                    if estimate
                    else measured.get("sample_size"),
                    "population": measured.get("population"),
                    "error_code": (check["error"] or {}).get("code"),
                    "skip_reason": check["skip_reason"],
                }
            )
            if check["error"]:
                failures.append(
                    f"- [{path.stem}]({path.name}): `{check['id']}` errored with "
                    f"`{check['error']['code']}`."
                )
    summary = (
        f"The largest recorded size where all executable checks completed is "
        f"**{max(successful_sizes):,} nodes**."
        if successful_sizes
        else "No recorded size completed all executable checks."
    )
    summary += " Findings (`fail`/`warn`) count as completed evaluations; execution errors do not."
    summary += "\n\n" + ("\n".join(failures) if failures else "No execution errors were recorded.")
    summary += "\n\nAn errored run's wall time is time to return, not a successful audit runtime."
    largest = max((record for _, record in records), key=lambda record: record["counts"]["nodes"])
    core = [
        check
        for check in largest["results"]["checks"]
        if not check["id"].startswith("pii_") and check["verdict"] != "skipped"
    ]
    summary = (
        f"At **{largest['counts']['nodes']:,} nodes**, "
        f"**{sum(check['verdict'] == 'pass' for check in core)} of {len(core)} attempted core "
        f"checks passed**. Skipped checks and PII outcomes are reported separately below.\n\n"
        + summary
    )
    by_id = [{check["id"]: check for check in record["results"]["checks"]} for _, record in records]
    if any(set(checks) != set(by_id[0]) for checks in by_id):
        raise ValueError("Recorded runs selected different checks")
    timings = [
        "| Check | " + " | ".join(path.stem for path, _ in records) + " | Last run outcome |",
        "| --- | " + " | ".join("---:" for _ in records) + " | --- |",
    ]
    for name in by_id[0]:
        durations = [
            "not executed"
            if checks[name]["duration_ms"] is None
            else f"{checks[name]['duration_ms'] / 1000:.3f}"
            for checks in by_id
        ]
        timings.append(f"| `{name}` | {' | '.join(durations)} | {by_id[-1][name]['verdict']} |")
    report_path = directory / "REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    for name, content in {
        "summary": summary,
        "totals": "\n".join(totals),
        "checks": "\n".join(timings),
    }.items():
        start, end = f"<!-- BEGIN GENERATED: {name} -->", f"<!-- END GENERATED: {name} -->"
        report, count = re.subn(
            re.escape(start) + r".*?" + re.escape(end),
            lambda _, start=start, end=end, content=content: f"{start}\n{content}\n{end}",
            report,
            flags=re.DOTALL,
        )
        if count != 1:
            raise ValueError(f"Expected exactly one generated {name} block in {report_path}")
    return {directory / "checks.csv": output.getvalue(), report_path: report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in derived_artifacts().items():
        if args.check:
            if path.read_text(encoding="utf-8") != content:
                stale.append(path.name)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        print("Stale benchmark artifacts: " + ", ".join(stale))
        print("Fix: run python tools/summarize_graph_size.py")
    return int(bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
