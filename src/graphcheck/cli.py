import json
from dataclasses import replace
from pathlib import Path

import typer

from graphcheck import __version__
from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
from graphcheck.contracts.results import CheckError, Results
from graphcheck.debug_diagnostics import CapabilityContext, blocked_checks_for_project
from graphcheck.engine import DirectoryBaselineProvider, Engine, SuiteInput, failed_results
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient, debug_trace, error_json, init_trace
from graphcheck.project import (
    ARTIFACTS_DIR,
    PROJECT_FILE,
    ensure_gitignore_entries,
    find_project_root,
    load_project_config,
    write_default_project,
    write_example_suite,
)
from graphcheck.reporting.html import write_html_report
from graphcheck.reporting.writer import write_results

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
    run_dir: Path | None = None
    tags: list[str] = []
    client: Neo4jClient | None = None
    try:
        root = find_project_root()
        run_dir = root / ARTIFACTS_DIR / "runs" / "latest"
        config = load_project_config(root)
        artifacts = _project_path(root, config.artifacts)
        run_dir = artifacts / "runs" / "latest"
        tags = _selection_tags(select or [])
        suite_inputs = _load_suite_inputs(
            _project_path(root, config.checks),
            requested_suites,
        )
        profiles = load_profiles(root)
        _, selected_profile = select_profile(profiles, profile)
        client = Neo4jClient(selected_profile)
        results = Engine(
            client,
            baselines=DirectoryBaselineProvider(artifacts / "baselines"),
        ).run(
            suite_inputs,
            tags=tags,
            fail_fast=fail_fast,
            selection_suites=requested_suites or None,
        )
    except GraphCheckError as exc:
        if run_dir is None:
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
        if run_dir is None:
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
        assert run_dir is not None
        results_path, report_path = _write_run_artifacts(results, run_dir)
    except Exception as exc:
        typer.echo(f"run.artifact_failed: Could not write run artifacts: {exc}", err=True)
        typer.echo("Fix: Check the configured artifacts path and filesystem permissions.", err=True)
        raise typer.Exit(3) from exc

    _print_run_summary(results, results_path, report_path)
    raise typer.Exit(results.run.exit_code)


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


def _write_run_artifacts(results: Results, run_dir: Path) -> tuple[Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = write_results(results, run_dir / "results.json")
    report_path = write_html_report(results, run_dir / "report.html")
    return results_path, report_path


def _print_setup_error(error: CheckError) -> None:
    typer.echo(f"{error.code}: {error.message}", err=True)
    typer.echo(f"Fix: {error.fix}", err=True)


def _print_run_summary(results: Results, results_path: Path, report_path: Path) -> None:
    totals = results.totals
    score = "n/a" if results.score is None else str(results.score.value)
    typer.echo(f"GraphCheck run {results.run.id}: {results.run.status.value}")
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
