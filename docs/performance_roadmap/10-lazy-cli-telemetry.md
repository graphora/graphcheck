# PR 10 — Lazy-load CLI execution and telemetry modules

- Category: CLI responsiveness and dependency bloat
- Roadmap source: Step 5, startup phase
- Prerequisites: PRs 01 and 02
- Suggested PR title: `perf: add a lightweight CLI bootstrap and lazy telemetry initialization`

## Goal

Reduce cold CLI latency by answering cheap commands before importing Neo4j, reporting, profiler, or
the full telemetry model/delivery stack.

## Scope

- A minimal `--version` fast path.
- Command-local imports for heavy execution modules.
- Standard-library-only telemetry consent bootstrap.
- Disabled telemetry path that does not import event/delivery models.
- Fresh-process import-boundary and timing tests.

## Non-goals

- Replacing Typer or Rich.
- Splitting CLI/runner responsibilities.
- Changing telemetry consent semantics or schemas.
- Removing telemetry.

## Files expected to change

- package entry point and/or a small CLI bootstrap module
- `src/graphcheck/cli.py`
- telemetry package import boundaries
- CLI and telemetry tests
- startup benchmark tests
- packaging configuration if the entry point changes

## Import boundary

For `graphcheck --version`, do not import:

- Typer;
- Pydantic;
- Neo4j driver;
- reporting;
- profiler;
- telemetry events/delivery.

For normal telemetry-disabled commands:

1. read effective consent with the standard library;
2. return an inactive runtime without importing event models or PostHog;
3. import full telemetry only after enabled consent is established.

Telemetry management commands may import policy functions explicitly when invoked.

## Implementation

1. Add fresh-subprocess tests that record selected `sys.modules` entries.
2. Add a minimal entry point that recognizes only the exact version fast-path arguments.
3. Preserve Typer's behavior for all other arguments, including error handling.
4. Move heavy command implementation imports inside the commands/wrappers that need them.
5. Split consent-file parsing from Pydantic telemetry payload models.
6. Import delivery and event models only for active telemetry or telemetry-specific commands.
7. Compare command help/output/exit codes against current snapshots.
8. Measure median and p95 against PR 02's baseline.

## Tests

Run:

```console
uv run pytest tests/test_cli.py tests/telemetry -q
```

Required subprocess cases:

- `--version`;
- `--help`;
- each command's `--help`;
- telemetry disabled;
- telemetry process-enabled;
- telemetry persisted-enabled;
- invalid command/option;
- installed wheel entry point.

## Acceptance criteria

- `--version` does not import Typer, Pydantic, Neo4j, or telemetry.
- Disabled commands do not initialize telemetry payload/delivery modules.
- Help, output, and exit codes remain compatible.
- Startup median and p95 improve against a repeatable baseline.
- Enabled telemetry remains schema-valid and behaviorally unchanged.

## Rollback

The bootstrap can delegate every invocation back to the existing Typer entry point if a compatibility
issue is found. Lazy telemetry imports can be reverted independently.

## PR checklist

- [ ] Import-boundary assertions run in fresh processes.
- [ ] Installed-wheel entry point was tested.
- [ ] No telemetry policy behavior changed.
- [ ] Before/after cold-start data is attached.
