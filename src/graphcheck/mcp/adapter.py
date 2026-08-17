from __future__ import annotations

from typing import Any

from graphcheck.application.paths import project_path
from graphcheck.application.run import (
    RunRequest,
    execute_run,
)
from graphcheck.application.suites import load_suite_inputs
from graphcheck.errors import GraphCheckError
from graphcheck.project import (
    find_project_root,
    load_project_config,
)
from graphcheck.reporting import load_results


def list_checks() -> dict[str, list[dict[str, Any]]]:
    """
    Return the project's configured suites and their checks as structured JSON.

    An agent discovers valid ``run_suite`` arguments from the ``suite`` values here. The
    suites are loaded and validated but never executed, so no database connection is made.
    """
    root = find_project_root()
    config = load_project_config(root)
    checks_dir = project_path(root, config.checks)

    suites = []
    for suite_input in load_suite_inputs(checks_dir, []):
        suite = suite_input.suite
        suites.append(
            {
                "suite": suite.suite,
                "checks": [
                    {
                        "id": check.id,
                        "kind": check.pattern.value,
                        "severity": check.severity.value,
                        "tags": list(check.tags),
                        "generated": check.generated,
                    }
                    for check in suite.checks
                ],
            }
        )

    return {"suites": suites}


def get_results(run_id: str = "latest") -> Any:
    """
    Load a GraphCheck results.json file safely.
    """
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise GraphCheckError(
            code="mcp.invalid_run_id",
            message="The supplied run ID is invalid.",
            fix="Provide a run ID returned by GraphCheck without path separators or '..'.",
        )

    root = find_project_root()
    config = load_project_config(root)
    artifacts = project_path(root, config.artifacts)
    runs_dir = (artifacts / "runs").resolve()

    results_path = (runs_dir / run_id / "results.json").resolve()

    try:
        results_path.relative_to(runs_dir)
    except ValueError as exc:
        raise GraphCheckError(
            code="mcp.invalid_run_id",
            message="The supplied run ID resolves outside the GraphCheck runs directory.",
            fix="Provide a valid GraphCheck run ID.",
        ) from exc

    # Translate every result-file failure into a stable, path-free GraphCheck error.
    # A syntactically valid but missing, unreadable, malformed, or contract-invalid
    # artifact must never surface a filesystem path to the MCP client.
    try:
        return load_results(results_path)
    except GraphCheckError:
        raise
    except FileNotFoundError as exc:
        raise GraphCheckError(
            code="mcp.results_not_found",
            message="No GraphCheck results exist for the supplied run ID.",
            fix="Run a suite to produce results, or supply a run ID from a completed run.",
        ) from exc
    except Exception as exc:
        raise GraphCheckError(
            code="mcp.results_unreadable",
            message="The GraphCheck results for the supplied run ID could not be read.",
            fix="Re-run the suite to regenerate the results, then try again.",
        ) from exc


def run_suite(
    suite: str,
    profile: str | None = None,
) -> Any:
    """
    Run a GraphCheck suite.
    """

    outcome = execute_run(
        RunRequest(
            profile=profile,
            suite_ids=[suite],
            tags=[],
            fail_fast=False,
            # Enforce the same read-only credential invariant the CLI requires, so an
            # Enterprise credential rejected by `graphcheck run` is also rejected here.
            verify_read_only_credential=True,
        )
    )

    # The run completed but its artifacts were not published, so `get_results` would not
    # find them. Surface this as a tool error instead of returning an apparently good run,
    # matching the CLI's exit-3 behaviour.
    if outcome.artifact_error is not None:
        raise GraphCheckError(
            code="mcp.artifact_write_failed",
            message="GraphCheck completed the run but could not publish its result artifacts.",
            fix=(
                "Check the configured artifacts path and filesystem permissions, "
                "then run the suite again."
            ),
        )

    return outcome.results
