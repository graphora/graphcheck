#!/usr/bin/env python3
"""Reads GraphCheck's results.json and writes a pass/fail/error rollup
to the GitHub Step Summary. Never raises: if results.json is missing or
unreadable, it writes a clear "no results" message instead.
"""
import json
import os
import sys

RESULTS_PATH = ".graphcheck/runs/latest/results.json"


def main():
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        print("GITHUB_STEP_SUMMARY not set; nothing to write.")
        return

    lines = []

    if not os.path.exists(RESULTS_PATH):
        lines.append("## GraphCheck results\n")
        lines.append(
            "No results were produced. The run likely failed before it "
            "could execute any checks (bad config, connection failure, "
            "or setup/artifact error).\n"
        )
        _write(summary_path, lines)
        return

    try:
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        lines.append("## GraphCheck results\n")
        lines.append(f"results.json exists but could not be read: {exc}\n")
        _write(summary_path, lines)
        return

    totals = data.get("totals", {})
    suites = data.get("suites", [])
    checks = data.get("checks", [])
    run = data.get("run", {})
    score = data.get("score", {})

    lines.append("## GraphCheck results\n")
    lines.append(
        f"**Run status:** `{run.get('status', 'unknown')}` "
        f"&nbsp;|&nbsp; **Exit code:** `{run.get('exit_code', 'unknown')}` "
        f"&nbsp;|&nbsp; **Score:** {score.get('value', 'n/a')}\n"
    )

    lines.append(
        f"**Totals:** {totals.get('checks', 0)} checks &mdash; "
        f"{totals.get('pass', 0)} passed, "
        f"{totals.get('fail', 0)} failed, "
        f"{totals.get('errored', 0)} errored, "
        f"{totals.get('warn', 0)} warned, "
        f"{totals.get('skipped', 0)} skipped\n"
    )

    if suites:
        lines.append("| Suite | Score | Pass | Fail | Errored | Warn | Skipped |")
        lines.append("|---|---|---|---|---|---|---|")
        for suite in suites:
            t = suite.get("totals", {})
            lines.append(
                f"| {suite.get('id', '?')} | {suite.get('score', '?')} | "
                f"{t.get('pass', 0)} | {t.get('fail', 0)} | "
                f"{t.get('errored', 0)} | {t.get('warn', 0)} | "
                f"{t.get('skipped', 0)} |"
            )
        lines.append("")

    failing = [c for c in checks if c.get("verdict") in ("fail", "errored")]
    if failing:
        lines.append("### Failing / errored checks\n")
        for c in failing:
            evidence_msg = None
            if c.get("evidence"):
                evidence_msg = c["evidence"].get("message")
            elif c.get("error"):
                evidence_msg = c["error"].get("message")
            evidence_msg = evidence_msg or "(no evidence message provided)"
            lines.append(
                f"- **{c.get('name', c.get('id', '?'))}** "
                f"(`{c.get('suite_id', '?')}`, verdict: `{c.get('verdict')}`) "
                f"&mdash; {evidence_msg}"
            )
        lines.append("")

    _write(summary_path, lines)


def _write(path, lines):
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()