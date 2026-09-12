from __future__ import annotations

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
from graphcheck.baselines import latest_baseline
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
from graphcheck.engine.executor import _accepts_parameter
from graphcheck.engine.runner import _remaining
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.project import (
    find_project_root,
    load_project_config,
)
from graphcheck.provenance import config_hash
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
    target_observer: Callable[[object], None] | None = None,
    stage_observer: Callable[[str, int | None], None] | None = None,
) -> RunOutcome:
    """
    Execute a GraphCheck run independently of the CLI or MCP.
    """
    if stage_observer is not None:
        stage_observer("Loading checks", None)
    root = find_project_root()
    config = load_project_config(root)
    artifacts = project_path(root, config.artifacts)
    runs_dir = artifacts / "runs"

    checks_dir = project_path(root, config.checks)
    baseline = None
    profile_name, selected_profile = request.profile, None
    client: Neo4jClient | None = None
    setup_done_perf: float | None = None
    engine_started = False

    try:
        profiles = load_profiles(root)
        profile_name, selected_profile = select_profile(profiles, request.profile)
        baseline = latest_baseline(root, config.artifacts)
        max_concurrency = request.concurrency or int(config.concurrency)
        engine_config = EngineConfig(
            max_concurrency=max_concurrency, result_row_limit=config.engine.result_row_limit
        )
        deadline = time.monotonic() + engine_config.time_budget_s
        suite_inputs = load_suite_inputs(checks_dir, request.suite_ids)
        check_count = sum(
            not request.tags or any(tag in check.tags for tag in request.tags)
            for item in suite_inputs
            for check in item.suite.checks
        )
        if stage_observer is not None:
            stage_observer("Connecting", check_count)

        factory = client_factory or _new_neo4j_client
        client = factory(
            selected_profile,
            max_concurrency,
        )

        if request.verify_read_only_credential:
            target = _verify_cli_audit_credential(client, deadline=deadline)
            if target_observer is not None:
                target_observer(target)

        setup_done_perf = time.monotonic()
        if stage_observer is not None:
            stage_observer("Running checks", check_count)
        engine = Engine(
            client,
            baselines=DirectoryBaselineProvider(
                artifacts / "baselines",
            ),
            config=engine_config,
            progress_callback=progress_callback,
            event_sink=event_sink,
        )
        _remaining(deadline, time.monotonic())
        engine_started = True
        results = engine.run(
            suite_inputs,
            tags=request.tags,
            fail_fast=request.fail_fast,
            selection_suites=request.suite_ids or None,
            deadline=deadline,
        )

    except GraphCheckError as exc:
        if setup_done_perf is None:
            setup_done_perf = time.monotonic()
        results = failed_results(
            exc.error,
            suite_ids=request.suite_ids,
            tags=request.tags,
            fail_fast=request.fail_fast,
        )

    except Exception as exc:
        if setup_done_perf is None:
            setup_done_perf = time.monotonic()
        if engine_started:
            # An unexpected fault raised by Engine.run() is an engine error, not a
            # configuration problem. Preserve `engine.unexpected` so the CLI reports it as
            # ENGINE / ENGINE_ERROR rather than a user configuration failure.
            error = CheckError(
                code="engine.unexpected",
                message=f"The GraphCheck engine failed unexpectedly: {type(exc).__name__}: {exc}",
                fix="Re-run the check suite; if it recurs, file a bug with the run details.",
            )
        else:
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

    finally:
        if client is not None:
            with suppress(Exception):
                client.close()

    # Publish exactly once, outside the setup/engine exception translation above. A write
    # failure must preserve the completed (or already-failed) result and surface as
    # artifact_error with the real artifact-write timing boundary — never a retried write
    # nor a re-labelled run.configuration result.
    artifact_started_perf = time.monotonic()
    results.run.baseline_ref = baseline.name if baseline is not None else None
    results.run.config_hash = config_hash(
        {
            "engine": results.run.config_hash,
            "project": config.model_dump(mode="json", exclude={"generate", "concurrency"}),
            "concurrency": request.concurrency or config.concurrency,
            "profile": profile_name,
            "connection": {
                "uri": selected_profile.uri,
                "database": selected_profile.database,
                "user": selected_profile.user,
            }
            if selected_profile is not None
            else None,
            "verify_read_only_credential": request.verify_read_only_credential,
        }
    )
    try:
        if stage_observer is not None:
            stage_observer("Writing reports", None)
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
            artifact_started_perf=artifact_started_perf,
        )

    return RunOutcome(
        results=results,
        results_path=results_path,
        report_path=report_path,
        setup_done_perf=setup_done_perf,
        artifact_started_perf=artifact_started_perf,
    )


def _new_neo4j_client(profile, max_concurrency: int):
    return Neo4jClient(profile, max_concurrency=max_concurrency)


def _verify_cli_audit_credential(client: object, *, deadline: float) -> object | None:
    """Probe the target, verify the read-only credential, and return the probed target.

    The returned target carries the live node/relationship counts so a caller can render a
    run header without probing the database a second time.
    """
    verify = getattr(client, "verify_read_only_credential", None)
    probe = getattr(client, "probe", None)
    result = None
    for method in (probe, verify):
        remaining = _remaining(deadline, time.monotonic())
        if callable(method):
            value = (
                method(timeout_s=remaining)
                if _accepts_parameter(method, "timeout_s", variadic=True)
                else method()
            )
            if method is probe:
                result = value
    _remaining(deadline, time.monotonic())
    target = result[0] if isinstance(result, tuple) else result
    if isinstance(result, tuple) and len(result) > 2 and target is not None:
        counts = result[2]
        copy = getattr(target, "model_copy", None)
        if callable(copy):
            target = copy(
                update={
                    "nodes": getattr(counts, "nodes", getattr(target, "nodes", None)),
                    "relationships": getattr(
                        counts, "relationships", getattr(target, "relationships", None)
                    ),
                }
            )
    return target
