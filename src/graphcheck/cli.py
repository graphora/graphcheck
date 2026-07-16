import json
import webbrowser
from pathlib import Path

import typer

from graphcheck import __version__
from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
from graphcheck.contracts.results import Verdict
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import debug_trace, error_json
from graphcheck.project import (
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
)

_DIAGNOSTIC_VERDICTS = {Verdict.FAIL, Verdict.WARN, Verdict.ERRORED}

app = typer.Typer(
    name="graphcheck",
    help="Semantic observability for property graphs.",
    add_completion=False,
    no_args_is_help=True,
)


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
    """GraphCheck — the command surface lands in Week 3 (C6)."""


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
        trace = debug_trace(profile_name, profile)
    except GraphCheckError as exc:
        typer.echo(f"Neo4j was not detected: {exc.error.code}")
        typer.echo(exc.error.message)
        typer.echo(f"Fix: {exc.error.fix}")
    else:
        typer.echo(f"Detected Neo4j at {profile.uri} (version {trace.target.server_version})")
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
    typer.echo(f"Counts: {trace.counts.nodes} nodes, {trace.counts.relationships} relationships")


@app.command()
def report(
    open_report: bool = typer.Option(
        False,
        "--open",
        help="Open the most recent report.html in the default browser.",
    ),
    list_reports: bool = typer.Option(
        False,
        "--list",
        help="List report history with timestamps, scores, and statuses.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run",
        metavar="ID",
        help="Open a specific historical run.",
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
        help="Generate a diagnostic report containing failures, warnings, and errors.",
    ),
) -> None:
    """Work with generated GraphCheck reports."""
    _validate_report_options(
        open_report=open_report,
        list_reports=list_reports,
        run_id=run_id,
        compare=compare,
        prune=prune,
        keep=keep,
        failures_only=failures_only,
    )
    if not any((open_report, list_reports, run_id, compare, prune, failures_only)):
        typer.echo(
            "Use `graphcheck report --open`, `--list`, `--run`, `--compare`, `--prune`, "
            "or `--failures-only`."
        )
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
            record = find_report_run(records, run_id) if run_id else _latest_run(records)
            output = record.directory / "report.failures.html"
            write_html_report(record.results, output, verdicts=_DIAGNOSTIC_VERDICTS)
            typer.echo(f"Wrote {output}")
            if open_report or run_id is not None:
                _open_html_report(output)
            return

        if run_id is not None:
            record = find_report_run(discover_report_runs(runs_dir), run_id)
            _open_html_report(record.report_path)
            return

        _open_html_report(_latest_html_report(runs_dir))
    except ReportHistoryError as exc:
        typer.echo(f"report.error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _validate_report_options(
    *,
    open_report: bool,
    list_reports: bool,
    run_id: str | None,
    compare: tuple[str, str] | None,
    prune: bool,
    keep: int | None,
    failures_only: bool,
) -> None:
    if keep is not None and not prune:
        _report_usage_error("--keep requires --prune.")
    if prune and keep is None:
        _report_usage_error("--prune requires --keep COUNT.")
    if keep is not None and keep < 1:
        _report_usage_error("--keep must be at least 1.")

    standalone = sum((list_reports, compare is not None, prune))
    selection_actions = any((open_report, run_id is not None, failures_only))
    if standalone > 1 or (standalone and selection_actions):
        _report_usage_error(
            "--list, --compare, and --prune are standalone actions and cannot be combined "
            "with other report actions."
        )


def _report_usage_error(message: str) -> None:
    typer.echo(f"report.usage: {message}", err=True)
    raise typer.Exit(2)


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
