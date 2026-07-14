import json
import webbrowser
from pathlib import Path

import typer

from graphcheck import __version__
from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
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
) -> None:
    """Work with generated GraphCheck reports."""
    if not open_report:
        typer.echo("Use `graphcheck report --open` to open the most recent HTML report.")
        return

    try:
        root = find_project_root()
        config = load_project_config(root)
    except GraphCheckError as exc:
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc

    runs_dir = root / config.artifacts / "runs"
    reports = list(runs_dir.rglob("report.html")) if runs_dir.is_dir() else []
    if not reports:
        typer.echo(f"No report.html found under {runs_dir}.", err=True)
        typer.echo("Fix: Run `graphcheck run` to generate a report first.", err=True)
        raise typer.Exit(1)

    latest = max(reports, key=lambda path: (path.stat().st_mtime_ns, str(path)))
    if not webbrowser.open(latest.resolve().as_uri()):
        typer.echo(f"Could not open {latest} in the default browser.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Opened {latest}")
