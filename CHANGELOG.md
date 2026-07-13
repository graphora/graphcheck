# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: packaging, minimal CLI, CI, governance.
- SPEC-01 `results.json` and SPEC-02 check YAML contracts: Pydantic models (source of truth), generated JSON Schemas, machine-valid fixtures, and validation tests.
- SPEC-03 for the neo4j connector.
- Neo4j connector foundation: project discovery, `graphcheck init`, strict connection profile loading, env-var password override, structured adapter errors, read-only Neo4j driver wrapper, capability probe, and `graphcheck debug` with stable JSON output.
- Connector tests covering profile validation, debug JSON shape, count-store plan detection, and opt-in Neo4j 4.4 / 5.x testcontainers integration.
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

### Changed

- Raised the Python floor to **3.12** (`requires-python = ">=3.12"`, ruff `target-version = "py312"`, CI matrix `3.12`–`3.13`). Dropping 3.10/3.11 is a deliberate decision (3.10 reaches end-of-life Oct 2026), and lets the contracts use modern-Python idioms (`StrEnum`).

### Fixed

- Contract validators now reject shapes the frozen specs disallow: severity/verdict mismatches (which could downgrade the CI exit code), the `passed`/`with_` field-name aliases, and check-result / run records that omit a frozen present-but-nullable key.
- Count-store detection now inspects the Neo4j `EXPLAIN` summary plan instead of stringifying result rows.
- The connector's rich read path now preserves result columns, graph entities, and notifications;
  missing schema-reference warnings become structured errors, per-query timeouts reach the driver,
  and target fingerprints now hash graph schema tokens plus counts rather than connection details.

### Changed

- Project runtime and CI target moved to Python 3.12+, with the test matrix narrowed to Python 3.12 and 3.13.
