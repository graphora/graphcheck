import json
from dataclasses import replace
from pathlib import Path

import typer
from pydantic import ValidationError

from graphcheck import __version__
from graphcheck.baselines import resolve_diff_baselines, set_current_baseline, write_baseline
from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
from graphcheck.contracts.profile import BaselineProfile
from graphcheck.debug_diagnostics import CapabilityContext, blocked_checks_for_project
from graphcheck.diff import SchemaVersionMismatch, compare, render_human, render_json
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient, debug_trace, error_json, init_trace
from graphcheck.profiler import profile as build_profile
from graphcheck.project import (
    PROJECT_FILE,
    ensure_gitignore_entries,
    find_project_root,
    write_default_project,
    write_example_suite,
)

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
                f"- {blocked.suite}/{blocked.check_id} requires "
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
        _print_profile_summary(
            baseline,
            path,
        )

    except GraphCheckError as exc:
        typer.echo(f"{exc.error.code}: {exc.error.message}", err=True)
        typer.echo(f"Fix: {exc.error.fix}", err=True)
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
        if current_data.get("schema_version") != latest_data.get("schema_version"):
            raise SchemaVersionMismatch(
                "cannot diff baselines with different schema_version "
                f"(a={current_data.get('schema_version')}, "
                f"b={latest_data.get('schema_version')})"
            )
        current_baseline = BaselineProfile.model_validate_json(current_raw)
        latest_baseline = BaselineProfile.model_validate_json(latest_raw)
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

    if current_baseline.target != latest_baseline.target:
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
    if report.fingerprint_changed:
        raise typer.Exit(1)


def _print_target_identity_warning(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> None:
    typer.echo("WARNING")
    typer.echo()
    typer.echo("The selected baseline snapshots belong to different database / target identities.")
    typer.echo()
    typer.echo("Current Baseline")
    typer.echo(json.dumps(current_baseline.target.model_dump(), indent=2, sort_keys=True))
    typer.echo()
    typer.echo("Latest Baseline")
    typer.echo(json.dumps(latest_baseline.target.model_dump(), indent=2, sort_keys=True))
    typer.echo()
    typer.echo(
        "Comparing baseline snapshots from different databases or targets may produce "
        "misleading drift results."
    )


def _print_profile_summary(
    baseline: BaselineProfile,
    baseline_path: Path,
) -> None:
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

    typer.echo(f"Baseline written to:\n{baseline_path}")
