from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from graphcheck.application.artifacts import (
    RenderObserver,
    write_run_artifacts,
)
from graphcheck.application.paths import project_path
from graphcheck.application.suites import load_suite_inputs
from graphcheck.connection_profiles import (
    load_profiles,
    select_profile,
)
from graphcheck.contracts.results import CheckError, Results
from graphcheck.engine import (
    DirectoryBaselineProvider,
    Engine,
    EngineConfig,
    failed_results,
)
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.project import (
    find_project_root,
    load_project_config,
)
from graphcheck.telemetry.events import EngineEventSink


@dataclass(slots=True)
class RunRequest:
    profile: str | None
    suite_ids: list[str]
    tags: list[str]
    fail_fast: bool
    concurrency: int | None = None
    verify_read_only_credential: bool = False


@dataclass(slots=True)
class RunOutcome:
    results: Results
    results_path: Path | None
    report_path: Path | None
    artifact_error: Exception | None = None
    # `time.monotonic()` boundaries so a caller can attribute setup versus
    # artifact-write time correctly. `setup_done_perf` is stamped once profile,
    # client, credential, and suite setup finish (before the engine runs);
    # `artifact_started_perf` is stamped immediately before artifacts are written.
    setup_done_perf: float | None = None
    artifact_started_perf: float | None = None


def execute_run(
    request: RunRequest,
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    event_sink: EngineEventSink | None = None,
    render_observer: RenderObserver | None = None,
    client_factory: Callable[[object, int], Neo4jClient] | None = None,
    artifact_writer: Callable[..., tuple[Path, Path]] = write_run_artifacts,
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

    client: Neo4jClient | None = None

    try:
        max_concurrency = request.concurrency or int(config.concurrency)

        factory = client_factory or _new_neo4j_client
        client = factory(
            selected_profile,
            max_concurrency,
        )

        if request.verify_read_only_credential:
            _verify_cli_audit_credential(client)

        suite_inputs = load_suite_inputs(
            checks_dir,
            request.suite_ids,
        )
        setup_done_perf = time.monotonic()
        results = Engine(
            client,
            baselines=DirectoryBaselineProvider(
                artifacts / "baselines",
            ),
            config=EngineConfig(max_concurrency=max_concurrency),
            progress_callback=progress_callback,
            event_sink=event_sink,
        ).run(
            suite_inputs,
            tags=request.tags,
            fail_fast=request.fail_fast,
            selection_suites=request.suite_ids or None,
        )

        artifact_started_perf = time.monotonic()
        results_path, report_path = artifact_writer(
            results,
            runs_dir,
            render_observer=render_observer,
        )

        return RunOutcome(
            results=results,
            results_path=results_path,
            report_path=report_path,
            setup_done_perf=setup_done_perf,
            artifact_started_perf=artifact_started_perf,
        )

    except GraphCheckError as exc:
        setup_done_perf = time.monotonic()
        results = failed_results(
            exc.error,
            suite_ids=request.suite_ids,
            tags=request.tags,
            fail_fast=request.fail_fast,
        )

        try:
            artifact_started_perf = time.monotonic()
            results_path, report_path = artifact_writer(
                results,
                runs_dir,
                render_observer=render_observer,
            )
        except Exception as artifact_exc:
            return RunOutcome(
                results=results,
                results_path=None,
                report_path=None,
                artifact_error=artifact_exc,
                setup_done_perf=setup_done_perf,
            )

        return RunOutcome(
            results=results,
            results_path=results_path,
            report_path=report_path,
            setup_done_perf=setup_done_perf,
            artifact_started_perf=artifact_started_perf,
        )

    except Exception as exc:
        setup_done_perf = time.monotonic()
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

        try:
            artifact_started_perf = time.monotonic()
            results_path, report_path = artifact_writer(
                results,
                runs_dir,
                render_observer=render_observer,
            )
        except Exception as artifact_exc:
            return RunOutcome(
                results=results,
                results_path=None,
                report_path=None,
                artifact_error=artifact_exc,
                setup_done_perf=setup_done_perf,
            )

        return RunOutcome(
            results=results,
            results_path=results_path,
            report_path=report_path,
            setup_done_perf=setup_done_perf,
            artifact_started_perf=artifact_started_perf,
        )

    finally:
        if client is not None:
            with suppress(Exception):
                client.close()


def _new_neo4j_client(profile, max_concurrency: int):
    """Construct the workload-aware Neo4j client while retaining simple test doubles."""
    parameters = inspect.signature(Neo4jClient).parameters.values()
    accepts_setting = any(
        parameter.name == "max_concurrency" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    return (
        Neo4jClient(profile, max_concurrency=max_concurrency)
        if accepts_setting
        else Neo4jClient(profile)
    )


def _verify_cli_audit_credential(client: object) -> None:
    probe = getattr(client, "probe", None)
    verify = getattr(client, "verify_read_only_credential", None)

    if callable(probe):
        probe()

    if callable(verify):
        verify()
