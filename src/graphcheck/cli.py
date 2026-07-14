import json
from pathlib import Path

import typer

from graphcheck import __version__
from graphcheck.baselines import set_current_baseline, write_baseline
from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient, debug_trace, error_json
from graphcheck.profiler import profile as build_profile
from graphcheck.project import (
    PROJECT_FILE,
    ensure_gitignore_entries,
    find_project_root,
    write_default_project,
    write_example_suite,
)

app = typer.Typer(
    name="graphcheck",
    help="Semantic observability for property graphs.",
    add_completion=False,
    no_args_is_help=True,
)
baseline_app = typer.Typer(help="Manage baseline snapshots.")
app.add_typer(baseline_app, name="baseline")


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
def profile(
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Connection profile to use.",
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

    except GraphCheckError as exc:
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(path)


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
