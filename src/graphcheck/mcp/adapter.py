from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from graphcheck.packs.catalog import builtin_pack_catalog
from graphcheck.reporting import load_results


def list_checks() -> dict[str, list[dict[str, Any]]]:
    """
    Return all available GraphCheck checks as structured JSON.
    """

    catalog = builtin_pack_catalog()

    checks = []

    for check in catalog.checks.values():
        item = asdict(check)

        item["requires"] = list(item["requires"])
        item["evidence_elements"] = list(item["evidence_elements"])
        item["evidence_id_fields"] = list(item["evidence_id_fields"])

        checks.append(item)

    return {"checks": checks}


def get_results(path: str | Path) -> Any:
    """
    Load a GraphCheck results.json file.
    """
    return load_results(path)


def run_suite(
    suite: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Run a GraphCheck suite by invoking the existing CLI.
    """
    from graphcheck.cli import app

    runner = CliRunner()

    args = ["run"]

    if profile:
        args.extend(["--profile", profile])

    args.extend(["--suite", suite])

    result = runner.invoke(app, args)

    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "exception": str(result.exception) if result.exception else None,
    }