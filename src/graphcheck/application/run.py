from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from graphcheck.application.paths import project_path
from graphcheck.application.suites import load_suite_inputs
from graphcheck.application.artifacts import write_run_artifacts

from graphcheck.baselines import DirectoryBaselineProvider
from graphcheck.contracts.results import CheckError
from graphcheck.errors import GraphCheckError
from graphcheck.contracts.results import Results, failed_results
from graphcheck.engine import Engine
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.project import (
    find_project_root,
    load_project_config,
    load_profiles,
    select_profile,
)


@dataclass(slots=True)
class RunRequest:
    profile: str | None
    suite_ids: list[str]
    tags: list[str]
    fail_fast: bool


@dataclass(slots=True)
class RunOutcome:
    results: Results
    results_path: Path
    report_path: Path



def execute_run(
    request: RunRequest,
) -> RunOutcome:
    """
    Execute a GraphCheck run independently of the CLI or MCP.
    """
    root = find_project_root()
    config = load_project_config(root)
    artifacts = project_path(root, config.artifacts)
    runs_dir = artifacts / "runs"

    checks_dir = project_path(root, config.checks)
    profiles = load_profiles(root)
    _, selected_profile = select_profile(
        profiles,
        request.profile,
    )

    client = Neo4jClient(selected_profile)

    try:
        suite_inputs = load_suite_inputs(
            checks_dir,
            request.suite_ids,
        )

        results = Engine(
            client,
            baselines=DirectoryBaselineProvider(
                artifacts / "baselines",
            ),
        ).run(
            suite_inputs,
            tags=request.tags,
            fail_fast=request.fail_fast,
            selection_suites=request.suite_ids or None,
        )

        results_path, report_path = write_run_artifacts(
            results,
            runs_dir,
        )

        return RunOutcome(
            results=results,
            results_path=results_path,
            report_path=report_path,
        )
    
    except GraphCheckError as exc:
        results = failed_results(
            exc.error,
            suite_ids=request.suite_ids,
            tags=request.tags,
            fail_fast=request.fail_fast,
        )

        results_path, report_path = write_run_artifacts(
            results,
            runs_dir,
        )

        return RunOutcome(
            results=results,
            results_path=results_path,
            report_path=report_path,
        )
    
    except Exception as exc:
        error = CheckError(
            code="run.configuration",
            message=f"GraphCheck could not prepare the run: {type(exc).__name__}: {exc}",
            fix="Fix the project configuration, then run `graphcheck debug` and try again.",
        )

        results = failed_results(
            error,
            suite_ids=request.suite_ids,
            tags=request.tags,
            fail_fast=request.fail_fast,
        )

        results_path, report_path = write_run_artifacts(
            results,
            runs_dir,
        )

        return RunOutcome(
            results=results,
            results_path=results_path,
            report_path=report_path,
        )
    
    finally:
        client.close()