import json
import logging
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from graphcheck import __version__
from graphcheck.telemetry.consent import (
    disable_telemetry,
    enable_telemetry,
    reset_installation_id,
    resolve_consent,
)
from graphcheck.telemetry.types import (
    CONSENT_VERSION,
    TELEMETRY_SCHEMA_VERSION,
    ArtifactOutcome,
    CliFailureStage,
    CommandAction,
    CommandName,
    ConsentState,
    EventOutcome,
    OutputMode,
    ProcessOutcome,
    SafeErrorCode,
    safe_command,
)

if TYPE_CHECKING:
    from graphcheck.contracts.profile import BaselineProfile
    from graphcheck.contracts.results import CheckError, Results, RunTarget
    from graphcheck.engine import SuiteInput
    from graphcheck.generation.disclosure import GenerateDisclosure
    from graphcheck.generation.service import DroppedCandidate, GenerateResult
    from graphcheck.reporting.history import ReportRun

_NEO4J_NOTIFICATION_LOGGER = "neo4j.notifications"


def _call(module: str, name: str, *args, **kwargs):
    """Import a command dependency only when its command actually uses it."""
    return getattr(import_module(module), name)(*args, **kwargs)


# Stable injection points for tests and integrations; each forwards lazily by default.
def find_project_root(*args, **kwargs):
    return _call("graphcheck.project", "find_project_root", *args, **kwargs)


def load_profiles(*args, **kwargs):
    return _call("graphcheck.connection_profiles", "load_profiles", *args, **kwargs)


def select_profile(*args, **kwargs):
    return _call("graphcheck.connection_profiles", "select_profile", *args, **kwargs)


def Neo4jClient(*args, **kwargs):
    return _call("graphcheck.neo4j_adapter", "Neo4jClient", *args, **kwargs)


def init_trace(*args, **kwargs):
    return _call("graphcheck.neo4j_adapter", "init_trace", *args, **kwargs)


def debug_trace(*args, **kwargs):
    return _call("graphcheck.neo4j_adapter", "debug_trace", *args, **kwargs)


def build_profile(*args, **kwargs):
    return _call("graphcheck.profiler", "profile", *args, **kwargs)


def resolve_diff_baselines(*args, **kwargs):
    return _call("graphcheck.baselines", "resolve_diff_baselines", *args, **kwargs)


def compare_baselines(*args, **kwargs):
    return _call("graphcheck.diff", "compare", *args, **kwargs)


def generation_service_factory(*args, **kwargs):
    return _call("graphcheck.generation.service", "GenerationService", *args, **kwargs)


def write_results(*args, **kwargs):
    return _call("graphcheck.reporting.writer", "write_results", *args, **kwargs)


def redact_results(*args, **kwargs):
    return _call("graphcheck.reporting.redaction", "redact_results", *args, **kwargs)


def write_html_report(*args, **kwargs):
    return _call("graphcheck.reporting.html", "write_html_report", *args, **kwargs)


def open_report_explorer(*args, **kwargs):
    return _call("graphcheck.reporting.explorer", "launch_report_explorer", *args, **kwargs)


def run_monitor(*args, **kwargs):
    return _call("graphcheck.observability.runner", "run_monitor", *args, **kwargs)


app = typer.Typer(
    name="graphcheck",
    help="Semantic observability for property graphs.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

mcp_app = typer.Typer(help="Model Context Protocol commands.")
app.add_typer(mcp_app, name="mcp")

telemetry_app = typer.Typer(
    name="telemetry",
    help="Manage anonymous opt-in product telemetry.",
    no_args_is_help=True,
)
app.add_typer(telemetry_app, name="telemetry")

baseline_app = typer.Typer(help="Manage baseline snapshots.")
app.add_typer(baseline_app, name="baseline")

_COMMAND_TELEMETRY: ContextVar[object | None] = ContextVar(
    "graphcheck_command_telemetry",
    default=None,
)


def _telemetry_command(command: CommandName):
    def decorate(function):
        @wraps(function)
        def instrumented(*args, **kwargs):
            runtime = _command_telemetry()
            owns_runtime = runtime is None
            token = None
            if runtime is None:
                output_mode = (
                    OutputMode.JSON if kwargs.get("json_output") is True else OutputMode.HUMAN
                )
                runtime = _start_telemetry_runtime(command, output_mode=output_mode)
                token = _COMMAND_TELEMETRY.set(runtime)
            runtime.mark_callback_entered()
            try:
                return function(*args, **kwargs)
            except typer.Exit:
                raise
            except Exception:
                if runtime.process_outcome is ProcessOutcome.SUCCESS:
                    runtime.fail(
                        ProcessOutcome.UNEXPECTED_ERROR,
                        CliFailureStage.PROJECT_DISCOVERY,
                        SafeErrorCode.UNKNOWN,
                    )
                raise
            finally:
                if owns_runtime:
                    with suppress(Exception):
                        runtime.finish()
                    assert token is not None
                    _COMMAND_TELEMETRY.reset(token)

        return instrumented

    return decorate


def _command_telemetry():
    return _COMMAND_TELEMETRY.get()


def cli(*, consent: ConsentState | None = None) -> None:
    """Run Typer behind the true command boundary, including argument-parsing failures."""

    arguments = tuple(sys.argv[1:])
    command = _command_from_argv(arguments)
    if command is CommandName.TELEMETRY:
        app()
        return

    output_mode = OutputMode.JSON if "--json" in arguments else OutputMode.HUMAN
    runtime = _start_telemetry_runtime(command, output_mode=output_mode, consent=consent)
    token = _COMMAND_TELEMETRY.set(runtime)
    try:
        app()
    except SystemExit as exc:
        if not runtime.callback_entered and exc.code not in {None, 0}:
            runtime.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.CONFIG_LOAD,
                SafeErrorCode.CONFIG_INVALID,
            )
        raise
    except Exception:
        if runtime.process_outcome is ProcessOutcome.SUCCESS:
            runtime.fail(
                ProcessOutcome.UNEXPECTED_ERROR,
                CliFailureStage.CONFIG_LOAD,
                SafeErrorCode.UNKNOWN,
            )
        raise
    finally:
        with suppress(Exception):
            runtime.finish()
        _COMMAND_TELEMETRY.reset(token)


def _command_from_argv(arguments: Sequence[str]) -> CommandName:
    for argument in arguments:
        if argument == "--":
            break
        if not argument.startswith("-"):
            return safe_command(argument)
    return CommandName.OTHER


def _start_telemetry_runtime(
    command: CommandName,
    *,
    output_mode: OutputMode,
    consent: ConsentState | None = None,
    action: CommandAction | str | None = None,
):
    state = consent
    if state is None:
        try:
            state = resolve_consent()
        except Exception:
            from graphcheck.telemetry.types import ConsentSource

            state = ConsentState(False, ConsentSource.DEFAULT)
    if state.enabled:
        from graphcheck.telemetry.runtime import CommandTelemetryRuntime

        return CommandTelemetryRuntime.start(
            command,
            action=action,
            output_mode=output_mode,
            consent=state,
        )
    from graphcheck.telemetry.inactive import InactiveCommandTelemetryRuntime

    return InactiveCommandTelemetryRuntime.start(
        command,
        action=action,
        output_mode=output_mode,
        consent=state,
    )


def _version(value: bool) -> None:
    if value:
        typer.secho(f"graphcheck {__version__}", fg=typer.colors.BRIGHT_BLUE, bold=True)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Run graph checks, diagnose Neo4j, and inspect offline reports."""

    # Neo4j 6 logs every server notification at WARNING by default. These are query metadata,
    # which the adapter consumes and promotes when GraphCheck needs to act on them; letting the
    # driver's logger also write them to stderr overwhelms the CLI's stable human output.
    logging.getLogger(_NEO4J_NOTIFICATION_LOGGER).setLevel(logging.ERROR)


@telemetry_app.command("enable")
def telemetry_enable() -> None:
    """Enable anonymous telemetry for this user."""

    started = time.monotonic()
    before = resolve_consent()
    enable_telemetry()
    state = resolve_consent()
    typer.secho("Anonymous GraphCheck telemetry is enabled.", fg=typer.colors.GREEN)
    if not _telemetry_delivery_configured():
        typer.secho(
            "Telemetry delivery is not configured in this build; consent remains stored.",
            fg=typer.colors.YELLOW,
        )
    # This is the single telemetry control action that may emit: consent exists only after the
    # state has been stored, so construct the command runtime at that point.
    try:
        if before.enabled or not state.enabled:
            return
        runtime = _start_telemetry_runtime(
            CommandName.TELEMETRY,
            action=CommandAction.ENABLE,
            output_mode=OutputMode.HUMAN,
            consent=state,
        )
        runtime.started_perf = started
        runtime.finish()
    except Exception:
        pass


@telemetry_app.command("disable")
def telemetry_disable() -> None:
    """Disable telemetry without sending an event."""

    disable_telemetry()
    typer.secho("Anonymous GraphCheck telemetry is disabled.", fg=typer.colors.YELLOW)


@telemetry_app.command("status")
def telemetry_status() -> None:
    """Show the effective user/process telemetry state."""

    state = resolve_consent()
    typer.secho(
        f"Telemetry: {'enabled' if state.enabled else 'disabled'}",
        fg=typer.colors.GREEN if state.enabled else typer.colors.YELLOW,
    )
    typer.echo(f"Source: {state.source.value}")
    delivery = "configured" if _telemetry_delivery_configured() else "not configured"
    typer.echo(f"Delivery: {delivery}")
    if state.renewal_required:
        typer.secho(
            "Consent renewal is required for the current telemetry consent version.",
            fg=typer.colors.YELLOW,
        )


@telemetry_app.command("preview")
def telemetry_preview() -> None:
    """Print representative sanitized payloads without sending them."""

    payload = _telemetry_preview_payload()
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@telemetry_app.command("reset-id")
def telemetry_reset_id() -> None:
    """Break installation linkage by replacing or clearing the stored ID."""

    state = reset_installation_id()
    if state.enabled:
        typer.secho(
            "Telemetry installation ID was reset; telemetry remains enabled.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho("Inactive telemetry installation ID was cleared.", fg=typer.colors.GREEN)


@app.command()
@_telemetry_command(CommandName.INIT)
def init() -> None:
    """Scaffold a new GraphCheck project in the current directory."""
    from graphcheck.connection_profiles import write_default_profiles
    from graphcheck.errors import GraphCheckError
    from graphcheck.project import (
        PROJECT_FILE,
        ensure_gitignore_entries,
        write_default_project,
        write_example_suite,
    )

    root = Path.cwd()
    write_default_project(root)
    write_default_profiles(root)
    ensure_gitignore_entries(root)
    write_example_suite(root)

    typer.secho(f"Wrote {PROJECT_FILE}", fg=typer.colors.GREEN)
    typer.secho("Wrote profiles.yml", fg=typer.colors.GREEN)
    typer.secho("Profile setup help is included in profiles.yml", fg=typer.colors.CYAN)
    typer.secho("Wrote checks/example.yml with 3 sample checks", fg=typer.colors.GREEN)

    profiles = load_profiles(root)
    profile_name, profile = select_profile(profiles)
    probe_started = time.monotonic()
    connected = False
    try:
        trace = init_trace(profile_name, profile)
    except GraphCheckError as exc:
        if (telemetry := _command_telemetry()) is not None:
            telemetry.record_probe(
                started_perf=probe_started,
                outcome=EventOutcome.ERROR,
            )
        typer.secho(f"Neo4j was not detected: {exc.error.code}", fg=typer.colors.YELLOW)
        typer.secho(exc.error.message, fg=typer.colors.YELLOW)
        typer.secho(f"Fix: {exc.error.fix}", fg=typer.colors.CYAN)
    except Exception as exc:
        if (telemetry := _command_telemetry()) is not None:
            telemetry.record_probe(started_perf=probe_started, outcome=EventOutcome.ERROR)
        typer.secho("Neo4j was not detected: init.internal_error", fg=typer.colors.YELLOW)
        typer.secho(
            "GraphCheck could not complete the connection diagnostic.", fg=typer.colors.YELLOW
        )
        typer.secho(
            "Fix: Run `graphcheck debug --json`; if the problem persists, report the JSON "
            f"error and GraphCheck version. ({type(exc).__name__})",
            fg=typer.colors.CYAN,
        )
    else:
        connected = True
        if (telemetry := _command_telemetry()) is not None:
            telemetry.record_probe(
                started_perf=probe_started,
                outcome=EventOutcome.SUCCESS,
                target=trace.target,
            )
        typer.secho(
            f"Detected Neo4j at {profile.uri} (version {trace.target.server_version})",
            fg=typer.colors.GREEN,
        )
        typer.secho(
            f"APOC: {'yes' if trace.target.capabilities.apoc else 'no'}",
            fg=typer.colors.GREEN if trace.target.capabilities.apoc else typer.colors.YELLOW,
        )
    typer.secho(
        (
            "Next: edit checks/example.yml, then run `graphcheck run`"
            if connected
            else "Next: apply the fix above, then run `graphcheck debug`"
        ),
        fg=typer.colors.CYAN,
    )


@app.command()
@_telemetry_command(CommandName.DEBUG)
def debug(
    profile: str | None = typer.Option(None, "--profile", help="Connection profile to use."),
    json_output: bool = typer.Option(False, "--json", help="Emit the stable debug JSON trace."),
) -> None:
    """Diagnose Neo4j connectivity and apply the edition-specific read-only policy."""
    from graphcheck.debug_diagnostics import CapabilityContext, blocked_checks_for_project
    from graphcheck.errors import GraphCheckError
    from graphcheck.neo4j_adapter import error_json

    profile_name = profile or "local"
    try:
        root = find_project_root()
        profiles = load_profiles(root)
        profile_name, selected = select_profile(profiles, profile)
        probe_started = time.monotonic()
        trace = debug_trace(profile_name, selected)
        if (telemetry := _command_telemetry()) is not None:
            telemetry.record_probe(
                started_perf=probe_started,
                outcome=EventOutcome.SUCCESS,
                target=trace.target,
            )
        trace = replace(
            trace,
            blocked_checks=tuple(
                blocked_checks_for_project(
                    root,
                    CapabilityContext.from_probe(trace.target.capabilities, trace.visibility),
                )
            ),
        )
    except GraphCheckError as exc:
        if (telemetry := _command_telemetry()) is not None:
            if "probe_started" in locals():
                telemetry.record_probe(
                    started_perf=probe_started,
                    outcome=EventOutcome.ERROR,
                )
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                _cli_stage_for_error(exc.error.code),
                exc.error.code,
            )
        payload = error_json(profile_name, exc.error)
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
            typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        from graphcheck.contracts.results import CheckError

        error = CheckError(
            code="debug.internal_error",
            message="GraphCheck could not complete the connection diagnostic.",
            fix="Run `graphcheck debug --json`; if the problem persists, report the JSON error "
            "and GraphCheck version.",
        )
        payload = error_json(profile_name, error)
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            typer.echo(f"{error.code}: {error.message}", err=True)
            typer.echo(f"Fix: {error.fix}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(trace.as_json(), indent=2, sort_keys=True))
        return

    caps = trace.target.capabilities
    typer.echo(f"Profile: {trace.profile}")
    if trace.versions is not None:
        typer.echo(f"GraphCheck version: {trace.versions.graphcheck}")
        typer.echo(f"Neo4j Python driver: {trace.versions.neo4j_driver}")
    typer.echo(f"Neo4j Server: {trace.target.server_version}")
    if trace.versions is not None:
        typer.echo(f"Cypher: {trace.versions.cypher}")
    typer.echo(f"Edition: {trace.target.edition}")
    typer.echo(f"Database name: {trace.target.database}")
    typer.echo(f"APOC: {'yes' if caps.apoc else 'no'}")
    typer.echo(f"Count store: {'yes' if caps.count_store else 'no'}")
    can_see = []
    if trace.visibility.can_connect:
        can_see.append("connect")
    if trace.visibility.can_read:
        can_see.append("read")
    if trace.visibility.can_show_procedures:
        can_see.append("procedures")
    cannot_see = []
    if not trace.visibility.can_connect:
        cannot_see.append("connect")
    if not trace.visibility.can_read:
        cannot_see.append("read")
    if not trace.visibility.can_show_procedures:
        cannot_see.append("procedures")
    typer.echo(f"Credentials can see: {', '.join(can_see) if can_see else 'none detected'}")
    cannot_see_text = ", ".join(cannot_see) if cannot_see else "none detected"
    typer.echo(f"Credentials cannot see: {cannot_see_text}")
    if trace.blocked_checks:
        typer.echo("Blocked checks:")
        for blocked in trace.blocked_checks:
            typer.echo(
                f"- {blocked.suite}/{blocked.check_id} ({blocked.check}) requires "
                f"{blocked.missing_capability}: {blocked.fix}"
            )
    else:
        typer.echo("Blocked checks: none")
    if trace.counts.nodes is None or trace.counts.relationships is None:
        typer.echo("Counts: unavailable (read access denied)")
    else:
        typer.echo(
            f"Counts: {trace.counts.nodes} nodes, {trace.counts.relationships} relationships"
        )


@app.command()
@_telemetry_command(CommandName.PROFILE)
def profile(
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Connection profile to use.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the complete baseline profile as JSON.",
    ),
) -> None:
    """Generate a baseline profile for the connected Neo4j graph."""
    from graphcheck.baselines import write_baseline
    from graphcheck.contracts.profile import ProfileStatus
    from graphcheck.errors import GraphCheckError
    from graphcheck.project import PROJECT_FILE, default_project_config, load_project_config

    telemetry = _command_telemetry()
    setup_started = time.monotonic()
    profiling_started = False
    unexpected_stage = CliFailureStage.PROJECT_DISCOVERY
    try:
        root = find_project_root()
        config = (
            load_project_config(root)
            if (root / PROJECT_FILE).is_file()
            else default_project_config()
        )
        unexpected_stage = CliFailureStage.PROFILE_LOAD
        profiles = load_profiles(root)
        _, selected = select_profile(profiles, profile)

        unexpected_stage = CliFailureStage.CLIENT_SETUP
        client = Neo4jClient(selected)
        if telemetry is not None:
            telemetry.mark_setup(setup_started)
        try:
            profiling_started = True
            unexpected_stage = CliFailureStage.PROFILE_COLLECTION
            observer = (
                telemetry.record_profile_stage
                if telemetry is not None and telemetry.enabled
                else None
            )
            result_observer = (
                telemetry.record_profile_result
                if telemetry is not None and telemetry.enabled
                else None
            )
            baseline = build_profile(
                client,
                telemetry_observer=observer,
                telemetry_result_observer=result_observer,
            )
        finally:
            client.close()
    except GraphCheckError as exc:
        if telemetry is not None:
            if telemetry.setup_ms is None:
                telemetry.mark_setup(setup_started)
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                (
                    CliFailureStage.PROFILE_COLLECTION
                    if profiling_started
                    else _cli_stage_for_error(exc.error.code)
                ),
                exc.error.code,
            )
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc
    except Exception:
        if telemetry is not None and telemetry.process_outcome is ProcessOutcome.SUCCESS:
            if telemetry.setup_ms is None:
                telemetry.mark_setup(setup_started)
            telemetry.fail(
                ProcessOutcome.UNEXPECTED_ERROR,
                unexpected_stage,
                (
                    SafeErrorCode.PROFILE_COLLECTION_FAILED
                    if unexpected_stage is CliFailureStage.PROFILE_COLLECTION
                    else SafeErrorCode.UNKNOWN
                ),
            )
        raise

    if telemetry is not None and not telemetry.profile_result_recorded:
        telemetry.record_profile_result(
            baseline.status,
            None if baseline.status is ProfileStatus.COMPLETE else "unknown",
        )
    artifact_started = time.monotonic()
    try:
        path = write_baseline(baseline, root, config.artifacts)
    except OSError as exc:
        if telemetry is not None:
            telemetry.baseline_artifact = ArtifactOutcome.ERROR
            telemetry.artifact_write_ms = max(
                0,
                round((time.monotonic() - artifact_started) * 1000),
            )
            telemetry.fail(
                ProcessOutcome.UNEXPECTED_ERROR,
                CliFailureStage.BASELINE_WRITE,
                SafeErrorCode.BASELINE_WRITE_FAILED,
            )
        typer.echo(f"baseline.write_failed: Could not write the baseline: {exc}", err=True)
        raise typer.Exit(1) from exc
    if telemetry is not None:
        telemetry.baseline_artifact = ArtifactOutcome.WRITTEN
        telemetry.artifact_write_ms = max(
            0,
            round((time.monotonic() - artifact_started) * 1000),
        )
    if json_output:
        typer.echo(baseline.model_dump_json(indent=2, by_alias=True))
    else:
        _print_profile_summary(
            baseline,
            path,
        )


@app.command()
def monitor(
    profile: str | None = typer.Option(None, "--profile", help="Connection profile to use."),
    host: str = typer.Option("127.0.0.1", "--host", help="Metrics server host."),
    port: int = typer.Option(9100, "--port", min=1, max=65535, help="Metrics server port."),
    interval: int = typer.Option(
        15,
        "--interval",
        min=1,
        help="Seconds between database health checks.",
    ),
) -> None:
    """Expose connected-database health metrics until interrupted."""
    from graphcheck.errors import GraphCheckError

    client: Neo4jClient | None = None
    try:
        root = find_project_root()
        profiles = load_profiles(root)
        _, selected = select_profile(profiles, profile)
        client = Neo4jClient(selected)
        display_host = "localhost" if host == "0.0.0.0" else host

        typer.echo("Starting GraphCheck monitoring...")
        typer.echo(f"Metrics endpoint: http://{display_host}:{port}/metrics")
        typer.echo(f"Health check interval: {interval} seconds")
        typer.echo("Press Ctrl+C to stop monitoring.")

        try:
            run_monitor(
                client=client,
                interval_seconds=interval,
                host=host,
                port=port,
            )
        except OSError:
            typer.echo("Unable to start GraphCheck monitoring.", err=True)
            typer.echo(
                f"Failed to start metrics server on {display_host}:{port}.",
                err=True,
            )
            typer.echo("The port may already be in use.", err=True)
            raise typer.Exit(1) from None
    except GraphCheckError as exc:
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if client is not None:
            client.close()


@app.command()
@_telemetry_command(CommandName.GENERATE)
def generate(
    from_: Annotated[
        Path | None,
        typer.Option("--from", help="Baseline JSON; defaults to the latest valid snapshot."),
    ] = None,
    docs: Annotated[
        list[Path] | None,
        typer.Option(
            "--docs",
            help="UTF-8 domain document sent verbatim. Repeat for multiple files.",
        ),
    ] = None,
    count: Annotated[
        int,
        typer.Option("--count", min=1, max=20, help="Approximate number of checks to propose."),
    ] = 5,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a stable machine-readable result."),
    ] = False,
) -> None:
    """Generate non-deterministic, inert check suggestions for human review."""
    from graphcheck.errors import GraphCheckError
    from graphcheck.generation.service import GenerationStage

    telemetry = _command_telemetry()
    current_stage = CliFailureStage.PROJECT_DISCOVERY
    artifact_started: float | None = None

    def observe_stage(stage: GenerationStage) -> None:
        nonlocal artifact_started, current_stage
        current_stage = CliFailureStage(stage.value)
        if stage is GenerationStage.ARTIFACT_WRITE:
            artifact_started = time.monotonic()

    def mark_generated_artifact(outcome: ArtifactOutcome) -> None:
        if telemetry is not None and artifact_started is not None:
            telemetry.mark_generated_artifact(artifact_started, outcome)

    def disclose(event: "GenerateDisclosure") -> None:
        if json_output:
            typer.echo(
                json.dumps(event.as_json(), separators=(",", ":"), ensure_ascii=False),
                err=True,
            )
        else:
            typer.echo(event.render_human(), err=True)

    def warn(candidate: "DroppedCandidate") -> None:
        if not json_output:
            typer.echo(
                f"Warning [{candidate.code}] {candidate.candidate}: {candidate.reason}",
                err=True,
            )

    try:
        root = find_project_root()
        current_stage = CliFailureStage.CONFIG_LOAD
        result = generation_service_factory().generate(
            project_root=root,
            baseline_from=from_,
            document_paths=docs,
            requested_count=count,
            disclosure_sink=disclose,
            warning_sink=warn,
            invocation_dir=Path.cwd(),
            stage_observer=observe_stage,
        )
    except GraphCheckError as exc:
        if exc.error.code.startswith("generate.write_"):
            mark_generated_artifact(ArtifactOutcome.ERROR)
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                _cli_stage_for_error(exc.error.code),
                exc.error.code,
            )
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "command": "generate",
                        "status": "error",
                        "error": exc.error.model_dump(mode="json"),
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            )
        else:
            typer.echo(f"Error [{exc.error.code}]: {exc.error.message}", err=True)
            typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc
    except Exception:
        if current_stage is CliFailureStage.ARTIFACT_WRITE:
            mark_generated_artifact(ArtifactOutcome.ERROR)
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.UNEXPECTED_ERROR,
                current_stage,
                SafeErrorCode.UNKNOWN,
            )
        raise

    mark_generated_artifact(ArtifactOutcome.WRITTEN)
    _render_generate_result(result, json_output=json_output)


def _render_generate_result(result: "GenerateResult", *, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                result.model_dump(mode="json"),
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return
    dropped = f" ({result.dropped} dropped)" if result.dropped else ""
    typer.echo(
        f"Wrote {result.written} generated checks to {result.path}{dropped}; "
        "review them and remove generated: true to activate."
    )


@app.command()
@_telemetry_command(CommandName.REPORT)
def report(
    report_id: str | None = typer.Argument(
        None,
        metavar="[ID]",
        help="Historical run ID to open; valid only with --open.",
    ),
    open_report: bool = typer.Option(
        False,
        "--open",
        help="Open the latest report, or the selected ID when one is provided.",
    ),
    list_reports: bool = typer.Option(
        False,
        "--list",
        help="List report history with timestamps, scores, and statuses.",
    ),
    compare: tuple[str, str] | None = typer.Option(
        None,
        "--compare",
        metavar="RUN1 RUN2",
        help="Compare the check outcomes in two historical reports.",
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Remove old historical report artifacts.",
    ),
    keep: int | None = typer.Option(
        None,
        "--keep",
        metavar="COUNT",
        help="Number of newest historical runs to retain with --prune.",
    ),
    failures_only: bool = typer.Option(
        False,
        "--failures-only",
        help="Write a report containing only failures, warnings, and errors.",
    ),
) -> None:
    """Open, list, compare, prune, or filter generated reports."""
    telemetry = _command_telemetry()
    if telemetry is not None:
        telemetry.set_action(
            _report_action(
                open_report=open_report,
                list_reports=list_reports,
                compare=compare,
                prune=prune,
                failures_only=failures_only,
            )
        )
    _validate_report_options(
        open_report=open_report,
        report_id=report_id,
        list_reports=list_reports,
        compare=compare,
        prune=prune,
        keep=keep,
        failures_only=failures_only,
    )
    if not any((open_report, list_reports, compare, prune, failures_only)):
        _print_report_command_help()
        return

    from graphcheck.contracts.results import Verdict
    from graphcheck.errors import GraphCheckError
    from graphcheck.project import load_project_config
    from graphcheck.reporting.history import (
        ReportHistoryError,
        discover_report_runs,
        find_report_run,
        format_report_comparison,
        format_report_history,
        prune_report_runs,
    )

    try:
        root = find_project_root()
        config = load_project_config(root)
    except GraphCheckError as exc:
        if (telemetry := _command_telemetry()) is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                _cli_stage_for_error(exc.error.code),
                exc.error.code,
            )
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc

    runs_dir = root / config.artifacts / "runs"
    try:
        if list_reports:
            typer.echo(format_report_history(discover_report_runs(runs_dir)))
            return

        if compare is not None:
            records = discover_report_runs(runs_dir)
            first = find_report_run(records, compare[0])
            second = find_report_run(records, compare[1])
            typer.echo(format_report_comparison(first, second))
            return

        if prune:
            assert keep is not None
            removed = prune_report_runs(runs_dir, keep)
            if not removed:
                typer.echo(f"No historical report runs needed pruning; keeping newest {keep}.")
                return
            typer.echo(f"Pruned {len(removed)} historical report run(s):")
            for record in removed:
                typer.echo(f"  {record.id}")
            return

        if failures_only:
            records = discover_report_runs(runs_dir)
            record = find_report_run(records, report_id) if report_id else _latest_run(records)
            output = record.directory / "report.failures.html"
            render_started = time.monotonic()
            try:
                write_html_report(
                    record.results,
                    output,
                    verdicts={Verdict.FAIL, Verdict.WARN, Verdict.ERRORED},
                )
            except OSError as exc:
                if telemetry is not None:
                    telemetry.render_ms = max(0, round((time.monotonic() - render_started) * 1000))
                    telemetry.report_artifact = ArtifactOutcome.ERROR
                    telemetry.fail(
                        ProcessOutcome.UNEXPECTED_ERROR,
                        CliFailureStage.REPORT_RENDER,
                        SafeErrorCode.REPORT_RENDER_FAILED,
                    )
                typer.echo("report.error: failed to render the requested report", err=True)
                raise typer.Exit(1) from exc
            if telemetry is not None:
                telemetry.render_ms = max(0, round((time.monotonic() - render_started) * 1000))
                telemetry.report_artifact = ArtifactOutcome.WRITTEN
            typer.secho(f"Wrote {output}", fg=typer.colors.GREEN)
            if open_report:
                _open_html_report(output)
            return

        if open_report:
            records = discover_report_runs(runs_dir)
            if records:
                record = (
                    find_report_run(records, report_id) if report_id is not None else records[0]
                )
                open_report_explorer(
                    runs_dir,
                    record.id,
                    opener=webbrowser.open,
                    on_open=lambda _: typer.echo(
                        f"Opened report explorer for {record.id}. "
                        "Keep this terminal open; press Ctrl+C to stop."
                    ),
                )
            elif report_id is not None:
                find_report_run(records, report_id)
            else:
                _open_html_report(_latest_html_report(runs_dir))
    except ReportHistoryError as exc:
        if telemetry is not None:
            render_failed = (
                failures_only and telemetry.report_artifact is not ArtifactOutcome.WRITTEN
            )
            if render_failed:
                telemetry.report_artifact = ArtifactOutcome.ERROR
            stage = (
                CliFailureStage.REPORT_RENDER
                if render_failed or not open_report
                else CliFailureStage.REPORT_OPEN
            )
            code = (
                SafeErrorCode.REPORT_RENDER_FAILED
                if stage is CliFailureStage.REPORT_RENDER
                else SafeErrorCode.REPORT_OPEN_FAILED
            )
            telemetry.fail(ProcessOutcome.USER_ERROR, stage, code)
        typer.echo(f"report.error: {exc}", err=True)
        raise typer.Exit(1) from exc


@baseline_app.command("set")
@_telemetry_command(CommandName.BASELINE)
def baseline_set(
    filename: str | None = typer.Argument(
        None,
        help="Timestamped baseline filename to activate; defaults to the newest snapshot.",
    ),
) -> None:
    """Select an existing snapshot as the active baseline."""
    from graphcheck.baselines import set_current_baseline
    from graphcheck.errors import GraphCheckError

    telemetry = _command_telemetry()
    if telemetry is not None:
        telemetry.set_action(CommandAction.SET)
    try:
        selected = set_current_baseline(filename)
    except GraphCheckError as exc:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.BASELINE_LOAD,
                exc.error.code,
            )
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Baseline set to {selected.name}", fg=typer.colors.GREEN)


@app.command("diff")
@_telemetry_command(CommandName.DIFF)
def diff_command(
    current_baseline_name: str | None = typer.Argument(
        None,
        help="Current Baseline filename or path.",
    ),
    latest_baseline_name: str | None = typer.Argument(
        None,
        help="Latest Baseline filename or path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the structured diff as JSON."),
) -> None:
    """Compare two stored baseline snapshots."""
    from pydantic import ValidationError

    from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
    from graphcheck.diff import SchemaVersionMismatch, render_human, render_json
    from graphcheck.errors import GraphCheckError

    telemetry = _command_telemetry()
    try:
        current_baseline_path, latest_baseline_path = resolve_diff_baselines(
            current_baseline_name,
            latest_baseline_name,
        )
        current_raw = current_baseline_path.read_text(encoding="utf-8")
        latest_raw = latest_baseline_path.read_text(encoding="utf-8")
        current_data = json.loads(current_raw)
        latest_data = json.loads(latest_raw)
        if not isinstance(current_data, dict) or not isinstance(latest_data, dict):
            raise GraphCheckError(
                "baseline.invalid",
                "Baseline JSON root must be an object.",
                "Choose a valid baseline snapshot generated by `graphcheck profile`.",
            )
        if current_data.get("schema_version") != latest_data.get("schema_version"):
            raise SchemaVersionMismatch(
                "cannot diff baselines with different schema_version "
                f"(a={current_data.get('schema_version')}, "
                f"b={latest_data.get('schema_version')})"
            )
        current_baseline = BaselineProfile.model_validate_json(current_raw)
        latest_baseline = BaselineProfile.model_validate_json(latest_raw)
        if (
            current_baseline.status is ProfileStatus.PARTIAL
            or latest_baseline.status is ProfileStatus.PARTIAL
        ):
            raise GraphCheckError(
                "diff.partial_baseline",
                "Comparison is inconclusive because one or more baselines are partial.",
                "Generate complete baseline profiles before running `graphcheck diff`.",
            )
    except GraphCheckError as exc:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.BASELINE_LOAD,
                exc.error.code,
            )
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(2) from exc
    except SchemaVersionMismatch as exc:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.DIFF_COMPARE,
                SafeErrorCode.DIFF_INCOMPARABLE,
            )
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.BASELINE_LOAD,
                (
                    SafeErrorCode.BASELINE_LOAD_FAILED
                    if isinstance(exc, OSError)
                    else SafeErrorCode.BASELINE_INVALID
                ),
            )
        typer.echo(f"error: unable to read baseline: {exc}", err=True)
        raise typer.Exit(2) from exc

    if _target_identity(current_baseline.target) != _target_identity(latest_baseline.target):
        if json_output:
            if telemetry is not None:
                telemetry.fail(
                    ProcessOutcome.USER_ERROR,
                    CliFailureStage.DIFF_COMPARE,
                    SafeErrorCode.DIFF_INCOMPARABLE,
                )
            typer.echo(
                "error: cannot diff baselines from different databases "
                f"(a={current_baseline.target.database}, b={latest_baseline.target.database})",
                err=True,
            )
            raise typer.Exit(2)
        _print_target_identity_warning(current_baseline, latest_baseline)
        if not typer.confirm("Do you want to continue?", default=False):
            typer.secho("Diff cancelled by user.", fg=typer.colors.YELLOW)
            return

    try:
        report = compare_baselines(current_baseline, latest_baseline)
    except GraphCheckError as exc:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.DIFF_COMPARE,
                exc.error.code,
            )
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(2) from exc
    except SchemaVersionMismatch as exc:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                CliFailureStage.DIFF_COMPARE,
                SafeErrorCode.DIFF_INCOMPARABLE,
            )
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception:
        if telemetry is not None:
            telemetry.fail(
                ProcessOutcome.UNEXPECTED_ERROR,
                CliFailureStage.DIFF_COMPARE,
                SafeErrorCode.DIFF_FAILED,
            )
        raise
    if isinstance(report, list):  # Compatibility with the original line-oriented hook.
        if not report:
            typer.secho("No drift detected.", fg=typer.colors.GREEN, bold=True)
            return
        typer.secho("Graph drift detected.\n", fg=typer.colors.YELLOW, bold=True)
        for message in report:
            typer.echo(message)
        return
    report = replace(
        report,
        baseline_a=current_baseline_path.name,
        baseline_b=latest_baseline_path.name,
    )
    typer.echo(render_json(report) if json_output else render_human(report))
    if report.drift_detected:
        raise typer.Exit(1)


def _target_identity(target: "RunTarget") -> str:
    return target.database


def _target_identity_json(target: "RunTarget") -> dict[str, str]:
    return {"database": _target_identity(target)}


def _print_target_identity_warning(
    current_baseline: "BaselineProfile",
    latest_baseline: "BaselineProfile",
) -> None:
    typer.secho("WARNING", fg=typer.colors.YELLOW, bold=True)
    typer.echo()
    typer.echo("The selected baseline snapshots belong to different database / target identities.")
    typer.echo()
    typer.echo("Current Baseline")
    typer.echo(json.dumps(_target_identity_json(current_baseline.target), indent=2, sort_keys=True))
    typer.echo()
    typer.echo("Latest Baseline")
    typer.echo(json.dumps(_target_identity_json(latest_baseline.target), indent=2, sort_keys=True))
    typer.echo()
    typer.echo(
        "Comparing baseline snapshots from different databases or targets may produce "
        "misleading drift results."
    )


def _print_profile_summary(
    baseline: "BaselineProfile",
    baseline_path: Path,
) -> None:
    from graphcheck.contracts.profile import ProfileStatus

    if baseline.status is ProfileStatus.PARTIAL:
        typer.secho("Profile completed with partial data.", fg=typer.colors.YELLOW, bold=True)
        typer.echo()
        typer.echo(f"Status: {baseline.status}")
        typer.echo(f"Reason: {baseline.partial_reason}")
        typer.echo(
            f"Collected: {baseline.statistics.node_count} nodes, "
            f"{baseline.statistics.relationship_count} relationships"
        )
        typer.echo()
        typer.echo(f"Baseline written to:\n{baseline_path}")
        return

    typer.secho("Profile completed.", fg=typer.colors.GREEN, bold=True)
    typer.echo()

    typer.echo(f"Status: {baseline.status}")

    if baseline.partial_reason:
        typer.echo(f"Reason: {baseline.partial_reason}")

    typer.echo()

    typer.echo(f"Nodes: {baseline.statistics.node_count}")
    typer.echo(f"Relationships: {baseline.statistics.relationship_count}")

    typer.echo()

    typer.echo(f"Labels: {len(baseline.graph_schema.labels)}")
    typer.echo(f"Relationship Types: {len(baseline.graph_schema.relationship_types)}")
    typer.echo(f"Constraints: {len(baseline.graph_schema.constraints)}")
    typer.echo(f"Indexes: {len(baseline.graph_schema.indexes)}")

    typer.echo()

    typer.echo("Degree Distribution:")
    for label in baseline.graph_schema.labels:
        distribution = label.degree_distribution
        if distribution is None:
            typer.echo(f"  {label.name}: unavailable")
        else:
            typer.echo(
                f"  {label.name}: median={distribution.median}, p95={distribution.p95}, "
                f"p99={distribution.p99}, maximum={distribution.maximum}"
            )

    typer.echo()

    typer.echo("Property Coverage:")
    for coverage in baseline.statistics.property_coverage:
        typer.echo(
            f"  {coverage.owner_name}.{coverage.property} ({coverage.owner}): {coverage.coverage}%"
        )

    typer.echo()

    typer.echo(f"Baseline written to:\n{baseline_path}")


def _validate_report_options(
    *,
    open_report: bool,
    report_id: str | None,
    list_reports: bool,
    compare: tuple[str, str] | None,
    prune: bool,
    keep: int | None,
    failures_only: bool,
) -> None:
    if report_id is not None and not open_report:
        _report_usage_error("A report ID requires --open.")
    if keep is not None and not prune:
        _report_usage_error("--keep requires --prune.")
    if prune and keep is None:
        _report_usage_error("--prune requires --keep COUNT.")
    if keep is not None and keep < 1:
        _report_usage_error("--keep must be at least 1.")

    standalone = sum((list_reports, compare is not None, prune))
    selection_actions = open_report or failures_only
    if standalone > 1 or (standalone and selection_actions):
        _report_usage_error(
            "--list, --compare, and --prune are standalone actions and cannot be combined "
            "with other report actions."
        )


def _report_usage_error(message: str) -> None:
    if (telemetry := _command_telemetry()) is not None:
        telemetry.fail(
            ProcessOutcome.USER_ERROR,
            CliFailureStage.CONFIG_LOAD,
            SafeErrorCode.CONFIG_INVALID,
        )
    typer.echo(f"report.usage: {message}", err=True)
    raise typer.Exit(2)


def _report_action(
    *,
    open_report: bool,
    list_reports: bool,
    compare: tuple[str, str] | None,
    prune: bool,
    failures_only: bool,
) -> CommandAction | None:
    if list_reports:
        return CommandAction.LIST
    if compare is not None:
        return CommandAction.COMPARE
    if prune:
        return CommandAction.PRUNE
    if failures_only:
        return CommandAction.FAILURES_ONLY
    if open_report:
        return CommandAction.OPEN
    return None


def _cli_stage_for_error(code: str) -> CliFailureStage:
    if code == "project.missing":
        return CliFailureStage.PROJECT_DISCOVERY
    if code.startswith("profile."):
        return CliFailureStage.PROFILE_LOAD
    if "suite" in code or "checks_" in code:
        return CliFailureStage.SUITE_LOAD
    if code.startswith("neo4j."):
        return CliFailureStage.PROBE
    if code.startswith("report."):
        return CliFailureStage.REPORT_RENDER
    if code.startswith("generate.baseline_"):
        return CliFailureStage.BASELINE_LOAD
    if code.startswith("generate.doc_"):
        return CliFailureStage.DOCUMENT_LOAD
    if code.startswith("generate.provider_"):
        return CliFailureStage.PROVIDER_REQUEST
    if code in {"generate.output_invalid", "generate.no_valid_candidates"}:
        return CliFailureStage.GENERATION_VALIDATION
    if code.startswith("generate.write_"):
        return CliFailureStage.ARTIFACT_WRITE
    return CliFailureStage.CONFIG_LOAD


def _telemetry_preview_payload() -> dict[str, object]:
    from graphcheck.telemetry.policy import assert_private_payload

    common = {
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "consent_version": CONSENT_VERSION,
        "graphcheck_version": __version__,
        "distinct_id": "<installation-uuid>",
        "session_id": "<process-uuid>",
        "telemetry_command_id": "<command-uuid>",
        "process_person_profile": False,
        "geoip_enrichment": False,
        "$process_person_profile": False,
        "$geoip_disable": True,
    }
    events = [
        {
            "event": "graphcheck_run_started",
            "properties": {
                **common,
                "telemetry_run_id": "<run-uuid>",
                "suite_count": 1,
                "selected_check_count": 3,
                "uses_sampling": True,
                "uses_baselines": False,
                "fail_fast_enabled": False,
            },
        },
        {
            "event": "graphcheck_check_processed",
            "properties": {
                **common,
                "telemetry_run_id": "<run-uuid>",
                "check_sequence": 1,
                "pattern": "conformance",
                "template": "existence",
                "processing_outcome": "completed",
                "query_count": 1,
            },
        },
        {
            "event": "graphcheck_run_completed",
            "properties": {
                **common,
                "telemetry_run_id": "<run-uuid>",
                "terminal_kind": "finished",
                "outcome": "complete",
                "selected_check_count": 3,
                "query_count": 3,
            },
        },
        {
            "event": "graphcheck_engine_faulted",
            "properties": {
                **common,
                "telemetry_run_id": "<run-uuid>",
                "engine_stage": "finalize",
                "exception_type": "RuntimeError",
                "safe_error_code": "engine.unexpected",
                "elapsed_ms": 125,
            },
        },
        {
            "event": "graphcheck_command_completed",
            "properties": {
                **common,
                "command": "run",
                "action": None,
                "process_outcome": "success",
                "failure_stage": None,
                "telemetry_run_id": "<run-uuid>",
                "os_family": "linux",
                "os_version": "6.8",
                "python_minor": "3.12",
            },
        },
        {
            "event": "graphcheck_profile_completed",
            "properties": {
                **common,
                "outcome": "complete",
                "duration_ms": 900,
                "schema_ms": 200,
                "property_coverage_ms": 400,
                "degree_distribution_ms": 200,
                "deadline_exhausted": False,
                "last_completed_stage": "degree_distribution",
                "partial_reason": None,
                "safe_error_code": None,
            },
        },
    ]
    for event in events:
        assert_private_payload(event["properties"])
    return {"sent": False, "events": events}


def _telemetry_delivery_configured() -> bool:
    from graphcheck.telemetry.posthog import telemetry_delivery_configured

    return telemetry_delivery_configured()


def _print_report_command_help() -> None:
    typer.echo(
        "Report commands:\n"
        "  graphcheck report --open [ID]         Open the latest or a selected report.\n"
        "  graphcheck report --list              List available report IDs.\n"
        "  graphcheck report --compare ID1 ID2   Compare two report outcomes.\n"
        "  graphcheck report --prune --keep N    Retain the newest N historical runs.\n"
        "  graphcheck report --failures-only     Write a diagnostic-only report.\n"
        "Use `graphcheck report --help` for option details."
    )


def _latest_run(records: list["ReportRun"]) -> "ReportRun":
    from graphcheck.reporting.history import ReportHistoryError

    if not records:
        raise ReportHistoryError(
            "No results.json found in report history. Run `graphcheck run` first."
        )
    return records[0]


def _latest_html_report(runs_dir: Path) -> Path:
    from graphcheck.reporting.history import ReportHistoryError

    reports = list(runs_dir.rglob("report.html")) if runs_dir.is_dir() else []
    if not reports:
        raise ReportHistoryError(
            f"No report.html found under {runs_dir}. Run `graphcheck run` to generate one first."
        )
    return max(reports, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _open_html_report(path: Path) -> None:
    from graphcheck.reporting.history import ReportHistoryError

    if not path.is_file():
        raise ReportHistoryError(f"No report.html found for the selected run at {path}.")
    try:
        opened = webbrowser.open(path.resolve().as_uri())
    except (OSError, webbrowser.Error) as exc:
        raise ReportHistoryError(f"Could not open {path} in the default browser: {exc}") from exc
    if not opened:
        raise ReportHistoryError(f"Could not open {path} in the default browser.")
    typer.secho(f"Opened {path}", fg=typer.colors.CYAN)


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Start the GraphCheck MCP server."""
    _call("graphcheck.mcp.server", "run")


@app.command("run")
@_telemetry_command(CommandName.RUN)
def run_command(
    profile: str | None = typer.Option(None, "--profile", help="Connection profile to use."),
    select: list[str] | None = typer.Option(  # noqa: B008 - Typer declaration
        None,
        "--select",
        help="Select checks by tag (for example, --select tag:production). Repeatable.",
    ),
    suite: list[str] | None = typer.Option(  # noqa: B008 - Typer declaration
        None,
        "--suite",
        help="Run only this suite id. Repeatable.",
    ),
    fail_fast: bool = typer.Option(
        False,
        "--fail-fast",
        help="Stop after the first error-severity failure and mark later checks not run.",
    ),
    concurrency: int | None = typer.Option(
        None,
        "--concurrency",
        min=1,
        help="Maximum concurrent checks; overrides graphcheck.yml.",
    ),
    redacted: bool = typer.Option(
        False,
        "--redact",
        "--redacted",
        help="Mask parameter, expected, query, and evidence literal values in exported artifacts.",
    ),
) -> None:
    """Execute selected check suites and write machine and offline reports."""
    from graphcheck.application.run import RunRequest, execute_run
    from graphcheck.contracts.results import CheckError
    from graphcheck.engine import failed_results
    from graphcheck.errors import GraphCheckError
    from graphcheck.project import ARTIFACTS_DIR, load_project_config

    requested_suites = list(dict.fromkeys(suite or []))
    root: Path | None = None
    runs_dir: Path | None = None
    tags: list[str] = []
    results_path: Path | None = None
    report_path: Path | None = None
    setup_started = time.monotonic()
    telemetry = _command_telemetry()

    artifact_started = time.monotonic()
    render_times: list[int] = []
    render_failed = False
    check_count = 0
    progress_scope = ExitStack()
    run_progress_callback: Callable[[int, int, str], None] | None = None

    def observe_render(duration_ms: int, succeeded: bool) -> None:
        nonlocal render_failed
        render_times.append(duration_ms)
        render_failed = render_failed or not succeeded

    def display_error(error: CheckError) -> CheckError:
        # Mask the human-facing message and fix when the run is redacted.
        return (
            CheckError(code=error.code, message="[REDACTED]", fix="[REDACTED]")
            if redacted
            else error
        )

    def export_run_artifacts(run_results, target_runs_dir, *, render_observer=None):
        # Redact parameter, expected, query, and evidence literals in the written artifacts
        # when --redact is set. The in-memory results stay unredacted so telemetry and the
        # engine result are unaffected.
        exported = redact_results(run_results) if redacted else run_results
        return _write_run_artifacts(exported, target_runs_dir, render_observer=render_observer)

    def show_run_target(target: object) -> None:
        # The shared service calls this once the target is probed and the credential is
        # verified, before any check runs. Print the target header first (unless redacted),
        # then open the progress bar so the header always precedes progress output.
        nonlocal run_progress_callback
        if not redacted:
            _print_run_target(target)
        run_progress_callback = progress_scope.enter_context(
            _run_progress(check_count, redacted=redacted)
        )

    def forward_progress(completed: int, total: int, check_name: str) -> None:
        if run_progress_callback is not None:
            run_progress_callback(completed, total, check_name)

    def report_artifact_failure(exc: Exception) -> None:
        # Shared reporting for the two artifact-write failure paths. A render failure is
        # reported as a report-render failure, not an artifact-write failure, and the printed
        # cause is masked when the run is redacted.
        if telemetry is not None:
            telemetry.render_ms = sum(render_times) if render_times else None
            telemetry.mark_artifacts(
                artifact_started,
                results=ArtifactOutcome.ERROR,
                report=ArtifactOutcome.ERROR,
                exclude_ms=sum(render_times),
            )
            telemetry.fail(
                ProcessOutcome.UNEXPECTED_ERROR,
                CliFailureStage.REPORT_RENDER if render_failed else CliFailureStage.ARTIFACT_WRITE,
                SafeErrorCode.REPORT_RENDER_FAILED
                if render_failed
                else SafeErrorCode.ARTIFACT_WRITE_FAILED,
            )
        if results.run.error is not None:
            _print_setup_error(display_error(results.run.error))
        typer.secho(
            "run.artifact_failed: Could not write run artifacts: "
            f"{'[REDACTED]' if redacted else exc}",
            fg=typer.colors.RED,
            bold=True,
            err=True,
        )
        typer.secho(
            "Fix: Check the configured artifacts path and filesystem permissions.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    try:
        root = find_project_root()
        runs_dir = root / ARTIFACTS_DIR / "runs"
        config = load_project_config(root)
        artifacts = _project_path(root, config.artifacts)
        runs_dir = artifacts / "runs"
        tags = _selection_tags(select or [])
        suite_inputs = _load_suite_inputs(
            _project_path(root, config.checks),
            requested_suites,
        )
        check_count = _selected_check_count(suite_inputs, tags)

        with progress_scope:
            outcome = execute_run(
                RunRequest(
                    profile=profile,
                    suite_ids=requested_suites,
                    tags=tags,
                    fail_fast=fail_fast,
                    concurrency=concurrency,
                    verify_read_only_credential=True,
                ),
                progress_callback=forward_progress,
                event_sink=telemetry.event_sink if telemetry is not None else None,
                render_observer=observe_render if telemetry is not None else None,
                client_factory=_new_neo4j_client,
                artifact_writer=export_run_artifacts,
                target_observer=show_run_target,
            )

        results = outcome.results
        results_path = outcome.results_path
        report_path = outcome.report_path

        # Attribute time to the real boundaries the shared service reports, not the whole
        # command: setup_ms ends once client setup completes, and artifact_write_ms starts
        # immediately before publication.
        if telemetry is not None:
            if outcome.setup_done_perf is not None:
                telemetry.setup_ms = max(0, round((outcome.setup_done_perf - setup_started) * 1000))
            elif telemetry.setup_ms is None:
                telemetry.mark_setup(setup_started)
        if outcome.artifact_started_perf is not None:
            artifact_started = outcome.artifact_started_perf

        if outcome.artifact_error is not None:
            report_artifact_failure(outcome.artifact_error)
            raise typer.Exit(3)

    except typer.Exit:
        raise

    except GraphCheckError as exc:
        if telemetry is not None:
            if telemetry.setup_ms is None:
                telemetry.mark_setup(setup_started)
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                _cli_stage_for_error(exc.error.code),
                exc.error.code,
            )
        if runs_dir is None:
            _print_setup_error(display_error(exc.error))
            raise typer.Exit(3) from exc
        results = failed_results(
            exc.error,
            suite_ids=requested_suites,
            tags=tags,
            fail_fast=fail_fast,
        )

    except Exception as exc:
        if telemetry is not None:
            if telemetry.setup_ms is None:
                telemetry.mark_setup(setup_started)
            telemetry.fail(
                (
                    ProcessOutcome.ENGINE_ERROR
                    if telemetry.telemetry_run_id is not None
                    else ProcessOutcome.UNEXPECTED_ERROR
                ),
                (
                    CliFailureStage.ENGINE
                    if telemetry.telemetry_run_id is not None
                    else CliFailureStage.CONFIG_LOAD
                ),
                (
                    SafeErrorCode.ENGINE_UNEXPECTED
                    if telemetry.telemetry_run_id is not None
                    else SafeErrorCode.CONFIG_INVALID
                ),
            )
        error = CheckError(
            code="run.configuration",
            message=f"GraphCheck could not prepare the run: {type(exc).__name__}: {exc}",
            fix="Fix the project configuration, then run `graphcheck debug` and try again.",
        )
        if runs_dir is None:
            _print_setup_error(display_error(error))
            raise typer.Exit(3) from exc
        results = failed_results(
            error,
            suite_ids=requested_suites,
            tags=tags,
            fail_fast=fail_fast,
        )

    if results_path is None:
        assert runs_dir is not None

        artifact_started = time.monotonic()
        try:
            results_path, report_path = export_run_artifacts(
                results,
                runs_dir,
                render_observer=observe_render if telemetry is not None else None,
            )
        except Exception as artifact_exc:
            report_artifact_failure(artifact_exc)
            raise typer.Exit(3) from artifact_exc

    if (
        results.run.status.value == "failed"
        and telemetry is not None
        and telemetry.process_outcome is ProcessOutcome.SUCCESS
    ):
        # A failed run.status here comes from the shared service converting a preflight,
        # configuration, or unexpected-engine error into a result. Classify an engine fault as
        # ENGINE / ENGINE_ERROR and everything else by its error code, so preflight failures
        # are not mislabelled as engine errors and engine faults are not mislabelled as user
        # configuration errors.
        error_code = results.run.error.code if results.run.error is not None else None
        if error_code == "engine.unexpected":
            telemetry.fail(
                ProcessOutcome.ENGINE_ERROR,
                CliFailureStage.ENGINE,
                SafeErrorCode.ENGINE_UNEXPECTED,
            )
        else:
            telemetry.fail(
                ProcessOutcome.USER_ERROR,
                _cli_stage_for_error(error_code)
                if error_code is not None
                else CliFailureStage.ENGINE,
                error_code,
            )

    if telemetry is not None:
        telemetry.render_ms = sum(render_times)
        telemetry.mark_artifacts(
            artifact_started,
            results=ArtifactOutcome.WRITTEN,
            report=ArtifactOutcome.WRITTEN,
            exclude_ms=sum(render_times),
        )

    exported_results = redact_results(results) if redacted else results
    _print_run_summary(
        exported_results,
        results_path,
        report_path,
        display_run_id=results.run.id,
    )
    raise typer.Exit(results.run.exit_code)


@app.command("redact")
def redact_command(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            help="Existing results.json file or run directory to redact.",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Destination file (default: results.redacted.json beside the source).",
        ),
    ] = None,
) -> None:
    """Create a verified, mask-redacted results export from an existing run."""

    source_path = source / "results.json" if source.is_dir() else source
    if not source_path.is_file():
        raise typer.BadParameter(f"No results.json found at {source_path}", param_hint="source")
    redacted_name = f"{source_path.stem}.redacted{source_path.suffix}"
    destination = output or source_path.with_name(redacted_name)
    if destination.resolve() == source_path.resolve():
        raise typer.BadParameter("output must differ from source", param_hint="--output")
    redacted = redact_results(source_path.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_results(redacted, destination)
    typer.secho(f"Verified redacted export written to {destination}", fg=typer.colors.GREEN)


def _new_neo4j_client(profile, max_concurrency: int):
    """Construct the real workload-aware client while retaining simple CLI test doubles."""

    import inspect

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


def _selected_check_count(suites: Sequence["SuiteInput"], tags: Sequence[str]) -> int:
    return sum(
        1
        for suite_input in suites
        for check in suite_input.suite.checks
        if not tags or any(tag in check.tags for tag in tags)
    )


def _interactive_stderr() -> bool:
    return bool(getattr(sys.stderr, "isatty", lambda: False)())


def _elapsed_clock(started: float) -> str:
    minutes, seconds = divmod(max(0, int(time.monotonic() - started)), 60)
    return f"{minutes:02d}:{seconds:02d}"


def _progress_template(check_name: str) -> str:
    return "%(label)s  [%(bar)s]  %(info)s Complete | Checking: " + check_name.replace("%", "%%")


_PROGRESS_FILL_CHAR = "━"
_PROGRESS_EMPTY_CHAR = "─"


@contextmanager
def _run_progress(
    total_checks: int,
    *,
    redacted: bool = False,
) -> Iterator[Callable[[int, int, str], None] | None]:
    if total_checks == 0 or not _interactive_stderr():
        yield None
        return

    started = time.monotonic()
    state = {"check": "Preparing redacted graph checks" if redacted else "Preparing graph checks"}
    lock = threading.Lock()
    stopped = threading.Event()
    with typer.progressbar(
        length=total_checks,
        label="00:00",
        file=sys.stderr,
        show_eta=False,
        show_percent=True,
        show_pos=True,
        fill_char=typer.style(_PROGRESS_FILL_CHAR, fg=typer.colors.GREEN),
        empty_char=typer.style(_PROGRESS_EMPTY_CHAR, fg=typer.colors.BRIGHT_BLACK),
        width=28,
        color=True,
        bar_template=_progress_template(state["check"]),
    ) as bar:

        def refresh() -> None:
            with lock:
                bar.label = _elapsed_clock(started)
                bar.bar_template = _progress_template(state["check"])
                render = getattr(bar, "render_progress", None)
                if callable(render):
                    render()

        def tick() -> None:
            while not stopped.wait(1):
                refresh()

        ticker = threading.Thread(target=tick, name="graphcheck-progress-clock", daemon=True)
        ticker.start()

        def update(completed: int, total: int, check_name: str) -> None:
            display_name = "redacted check" if redacted else check_name
            with lock:
                state["check"] = display_name
                bar.label = _elapsed_clock(started)
                bar.bar_template = _progress_template(display_name)
                bar.update(1)

        try:
            yield update
        finally:
            stopped.set()
            ticker.join(timeout=1)


def _selection_tags(selectors: list[str]) -> list[str]:
    from graphcheck.errors import GraphCheckError

    tags: list[str] = []
    for selector in selectors:
        kind, separator, value = selector.partition(":")
        if separator != ":" or kind != "tag" or not value.strip():
            raise GraphCheckError(
                "run.invalid_selector",
                f"Unsupported selector {selector!r}.",
                "Use `--select tag:<name>`; repeat the option to match any selected tag.",
            )
        tag = value.strip()
        if tag not in tags:
            tags.append(tag)
    return tags


def _load_suite_inputs(checks_dir: Path, requested_suites: list[str]) -> list["SuiteInput"]:
    from graphcheck.engine import SuiteInput
    from graphcheck.errors import GraphCheckError

    if not checks_dir.is_dir():
        raise GraphCheckError(
            "run.checks_missing",
            f"Configured checks directory was not found: {checks_dir}",
            "Create the directory or fix `checks` in graphcheck.yml.",
        )
    try:
        paths = sorted(
            path
            for path in checks_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
        )
    except OSError as exc:
        raise GraphCheckError(
            "run.checks_unreadable",
            f"Could not enumerate check suites in {checks_dir}: {exc}",
            "Check the configured checks path and its filesystem permissions.",
        ) from exc

    requested = set(requested_suites)
    loaded: list[SuiteInput] = []
    for path in paths:
        try:
            loaded.append(SuiteInput.from_yaml(path.read_text(encoding="utf-8"), source=str(path)))
        except Exception as exc:
            raise GraphCheckError(
                "run.suite_invalid",
                f"Suite {path} is invalid: {type(exc).__name__}: {exc}",
                "Fix the suite YAML and remove unknown keys, then run it again.",
            ) from exc

    return [item for item in loaded if not requested or item.suite.suite in requested]


def _project_path(root: Path, configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else root / path


def _write_run_artifacts(
    results: "Results",
    runs_dir: Path,
    *,
    render_observer: Callable[[int, bool], None] | None = None,
) -> tuple[Path, Path]:
    """Delegate to the shared artifact writer.

    Kept as a lazy, module-level CLI entry point so the run command and the CLI tests share
    one implementation with the MCP surface while preserving fast startup.
    """
    from graphcheck.application.artifacts import write_run_artifacts

    return write_run_artifacts(results, runs_dir, render_observer=render_observer)


def _print_setup_error(error: "CheckError") -> None:
    typer.secho(f"{error.code}: {error.message}", fg=typer.colors.RED, bold=True, err=True)
    typer.secho(f"Fix: {error.fix}", fg=typer.colors.YELLOW, err=True)


def _run_status_color(status: str) -> str:
    return {
        "complete": typer.colors.GREEN,
        "partial": typer.colors.MAGENTA,
        "failed": typer.colors.RED,
    }.get(status, typer.colors.WHITE)


def _exit_code_color(exit_code: int) -> str:
    return {
        0: typer.colors.GREEN,
        1: typer.colors.RED,
        2: typer.colors.YELLOW,
        3: typer.colors.RED,
    }.get(exit_code, typer.colors.WHITE)


def _print_run_target(target) -> None:
    if target is None:
        return
    nodes = "unavailable" if target.nodes is None else f"{target.nodes:,}"
    relationships = "unavailable" if target.relationships is None else f"{target.relationships:,}"
    typer.echo(
        f"Target: {target.database} · Neo4j {target.server_version} {target.edition} · "
        f"{nodes} nodes · {relationships} relationships"
    )


def _suite_score_style(score: int | None) -> str:
    if score is None:
        return "white"
    if score == 100:
        return "green"
    return "yellow" if score >= 50 else "red"


def _suite_coverage_style(evaluated: int, selected: int) -> str:
    return "green" if evaluated == selected else "yellow"


def _summary_table():
    from rich import box
    from rich.table import Table

    return Table(
        box=box.SIMPLE_HEAD,
        safe_box=False,
        show_edge=False,
        show_lines=False,
        pad_edge=False,
        collapse_padding=True,
        header_style="bold white",
    )


def _suite_score_table(results: "Results"):
    from rich.text import Text

    table = _summary_table()
    table.add_column("Suite", no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Check Coverage", justify="right", no_wrap=True)
    for heading in ("Passed", "Failed", "Warnings", "Errored", "Skipped"):
        table.add_column(heading, justify="right", width=len(heading), no_wrap=True)
    if not results.suites:
        table.add_row(*(Text("n/a", style="bright_black") for _ in range(8)))
        return table
    state_colors = ("green", "red", "yellow", "magenta", "bright_black")
    for suite in results.suites:
        score = "n/a" if suite.score is None else f"{suite.score}/100"
        evaluated = suite.totals.checks - suite.totals.skipped
        counts = (
            suite.totals.passed,
            suite.totals.fail,
            suite.totals.warn,
            suite.totals.errored,
            suite.totals.skipped,
        )
        table.add_row(
            Text(suite.id, style="italic"),
            Text(score, style=_suite_score_style(suite.score)),
            Text(
                f"{evaluated}/{suite.totals.checks}",
                style=_suite_coverage_style(evaluated, suite.totals.checks),
            ),
            *(
                Text(str(count), style=color) if count else Text("-")
                for count, color in zip(counts, state_colors, strict=True)
            ),
        )
    return table


def _print_suite_score_table(results: "Results") -> None:
    from rich.console import Console

    typer.echo("Score breakdown by check suite:")
    typer.echo()
    Console(highlight=False).print(_suite_score_table(results))


def _result_text(presentation, *, has_not_evaluated: bool = False):
    from rich.text import Text

    text = Text(presentation.primary_sentence)
    if has_not_evaluated:
        text.append(
            " Coverage is incomplete due to the following check(s) which have not been evaluated:"
        )
        return text
    if not presentation.coverage_incomplete:
        return text
    text.append(" Coverage is incomplete.")
    if not presentation.skipped_suites:
        return text
    text = Text(presentation.primary_sentence)
    text.append(" Coverage is incomplete due to skipped check(s) from ")
    for index, suite in enumerate(presentation.skipped_suites):
        if index:
            text.append(", ")
        text.append(suite, style="italic")
    text.append(".")
    return text


def _print_result(presentation, *, has_not_evaluated: bool = False) -> None:
    from rich.console import Console
    from rich.text import Text

    Console(highlight=False).print(
        Text("Result: ") + _result_text(presentation, has_not_evaluated=has_not_evaluated),
        soft_wrap=True,
    )


def _not_evaluated_table(results: "Results"):
    from rich.text import Text

    from graphcheck.reporting.presentation import present_check

    table = _summary_table()
    table.add_column("Suite", no_wrap=True)
    table.add_column("Check")
    table.add_column("Reason")
    skipped = sorted(
        (check for check in results.checks if not check.executed),
        key=lambda check: (check.suite_id, check.id),
    )
    for check in skipped:
        reason = present_check(check).skip_reason
        assert reason is not None
        name = Text(check.name, style="italic")
        name.append(f" ({check.id})", style="dim")
        table.add_row(
            Text(check.suite_id, style="italic"),
            name,
            f"{reason.code}: {reason.explanation}",
        )
    return table


def _print_not_evaluated(results: "Results") -> None:
    from rich.console import Console

    if not results.totals.skipped:
        return
    typer.echo()
    Console(highlight=False).print(_not_evaluated_table(results))
    typer.echo()


def _execution_error_table(results: "Results"):
    from rich.text import Text

    table = _summary_table()
    include_fix = not results.run.redaction.applied
    table.add_column("Suite", no_wrap=True)
    table.add_column("Check")
    table.add_column("Reason")
    if include_fix:
        table.add_column("Fix")
    for check in sorted(
        (check for check in results.checks if check.error is not None),
        key=lambda check: (check.suite_id, check.id),
    ):
        name = Text(check.name, style="italic white")
        name.append(f" ({check.id})", style="dim white")
        cells = [
            Text(check.suite_id, style="italic white"),
            name,
            Text(f"{check.error.code}: {check.error.message}", style="magenta"),
        ]
        if include_fix:
            cells.append(Text(check.error.fix, style="white"))
        table.add_row(*cells)
    return table


def _print_execution_errors(results: "Results") -> None:
    from rich.console import Console

    if not results.totals.errored:
        return
    typer.echo(
        "The following check(s) have execution errors:"
        if results.run.redaction.applied
        else "The following check(s) have execution errors. Suggested fixes are provided:"
    )
    typer.echo()
    Console(highlight=False).print(_execution_error_table(results))
    typer.echo()


def _print_run_summary(
    results: "Results",
    results_path: Path,
    report_path: Path,
    *,
    display_run_id: str | None = None,
) -> None:
    from graphcheck.reporting.history import display_run_status
    from graphcheck.reporting.presentation import present_results

    totals = results.totals
    presentation = present_results(results)
    status = display_run_status(results).value
    run_label = "GraphCheck redacted run" if results.run.redaction.applied else "GraphCheck run"
    typer.echo(
        f"{run_label} {display_run_id or results.run.id}: "
        f"{typer.style(status, fg=_run_status_color(status), bold=True)}"
    )
    _print_suite_score_table(results)
    typer.echo()
    if results.run.partial_reason is not None:
        typer.echo(f"Partial: {results.run.partial_reason}")
    _print_result(presentation, has_not_evaluated=bool(totals.skipped))
    _print_not_evaluated(results)
    if results.run.error is not None:
        _print_setup_error(results.run.error)
    _print_execution_errors(results)
    if not any(check.measured is not None for check in results.checks) and (
        results.run.error is not None or any(check.error is not None for check in results.checks)
    ):
        typer.echo(
            "Nothing to evaluate: no selected check produced a measured result. "
            "Fix: adjust the selection or resolve the reported errors or skip reasons, then rerun."
        )
    elif (
        results.run.target is not None
        and results.run.target.nodes == 0
        and results.run.target.relationships == 0
    ):
        typer.echo(
            "Empty graph: checks were evaluated against zero nodes and relationships; "
            "load data if this was unexpected."
        )
    typer.echo(f"Results and Report saved to: {results_path.parent}")
    typer.secho(
        f"Exit code: {results.run.exit_code}",
        fg=_exit_code_color(results.run.exit_code),
        bold=True,
    )
    typer.echo()
