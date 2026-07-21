# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: packaging, minimal CLI, CI, governance.
- SPEC-01 `results.json` and SPEC-02 check YAML contracts: Pydantic models (source of truth), generated JSON Schemas, machine-valid fixtures, and validation tests.
- SPEC-03 for the neo4j connector.
- Neo4j connector foundation: project discovery, `graphcheck init`, strict connection profile loading, env-var password override, structured adapter errors, read-only Neo4j driver wrapper, capability probe, and `graphcheck debug` with stable JSON output.
- Connector tests covering profile validation, debug JSON shape, count-store plan detection, and opt-in Neo4j 4.4 / 5.x testcontainers integration.
- `graphcheck report` can open or list historical reports, select a run by id, compare
  two result artifacts, prune old runs with a retention count, and generate a focused
  failures/warnings/errors report.
- C1 core engine: strict suite semantics, parameterized Cypher compilation, read-only execution,
  isolated check errors, conformance/competency/drift verdict evaluation, pointer evidence,
  deterministic sampling, baseline resolution, and SPEC-01 run metadata.
- Compiler callbacks for all twelve C3 core-pack checks (with unobservable `dangling_rels` failing
  closed) and a C4-compatible baseline-provider boundary, ready for the downstream branches to
  merge without changing the frozen envelopes.
- Hypothesis evaluator invariants, an opt-in 10M-node/30-check performance budget test, and
  Neo4j integration coverage for broken queries, missing labels, isolation, and write rejection.
- `graphcheck run` with suite/tag selection, explicit fail-fast partial results, C4 baseline lookup,
  C5-compatible `results.json` and self-contained offline HTML artifacts, console summaries, and
  the frozen CI exit-code contract.
- SPEC-04 Engine, consolidating the C1 and run-command contracts into one detailed source for
  compilation, execution, evaluation, evidence, sampling, baselines, artifacts, and CI behavior.
- Core and PII pack metadata/schema slice: eleven additional core conformance `with` schemas, strictly typed `core.yml` and `pii.yml` metadata, a generated metadata JSON Schema, SPEC-09, and validation-parity tests.
- Complete built-in C3 runtime: manifest-driven compiler/capability binding for every registered
  check plus executable, deterministically sampled PII name/value scans with Luhn/Verhoeff
  validation, redacted findings, mandatory node evidence, and confidence intervals.

### Changed

- `graphcheck report --open [ID]` now opens the latest report when no ID is supplied and replaces
  the separate `--run ID` selector when opening a historical report.
- Bumped the independently versioned `results.json` contract to schema 1.1 to add honest aggregate
  measurement-scope evidence for count drift when removed graph elements cannot be selected.
- Raised the Python floor to **3.12** (`requires-python = ">=3.12"`, ruff `target-version = "py312"`, CI matrix `3.12`–`3.13`). Dropping 3.10/3.11 is a deliberate decision (3.10 reaches end-of-life Oct 2026), and lets the contracts use modern-Python idioms (`StrEnum`).

### Fixed

- Report history and rendering now read schema 1.0 artifacts by upgrading them to schema 1.1 in
  memory; newly written `results.json` files continue to use the current 1.1 contract.
- Corrected the C1/C5 merge resolution so human-readable visibility and blocker diagnostics remain
  in `graphcheck debug`, report opening no longer references an undefined debug trace, HTML reports
  retain deterministic check ordering and aggregate-scope labels, and results/HTML artifacts share
  temporal- and binary-safe JSON normalization.
- Contract validators now reject shapes the frozen specs disallow: severity/verdict mismatches (which could downgrade the CI exit code), the `passed`/`with_` field-name aliases, and check-result / run records that omit a frozen present-but-nullable key.
- Count-store detection now inspects the Neo4j `EXPLAIN` summary plan instead of stringifying result rows.
- The connector's rich read path now preserves result columns, graph entities, and notifications;
  missing schema-reference warnings become structured errors, per-query timeouts reach the driver,
  and target fingerprints now hash graph schema tokens plus counts rather than connection details.
- Debug probing now reports checks blocked by missing Neo4j read access instead of aborting while loading graph counts, including scoped privileges and `HOME GRAPH` grants or denials.
- Debug capability blockers now resolve `requires` from validated check-pack `.yml`/`.yaml`
  metadata and name every suite/check blocked by missing APOC in human and JSON output.
- Count-drift tolerance breaches now produce fail/warn findings with deterministic aggregate-scope
  evidence, including decreases to zero, instead of `engine.evidence_missing` errors.
- All observable core conformance checks now load through the public SPEC-02 registry/compiler path;
  the deliberately unobservable `dangling_rels` check continues to fail closed.
- Offline HTML reports now use the same canonical JSON-compatible value normalization as
  `results.json`, including YAML dates, datetimes, binary values, and deterministic set ordering.
- Missing pack-declared capabilities now produce explicit unsupported partial skips during engine
  runs, and missing property-key notifications are errored instead of becoming empty passes.
- Check-level sample sizes can only reduce the global sampling decision, PII sampling keys operate
  on individual node/property occurrences, and competency bag equality canonicalizes equivalent
  Neo4j/Python temporal values before hashing.
- Read execution now fails closed unless Neo4j's planner classifies the statement as read-only;
  array-valued properties no longer crash type, format, or PII scans; live PII population drift is
  rejected; and `dangling_rels` is an explicit store-consistency capability blocker.
