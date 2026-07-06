# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: packaging, minimal CLI, CI, governance.
- SPEC-01 `results.json` and SPEC-02 check YAML contracts: Pydantic models (source of truth), generated JSON Schemas, machine-valid fixtures, and validation tests.

### Fixed

- Contract validators now reject shapes the frozen specs disallow: severity/verdict mismatches (which could downgrade the CI exit code), the `passed`/`with_` field-name aliases, and check-result / run records that omit a frozen present-but-nullable key.
