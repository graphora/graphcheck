import json
import logging
import shutil
import sys
import uuid
import webbrowser
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import typer
from pydantic import ValidationError

from graphcheck import __version__
from graphcheck.baselines import resolve_diff_baselines, set_current_baseline, write_baseline
from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
from graphcheck.contracts.results import CheckError, Results, RunTarget, Verdict
from graphcheck.debug_diagnostics import CapabilityContext, blocked_checks_for_project
from graphcheck.diff import SchemaVersionMismatch, compare, render_human, render_json
from graphcheck.engine import DirectoryBaselineProvider, Engine, SuiteInput, failed_results
from graphcheck.errors import GraphCheckError
from graphcheck.mcp.server import run as run_mcp_server
from graphcheck.neo4j_adapter import Neo4jClient, debug_trace, error_json, init_trace
from graphcheck.profiler import profile as build_profile
from graphcheck.project import (
    ARTIFACTS_DIR,
    PROJECT_FILE,
    ensure_gitignore_entries,
    find_project_root,
    load_project_config,
    write_default_project,
    write_example_suite,
)
from graphcheck.reporting import (
    ReportHistoryError,
    ReportRun,
    discover_report_runs,
    find_report_run,
    format_report_comparison,
    format_report_history,
    prune_report_runs,
    write_html_report,
    write_results,
)

_DIAGNOSTIC_VERDICTS = {Verdict.FAIL, Verdict.WARN, Verdict.ERRORED}
_NEO4J_NOTIFICATION_LOGGER = "neo4j.notifications"

# Kept as an injection point for integrations that patched the original comparator.
compare_baselines = compare

app = typer.Typer(
    name="graphcheck",
    help="Semantic observability for property graphs.",
    add_completion=False,
    no_args_is_help=True,
)
baseline_app = typer.Typer(help="Manage baseline snapshots.")
app.add_typer(baseline_app, name="baseline")

mcp_app = typer.Typer(help="Start the GraphCheck MCP server.")
app.add_typer(mcp_app, name="mcp")

def _version(value: bool) -> None:
    if value:
        typer.echo(f"graphcheck {__version__}")
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


@app.command()
def init() -> None:
    """Scaffold a new GraphCheck project in the current directory."""
    root = Path.cwd()
    write_default_project(root)
    write_default_profiles(root)
    ensure_gitignore_entries(root)
    write_example_suite(root)

    typer.echo(f"Wrote {PROJECT_FILE}")
    typer.echo("Wrote profiles.yml")
    typer.echo("Wrote checks/example.yml with 3 sample checks")

    profiles = load_profiles(root)
    profile_name, profile = select_profile(profiles)
    try:
        trace = init_trace(profile_name, profile)
    except GraphCheckError as exc:
        typer.echo(f"Neo4j was not detected: {exc.error.code}")
        typer.echo(exc.error.message)
        typer.echo(f"Fix: {exc.error.fix}")
    else:
        typer.echo(f"Detected Neo4j at {profile.uri} (version {trace.target.server_version})")
        typer.echo(f"APOC: {'yes' if trace.target.capabilities.apoc else 'no'}")
    typer.echo("Next: edit checks/example.yml, then run `graphcheck run`")


@app.command()
def debug(
    profile: str | None = typer.Option(None, "--profile", help="Connection profile to use."),
    json_output: bool = typer.Option(False, "--json", help="Emit the stable debug JSON trace."),
) -> None:
    """Diagnose the configured Neo4j connection."""
    profile_name = profile or "local"
    try:
        root = find_project_root()
        profiles = load_profiles(root)
        profile_name, selected = select_profile(profiles, profile)
        trace = debug_trace(profile_name, selected)
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
        payload = error_json(profile_name, exc.error)
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
            typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(trace.as_json(), indent=2, sort_keys=True))
        return

    caps = trace.target.capabilities
    typer.echo(f"Profile: {trace.profile}")
    typer.echo(f"Neo4j version: {trace.target.server_version}")
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

    try:
        root = find_project_root()
        profiles = load_profiles(root)
        _, selected = select_profile(profiles, profile)

        client = Neo4jClient(selected)
        try:
            baseline = build_profile(client)
        finally:
            client.close()

        path = write_baseline(baseline)
        if json_output:
            typer.echo(baseline.model_dump_json(indent=2, by_alias=True))
        else:
            _print_profile_summary(
                baseline,
                path,
            )
    except GraphCheckError as exc:
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc


@app.command()
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

    try:
        root = find_project_root()
        config = load_project_config(root)
    except GraphCheckError as exc:
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
            write_html_report(record.results, output, verdicts=_DIAGNOSTIC_VERDICTS)
            typer.echo(f"Wrote {output}")
            if open_report:
                _open_html_report(output)
            return

        if open_report:
            if report_id is not None:
                record = find_report_run(discover_report_runs(runs_dir), report_id)
                _open_html_report(record.report_path)
            else:
                _open_html_report(_latest_html_report(runs_dir))
    except ReportHistoryError as exc:
        typer.echo(f"report.error: {exc}", err=True)
        raise typer.Exit(1) from exc


@baseline_app.command("set")
def baseline_set(
    filename: str | None = typer.Argument(
        None,
        help="Timestamped baseline filename to activate; defaults to the newest snapshot.",
    ),
) -> None:
    """Select an existing snapshot as the active baseline."""
    try:
        selected = set_current_baseline(filename)
    except GraphCheckError as exc:
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Baseline set to {selected.name}")


@app.command("diff")
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
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(2) from exc
    except SchemaVersionMismatch as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        typer.echo(f"error: unable to read baseline: {exc}", err=True)
        raise typer.Exit(2) from exc

    if _target_identity(current_baseline.target) != _target_identity(latest_baseline.target):
        if json_output:
            typer.echo(
                "error: cannot diff baselines from different databases "
                f"(a={current_baseline.target.database}, b={latest_baseline.target.database})",
                err=True,
            )
            raise typer.Exit(2)
        _print_target_identity_warning(current_baseline, latest_baseline)
        if not typer.confirm("Do you want to continue?", default=False):
            typer.echo("Diff cancelled by user.")
            return

    try:
        report = compare_baselines(current_baseline, latest_baseline)
    except SchemaVersionMismatch as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if isinstance(report, list):  # Compatibility with the original line-oriented hook.
        if not report:
            typer.echo("No drift detected.")
            return
        typer.echo("Graph drift detected.\n")
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


def _target_identity(target: RunTarget) -> str:
    return target.database


def _target_identity_json(target: RunTarget) -> dict[str, str]:
    return {"database": _target_identity(target)}


def _print_target_identity_warning(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> None:
    typer.echo("WARNING")
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
    baseline: BaselineProfile,
    baseline_path: Path,
) -> None:
    if baseline.status is ProfileStatus.PARTIAL:
        typer.echo("Profile completed with partial data.")
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

    typer.echo("Profile completed.")
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
    typer.echo(f"report.usage: {message}", err=True)
    raise typer.Exit(2)


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


def _latest_run(records: list[ReportRun]) -> ReportRun:
    if not records:
        raise ReportHistoryError(
            "No results.json found in report history. Run `graphcheck run` first."
        )
    return records[0]


def _latest_html_report(runs_dir: Path) -> Path:
    reports = list(runs_dir.rglob("report.html")) if runs_dir.is_dir() else []
    if not reports:
        raise ReportHistoryError(
            f"No report.html found under {runs_dir}. Run `graphcheck run` to generate one first."
        )
    return max(reports, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _open_html_report(path: Path) -> None:
    if not path.is_file():
        raise ReportHistoryError(f"No report.html found for the selected run at {path}.")
    try:
        opened = webbrowser.open(path.resolve().as_uri())
    except (OSError, webbrowser.Error) as exc:
        raise ReportHistoryError(f"Could not open {path} in the default browser: {exc}") from exc
    if not opened:
        raise ReportHistoryError(f"Could not open {path} in the default browser.")
    typer.echo(f"Opened {path}")

@mcp_app.command("serve")
def mcp_serve() -> None:
    """
    Start the GraphCheck MCP server.
    """
    run_mcp_server()
    
@app.command("run")
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
) -> None:
    """Execute selected check suites and write machine and offline reports."""

    requested_suites = list(dict.fromkeys(suite or []))
    root: Path | None = None
    runs_dir: Path | None = None
    tags: list[str] = []
    client: Neo4jClient | None = None
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
        profiles = load_profiles(root)
        _, selected_profile = select_profile(profiles, profile)
        client = Neo4jClient(selected_profile)
        with _run_progress(_selected_check_count(suite_inputs, tags)) as progress_callback:
            results = Engine(
                client,
                baselines=DirectoryBaselineProvider(artifacts / "baselines"),
                progress_callback=progress_callback,
            ).run(
                suite_inputs,
                tags=tags,
                fail_fast=fail_fast,
                selection_suites=requested_suites or None,
            )
    except GraphCheckError as exc:
        if runs_dir is None:
            _print_setup_error(exc.error)
            raise typer.Exit(3) from exc
        results = failed_results(
            exc.error,
            suite_ids=requested_suites,
            tags=tags,
            fail_fast=fail_fast,
        )
    except Exception as exc:
        error = CheckError(
            code="run.configuration",
            message=f"GraphCheck could not prepare the run: {type(exc).__name__}: {exc}",
            fix="Fix the project configuration, then run `graphcheck debug` and try again.",
        )
        if runs_dir is None:
            _print_setup_error(error)
            raise typer.Exit(3) from exc
        results = failed_results(
            error,
            suite_ids=requested_suites,
            tags=tags,
            fail_fast=fail_fast,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # a close failure must not discard a completed run artifact
                typer.echo(f"Warning: Neo4j driver cleanup failed: {exc}", err=True)

    try:
        assert runs_dir is not None
        results_path, report_path = _write_run_artifacts(results, runs_dir)
    except Exception as exc:
        typer.echo(f"run.artifact_failed: Could not write run artifacts: {exc}", err=True)
        typer.echo("Fix: Check the configured artifacts path and filesystem permissions.", err=True)
        raise typer.Exit(3) from exc

    _print_run_summary(results, results_path, report_path)
    raise typer.Exit(results.run.exit_code)


def _selected_check_count(suites: Sequence[SuiteInput], tags: Sequence[str]) -> int:
    return sum(
        1
        for suite_input in suites
        for check in suite_input.suite.checks
        if not tags or any(tag in check.tags for tag in tags)
    )


def _interactive_stderr() -> bool:
    return bool(getattr(sys.stderr, "isatty", lambda: False)())


@contextmanager
def _run_progress(
    total_checks: int,
) -> Iterator[Callable[[int, int, str], None] | None]:
    if total_checks == 0 or not _interactive_stderr():
        yield None
        return

    with typer.progressbar(
        length=total_checks,
        label="Running graph checks",
        file=sys.stderr,
        show_eta=True,
        show_percent=True,
        show_pos=True,
        fill_char="=",
        empty_char="-",
        width=28,
    ) as bar:

        def update(completed: int, total: int, check_name: str) -> None:
            bar.label = "Checks complete" if completed == total else f"Completed {check_name}"
            bar.update(1)

        yield update


def _selection_tags(selectors: list[str]) -> list[str]:
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


def _load_suite_inputs(checks_dir: Path, requested_suites: list[str]) -> list[SuiteInput]:
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

    loaded: list[SuiteInput] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
            loaded.append(SuiteInput.from_yaml(text, source=str(path)))
        except Exception as exc:
            raise GraphCheckError(
                "run.suite_invalid",
                f"Suite {path} is invalid: {type(exc).__name__}: {exc}",
                "Fix the suite YAML and remove unknown keys, then run it again.",
            ) from exc

    if not requested_suites:
        return loaded
    requested = set(requested_suites)
    return [item for item in loaded if item.suite.suite in requested]


def _project_path(root: Path, configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else root / path


def _write_run_artifacts(results: Results, runs_dir: Path) -> tuple[Path, Path]:
    runs_dir.mkdir(parents=True, exist_ok=True)
    resolved_runs = runs_dir.resolve()
    historical_dir = runs_dir / results.run.id
    if (
        historical_dir.name.casefold() == "latest"
        or historical_dir.resolve().parent != resolved_runs
    ):
        raise ValueError(f"run id cannot be used as an artifact directory: {results.run.id!r}")

    _publish_run_directory(results, historical_dir)
    latest_dir = runs_dir / "latest"
    _publish_run_directory(results, latest_dir)
    return latest_dir / "results.json", latest_dir / "report.html"


def _publish_run_directory(results: Results, directory: Path) -> None:
    """Stage and swap a complete results/report pair without exposing a mixed pair."""

    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = parent / f".{directory.name}.staging-{token}"
    backup = parent / f".{directory.name}.backup-{token}"
    staging.mkdir()
    previous_moved = False
    try:
        write_results(results, staging / "results.json")
        write_html_report(results, staging / "report.html")

        if directory.exists():
            is_junction = getattr(directory, "is_junction", lambda: False)
            if not directory.is_dir() or directory.is_symlink() or is_junction():
                raise OSError(f"refusing to replace linked or non-directory artifact: {directory}")
            directory.replace(backup)
            previous_moved = True
        staging.replace(directory)
    except Exception:
        if previous_moved and backup.exists():
            if directory.exists():
                shutil.rmtree(directory)
            backup.replace(directory)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _print_setup_error(error: CheckError) -> None:
    typer.echo(f"{error.code}: {error.message}", err=True)
    typer.echo(f"Fix: {error.fix}", err=True)


def _print_run_summary(results: Results, results_path: Path, report_path: Path) -> None:
    totals = results.totals
    score = "n/a" if results.score is None else str(results.score.value)
    typer.echo(f"GraphCheck run {results.run.id}: {results.run.status.value}")
    if len(results.suites) > 1:
        for suite in results.suites:
            suite_score = "n/a" if suite.score is None else str(suite.score)
            typer.echo(
                f"Suite {suite.id}: score {suite_score} | checks {suite.totals.checks} | "
                f"passed {suite.totals.passed} | failed {suite.totals.fail} | "
                f"warnings {suite.totals.warn} | errored {suite.totals.errored} | "
                f"skipped {suite.totals.skipped}"
            )
        typer.echo(f"Exit code: {results.run.exit_code}")
    else:
        typer.echo(
            "Checks: "
            f"{totals.checks} | passed {totals.passed} | failed {totals.fail} | "
            f"warnings {totals.warn} | errored {totals.errored} | skipped {totals.skipped}"
        )
        typer.echo(f"Score: {score} | exit code: {results.run.exit_code}")
    if results.run.partial_reason is not None:
        typer.echo(f"Partial: {results.run.partial_reason}")
    if results.run.error is not None:
        _print_setup_error(results.run.error)
    typer.echo(f"Results: {results_path}")
    typer.echo(f"Report: {report_path}")
