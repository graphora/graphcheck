# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: packaging, minimal CLI, CI, governance.
- SPEC-01 `results.json` and SPEC-02 check YAML contracts: Pydantic models (source of truth), generated JSON Schemas, machine-valid fixtures, and validation tests.
- SPEC-03 for the neo4j connector.
- Neo4j connector foundation: project discovery, `graphcheck init`, strict connection profile loading, env-var password override, structured adapter errors, read-only Neo4j driver wrapper, capability probe, and `graphcheck debug` with stable JSON output.
- Connector tests covering profile validation, debug JSON shape, count-store plan detection, and opt-in Neo4j 4.4 / 5.x testcontainers integration.
- Core and PII pack metadata/schema slice: eleven additional core conformance `with` schemas, strictly typed `core.yml` and `pii.yml` metadata, a generated metadata JSON Schema, SPEC-09, and validation-parity tests.

### Changed

- Raised the Python floor to **3.12** (`requires-python = ">=3.12"`, ruff `target-version = "py312"`, CI matrix `3.12`–`3.13`). Dropping 3.10/3.11 is a deliberate decision (3.10 reaches end-of-life Oct 2026), and lets the contracts use modern-Python idioms (`StrEnum`).

### Fixed

- Contract validators now reject shapes the frozen specs disallow: severity/verdict mismatches (which could downgrade the CI exit code), the `passed`/`with_` field-name aliases, and check-result / run records that omit a frozen present-but-nullable key.
- Count-store detection now inspects the Neo4j `EXPLAIN` summary plan instead of stringifying result rows.
- Debug probing now reports checks blocked by missing Neo4j read access instead of aborting while loading graph counts, including scoped privileges and `HOME GRAPH` grants or denials.

### Changed

- Project runtime and CI target moved to Python 3.12+, with the test matrix narrowed to Python 3.12 and 3.13.
