# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The GitHub Action now emits bounded inline workflow annotations for failed, errored, and
  warning-severity checks, attaches repository source locations when available, and reports any
  annotations dropped at the per-run cap while retaining the existing Step Summary.
- Added one CI/CD guide with copy-paste workflows for pull-request gating, scheduled audits with
  alerting, staging merges, and server-enforced read-only production runs.
- Added a scripted hostile-graph certification set that runs the real `debug`, `profile`, and
  `run` command boundary against noisy LLM Graph Builder-shaped data, a checksum-pinned Stanford
  SNAP scale graph, a three-member Neo4j 4.4 cluster, an APOC-less server, and an empty graph. Its
  manifest drives the runner and integration expectations; the legacy lane uses Docker-assigned
  ports and verifies three unique cluster members, one leader, and two followers before testing.

### Changed

- Reduced pull-request CI from 26 expanded jobs to 12: driver compatibility now tests the two
  supported edge combinations, pull requests scan only their commit range for secrets, and the
  Neo4j 4.4 hostile lane, cross-platform first-run trials, and Windows performance gates moved to
  a weekly and manually runnable Extended CI workflow.
- Added Python 3.14 to the CI test matrix and package classifiers.
- Reduced GitHub Action startup overhead with cached `uv` and Python setup, binary-only package
  installation, consolidated configuration/profile preparation, and direct helper execution from
  the Action environment.
- Regenerated the canonical fraud-ring sample reports and linked them from `README.md` and
  `docs/guides/user-guide.md`.
- Reorganized repository-facing material into dedicated `examples/`, `docs/`, `tools/`, and
  layered `tests/` directories, including a minimal starter and the preserved fraud-ring demo, so
  the repository root stays focused on project essentials.
- First-run CI now records three independent clean-runner install-to-first-valid-result samples on
  Linux, macOS, and Windows/WSL, uploads structured timing evidence, publishes the per-platform
  medians in the Actions summary, records both the tested GitHub SHA and pull-request head SHA, and
  enforces the under-15-minute adoption budget.
- The default check concurrency is now two workers across project scaffolding, the engine, and the
  Neo4j connection pool; `graphcheck.yml` and `graphcheck run --concurrency N` still override it.
- Migrated the breaking results contract from schema 1.2 to 2.0: `run.status` is now
  `run.run_status`, summary `status` is now `coverage_status`, and `load_results()` retains a
  compatibility path for historical artifacts.
- The Step Summary of `graphora/graphcheck-action` reports the run status and the coverage status
  separately from `v1.0.2`, which reads the schema 2.0 fields and falls back to the historical
  `run.status` and summary `status` for artifacts produced before this migration.
- The measured first-run path now installs a built wheel without a package cache and verifies the
  baseline-free `init` → `run` path directly and persists that evidence before `profile` runs as a
  separate post-timing smoke check.

### Fixed

- Restored pull-request smoke CI after the repository reorganization by staging the fraud-ring
  `graphcheck.yml` and `checks/` at the workspace root expected by the published GitHub Action.
- First-run stage failures, unexpected profiling faults, and baseline write errors now return an
  actionable `Fix:` diagnostic without exposing a Python traceback.

### Removed

- Removed the retired in-repository GraphCheck Action copy and its implementation tests; CI now
  guards the boundary and consumes only the standalone `graphora/graphcheck-action@v1` release.

## [0.2.0] - 2026-08-20

### Changed

- The base installation no longer pulls the LLM provider SDKs or MCP server stack. Install
  `graphcheck[generate]` for check authoring or `graphcheck[mcp]` for `graphcheck mcp serve`;
  commands now print the matching install command when an extra is absent, while JSON Schema
  remains an explicit runtime dependency for the documented schema validators.
- Restored the optional generation and MCP stacks to the development dependency group so a
  locked development install can run the complete test suite without changing the lean base
  package published to PyPI.
- Standardized the product description to "Semantic observability for property graphs." across
  the CLI help and packaging metadata.
- Pinned canonical HTML sample reports to LF line endings so their byte-level checks pass
  consistently on Windows and Unix checkouts.

### Fixed

- `graphcheck init` no longer scaffolds a drift check that errors on the first run. A fresh
  project's default checks are baseline-free, so the first `graphcheck run` returns a clean,
  understandable result instead of a missing-baseline execution error.

## [0.1.0] - 2026-08-20

### Added
- Clean-machine test protocol: step-by-step script, Part D interview questions, and a friction-log template for onboarding external testers.
- In-repo reference docs: CI setup, check reference (all core/PII checks with fraud-ring fixture examples), install/quickstart pointer, and a troubleshooting FAQ covering profile/connection/read-only errors.
- PyPI release tooling: a Trusted Publishing (OIDC) release workflow triggered by a published
  GitHub Release, guarding that the release tag matches the built version and smoke-testing a
  clean install before upload; packaging metadata (project URLs, classifiers, keywords, author);
  and a single-source version literal in `src/graphcheck/__init__.py`, read at build time by hatch. See `docs/maintainers/releasing.md`.
- A release-oriented top-level README with a versioned project header, embedded CLI demo,
  concise problem statement, quickstart, check-suite example, explicit non-goals, and a preserved
  full user guide for detailed operational workflows.
- An in-repo agent guide covering the three-tool MCP surface, validated `results.json`
  consumption (verdicts, evidence, and the `0/1/2/3` exit-code contract), programmatic SPEC-02
  check authoring, and the human-approval gate for inert generated checks.
- Contributor docs: dev setup, tests/lint, and PR flow sections in CONTRIBUTING.md, plus bug/feature/docs issue templates and three good-first-issue labels.
- A shared, immutable result-presentation layer now gives the CLI and HTML report the same
  deterministic language for clean, findings, incomplete, empty, all-skipped, and failed runs.
- Complete HTML check ledgers now show every selected check's stable identity and verdict, with
  generic persisted-reason explanations for skipped checks and an `Issues` filter for failures,
  warnings, and execution errors.
- Offline reports now include a compact target summary with graph size, schema inventory, and
  capability availability; a permanent `Not Evaluated` coverage section; and an accessible,
  tabbed `Checks Explorer` / `Next Steps` panel with fixed non-personalized guidance.
- CLI runs now include a borderless per-suite score and coverage table, including single-suite
  runs, while runs with skipped checks include a concise table naming each unevaluated check and
  its persisted reason.
- Drift checks can complete using a present, valid measurement from an otherwise partial baseline;
  missing measurements from partial baselines remain explicit partial-run errors.
- A clean all-pass results fixture and CR1–5 implementation roadmap document the new outcome,
  coverage, target, ledger, navigation, and generic-guidance acceptance contracts.
- Verified mask-redacted exports through `graphcheck run --redact` (alias `--redacted`) and
  `graphcheck redact`, with
  fail-closed literal-surface verification and preserved result contract structure.
- Closed redacted-export leaks through diagnostics, partial reasons, authored check metadata, and
  target-derived run IDs.
- Added relationship-preserving aliases for free-form identifiers and a source-aware final-artifact
  literal scan with an explicit structural allowlist.
- A purely local Docker Compose quickstart with pinned Neo4j 5.26.28 and automatic loading of the
  canonical reproducible fraud-ring fixture.
- Baseline-free fraud-ring conformance checks for the local demo, alongside the existing
  connection smoke check.
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
- Interactive `graphcheck run` progress with exact per-check completion and clean redirected output.
- SPEC-04 Engine, consolidating the C1 and run-command contracts into one detailed source for
  compilation, execution, evaluation, evidence, sampling, baselines, artifacts, and CI behavior.
- Core and PII pack metadata/schema slice: eleven additional core conformance `with` schemas, strictly typed `core.yml` and `pii.yml` metadata, a generated metadata JSON Schema, SPEC-09, and validation-parity tests.
- Complete built-in C3 runtime: manifest-driven compiler/capability binding for every registered
  check plus executable, deterministically sampled PII name/value scans with Luhn/Verhoeff
  validation, redacted findings, mandatory node evidence, and confidence intervals.
- A pure deterministic severity-weighted scorer with exact half-even rounding, execution
  coverage, and independently calculated per-suite scores for the machine-readable contract.
- `graphcheck generate` with disclosed, allow-listed baseline/document transmission; pinned
  Instructor adapters for Anthropic, Google Gemini/Gemma, OpenAI, and explicit-endpoint Ollama; one
  correction request;
  candidate and final SPEC-02 validation; inert generated markers; atomic exclusive YAML
  publication; stable human/JSON output; and network-free fake-provider integration coverage.
- Google tool schemas use flat, required function arguments and locally normalize them into
  conformance, competency, and drift objects, avoiding Gemma's unreliable nested object output.
- Google generation retries HTTP 500/502/503 once with bounded backoff before reporting provider
  unavailability.
- Google generation no longer retries timeouts or performs a correction call after a non-empty
  valid first batch; its compact prompt and 2,048-token output cap bound free-tier latency.
- Google conformance and drift candidates must reference labels/properties present in the baseline,
  preventing malformed tool arguments from being published as apparently valid checks.
- Google `gemini-*` models use native JSON structured output with the full typed GraphCheck
  proposal union and standard correction workflow, while the working Gemma tool path remains
  isolated and unchanged.
- SPEC-10 telemetry event infrastructure: six strict, immutable, content-free engine events;
  run/probe/query/check/terminal/fault instrumentation; reconciled aggregate mapping to six
  allowlisted PostHog events; and correlated outermost CLI command telemetry, including safe
  boundaries for the forthcoming `profile`, `diff`, and `baseline` commands.
- User-controlled anonymous telemetry that is disabled by default, with
  `graphcheck telemetry enable`, `disable`, `status`, `preview`, and `reset-id`; persisted,
  versioned consent; `DO_NOT_TRACK` and process overrides; non-persistent process identities; and
  no client construction or network activity before opt-in.
- A dependency-free, asynchronous PostHog Cloud adapter with a bounded queue and final flush,
  short request timeouts, a release-owned project-key seam, person-profile and geo-enrichment
  suppression, exact per-event property schemas, and complete failure isolation for offline,
  timed-out, refused, or otherwise unsuccessful delivery.
- Telemetry privacy and transparency safeguards: no queries, schema tokens, graph values,
  credentials, project/check identity, results, verdicts, paths, arguments, or free-form
  diagnostics; a complete public event/field inventory in `docs/reference/telemetry.md`; CI enforcement
  against allowlist drift; property-based anonymization coverage; explicit network-failure tests;
  and a fresh-install command matrix proving zero events before opt-in.
- A fifteen-part performance roadmap with repeatable cold-CLI, query-plan, allocation-retention,
  client/server timing, and opt-in 10-million-node benchmark records. Records include the commit,
  runtime, driver, server, Cypher version, concurrency, raw samples, and representative plans.
- Required performance regression gates for the named Windows/Python 3.12 reference environment,
  including confirmation batches for noisy CLI results, platform-independent logical memory
  bounds, machine-readable diagnostics, and retained CI artifacts.
- Shared native Cypher identifier validation and escaping for labels, relationship types, and
  property keys, including Unicode, punctuation, reserved-word, embedded-backtick, and
  injection-shaped identifier coverage.
- A bounded Neo4j result API with explicit consumption policies, truncation/completeness metadata,
  observed-row lower bounds, retained-row limits, server timing metadata, and optional early-stop
  predicates while preserving the existing eager connector API.
- Positive project and CLI concurrency controls through `graphcheck.yml` and
  `graphcheck run --concurrency`, with deterministic output ordering, deadline-aware scheduling,
  an initial conservative default of one worker, and Neo4j pool sizing matched to effective
  concurrency.
- A secure local report explorer launched by `graphcheck report --open`, with searchable history,
  in-place report switching, two-run comparisons, multi-select deletion, safe `latest` repair,
  authenticated same-origin APIs, restrictive browser security headers, and idle shutdown.
- Compact `summary.json` sidecars for report-history discovery. Full `results.json` artifacts are
  loaded lazily only when a report is selected, compared, rendered, or otherwise inspected.
- A documented Neo4j compatibility matrix covering Python driver 5.20 through 6.x, Neo4j Server
  5.26 LTS and tested calendar releases, and Cypher 5/25, with Neo4j Server 4.4 now explicitly
  rejected as unsupported.
- CI lanes for the minimum and latest supported Python-driver lines, Neo4j 5.26/Cypher 5 and
  2026.06/Cypher 5/25 integration targets, performance gates, and an isolated installed-wheel
  smoke test that verifies CLI startup and packaged check resources.
- Clean-environment first-run CI lanes for an Ubuntu container, native macOS, and Ubuntu under
  Windows WSL. Each lane installs GraphCheck in isolation, runs the real `init` → `profile` →
  `run` flow against pinned Neo4j 5.26.28, asserts a complete exit-0 result and required
  artifacts in under ten minutes, and retains the command logs, report, result, and timing evidence.
- Added GraphCheck observability support.
- Added `graphcheck monitor` CLI command.
- Added Prometheus metrics exporter.
- Added reference Prometheus/Grafana monitoring stack.
- Added Grafana dashboard for database health.
- Guided `profiles.yml` scaffolding with an editable local password, environment-variable guidance,
  supported Bolt/TLS URI examples, and edition-specific read-only setup notes so a fresh local
  install has an actionable path from `graphcheck init` to `graphcheck debug`.
- Stable connection-profile diagnostics for invalid URIs, TLS/certificate mismatches, unavailable
  databases, credentials whose Enterprise read-only status cannot be verified, and credentials
  missing the required built-in `reader` role or carrying additional roles. The new safe error
  codes are also covered by telemetry policy and report rendering.
- Live Neo4j integration coverage for Community Edition `debug` and `run`, Enterprise administrator
  rejection, built-in `reader` acceptance, and missing/additional-role rejection across the
  supported Neo4j/Cypher matrix.

### Changed

- Neo4j integration containers are reused for each CI session and the database-backed integration
  files run as one focused group, with explicit fixture cleanup preventing shared graph state from
  leaking between tests.
- Bumped the independently versioned `results.json` contract to schema 1.2. New non-failed runs
  persist sorted, unique label and relationship-type inventories collected by the existing target
  probe; schema 1.0 and 1.1 artifacts retain explicit in-memory `not recorded` compatibility.
- The stable `graphcheck debug --json` target now intentionally exposes the same schema 1.2 graph
  counts and sorted label/relationship-type inventory returned by the connector probe.
- `graphcheck run` now prints target metadata before interactive progress and ends with an explicit
  result sentence, one artifact-directory path, and a semantically colored exit-code line without
  repeating individual passing checks.
- The offline report now separates run lifecycle, findings, execution errors, and evaluated
  coverage instead of using broad `All clear` / `No issues found` language or a duplicate Issue
  Summary table.
- Expanded the README's Neo4j setup guidance with edition-specific credential requirements,
  Enterprise built-in `reader` role provisioning, and Community Edition's planner-guarded
  security model.
- `graphcheck report --open [ID]` now opens the latest report when no ID is supplied and replaces
  the separate `--run ID` selector when opening a historical report.
- Offline reports now present each suite's independently calculated score alongside execution
  coverage and verdict badges. The overall score remains in `results.json`; point-deduction and
  earned/possible-weight arithmetic are intentionally not shown in the HTML report.
- Every run is preserved below `runs/<run-id>/`, while `runs/latest` is refreshed from a fully
  staged result/report pair so history commands work without exposing mixed-version artifacts.
- Report history and comparisons now show named per-suite scores, matching the run summary and HTML
  report; the overall machine score is no longer substituted in those later views.
- Reports with zero evaluated checks now state that explicitly in both the run summary and issue
  table instead of labeling the empty result as all clear.
- Bumped the independently versioned `results.json` contract to schema 1.1 to add honest aggregate
  measurement-scope evidence for count drift when removed graph elements cannot be selected.
- Raised the Python floor to **3.12** (`requires-python = ">=3.12"`, ruff `target-version = "py312"`, CI matrix `3.12`–`3.13`). Dropping 3.10/3.11 is a deliberate decision (3.10 reaches end-of-life Oct 2026), and lets the contracts use modern-Python idioms (`StrEnum`).
- Built-in checks now compile graph schema names as validated, escaped native Cypher tokens instead
  of runtime token predicates. Values, regular expressions, thresholds, IDs, and sampling controls
  remain parameters, and optional relationship scopes use separate typed and generic query forms.
- Check execution now separates exact measurement queries from bounded evidence queries. Evidence
  runs only for failing measurements, both phases share a planner-verified read transaction and
  deadline, and malformed or failed evidence collection remains an errored check rather than a
  pass.
- Competency evaluation now derives one combined row-consumption policy for row bounds, emptiness,
  columns, uniqueness, `contains`, and duplicate-preserving bag equality. Decisive assertions stop
  early, full-result assertions enforce a safety ceiling, and hashing/projection work is performed
  only when requested.
- Hub and PII sampling now use deterministic oversampling gates before expensive ordering. Hub
  population is calculated in the query snapshot without a preflight round trip, PII selection is
  keyed per property occurrence, and exact versus estimated result metadata is preserved.
- Profiling now consolidates label/relationship counts, property inventories, coverage, sample
  types, and server-side degree percentiles into fewer bounded queries, reuses collected inventory
  across stages, and preserves partial-profile/deadline behavior.
- Normal artifact writing now validates through the canonical Pydantic model once instead of
  repeating runtime JSON Schema validation. `jsonschema` remains available for contract tests and
  explicit validation helpers but moved from production dependencies to the development group.
- The console entry point now uses a standard-library-only bootstrap for the exact `--version`
  path. Command modules, Neo4j, reporting, generation providers, Pydantic telemetry models, and
  PostHog delivery are imported lazily only when the selected command or enabled consent needs
  them.
- Telemetry consent persistence and shared enums are now separated from Pydantic payload models,
  with a no-op inactive runtime for disabled telemetry. Command events also include a coarse
  major/minor OS version without exposing kernel, build, distribution, or architecture details.
- Recursive suite discovery now reads and validates YAML files directly on each command before
  suite-ID filtering. The `.graphcheck-suite-manifest.json` cache, invalidation logic, and writes
  were removed; existing stale manifest files are ignored and left untouched.
- Read classification now uses a bounded 256-entry per-client LRU keyed by database and exact query
  text instead of process-global state. Concurrent identical preflights are single-flight,
  successful classifications alone are cached, cache/in-flight state is cleared on close, and
  query-free hit/miss plus timing data is exposed to internal telemetry.
- Target probing now caches the first successful complete probe per client with concurrent
  single-flight behavior, consolidates node and relationship totals into one count-store request,
  and reports query-free aggregate duration, round-trip count, and cache outcome in debug data.
- Graph entity identity and deterministic ordering now use opaque `elementId()` values throughout
  evidence, uniqueness, drift, and profile sampling. Numeric `id()` remains only in explicitly
  marked Cypher 5 sampling paths whose seeded distribution contract has not yet been versioned.
- Human and JSON debug output now distinguish GraphCheck, Neo4j Python driver, Neo4j Server, and
  database Cypher versions instead of presenting them as one ambiguous Neo4j version.
- Report history now discovers only safe direct child artifact directories, validates summary and
  result consistency, avoids following links/junctions, and transactionally repairs the `latest`
  alias after selected reports are deleted.
- Offline reports now provide searchable/filterable check cards, sortable issue summaries,
  light/dark themes, clearer complete/warning/error/partial banners, collapsible remediation text,
  responsive report-history controls, and in-place explorer navigation without full page reloads.
- Neo4j client defaults now use explicit connection/acquisition timeouts, fetch size, retry policy,
  and concurrency-aware pool limits. The production dependency is bounded to `neo4j>=5.20,<7`,
  and the development toolchain pins Ruff 0.16.0 for reproducible local and CI formatting.
- Recorded reference measurements reduced complete-result serialization median time from
  77.745 ms to 1.701 ms and cold `graphcheck --version` median time from 311.50 ms to 72.49 ms,
  while preserving artifact bytes and command behavior.
- Profile selection now validates the URI before constructing a driver, treats blank passwords as
  missing, lets a populated `password_env` override the inline password, and falls back to that
  inline password when the named environment variable is absent.
- `graphcheck init` and `graphcheck debug` now convert unexpected probe failures into stable,
  actionable diagnostics. Init reports whether Neo4j was detected and directs unsuccessful setup
  to `graphcheck debug`; debug preserves the same error shape in human and JSON output.
- Init, debug, and run now share a credential preflight. Community Edition follows an explicit
  planner-guarded policy because it has no RBAC, while Enterprise requires exactly Neo4j's
  built-in `reader` role plus `PUBLIC` and fails closed when current-user role evidence is
  unavailable or malformed.
- Neo4j error mapping now considers driver error codes, nested causes, and the selected profile to
  distinguish authentication, reachability, TLS, permission, query, and database failures and to
  provide profile-specific remediation.
- Failed run artifacts and offline HTML reports now retain the root connection or credential
  diagnostic and show its remediation in an `Action required` callout.

### Fixed

- The release README and full user guide now require Enterprise credentials to have exactly
  Neo4j's built-in `reader` role plus automatic `PUBLIC`, replacing obsolete custom-role setup
  instructions that the current credential gate rejects.
- Failed artifacts now remain failed in report headers and history for every run-level error code,
  rather than only for unreachable-Neo4j failures.
- Clean-result presentation no longer overstates all-skipped, empty-selection, partial, or errored
  runs; incomplete coverage and execution errors are reported explicitly on both output surfaces.
- Empty Neo4j databases now evaluate conformance checks vacuously with real zero-population
  measurements while drift and populated graphs with unfamiliar schema still report
  `engine.schema_reference_missing`; in-flight run-budget timeouts now produce partial exit 2, and
  terminal/HTML diagnostics reserve “checks were evaluated” for measured runs.
- Neo4j driver deprecation notifications no longer flood GraphCheck CLI output; GraphCheck still
  consumes the notification metadata needed to reject missing schema references.
- Report history and rendering now read schema 1.0 and 1.1 artifacts by upgrading them to schema
  1.2 in memory without rewriting the source; newly written `results.json` files use the current
  1.2 contract with non-null target inventory arrays.
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
- Early-stopped bounded reads now avoid the Neo4j driver's automatic result drain while still
  closing sessions safely; complete reads continue to consume summaries and expose server timing
  and notification metadata.
- Measurement/evidence execution passes plain Cypher strings at the explicit transaction boundary,
  preserving driver compatibility while retaining the shared snapshot and timeout budget.
- Native-token compilation preserves missing-schema diagnostics, recognizes provider-suffixed
  Neo4j plan operators, and keeps unknown label, relationship, and property notifications as
  structured errors instead of empty passes.
- Non-fail-fast concurrent execution preserves declared check order, isolates query/compiler
  failures, records generated/unsupported/deadline skips exactly once, and does not start work after
  the run budget is exhausted. Fail-fast execution remains sequential and deterministic.
- Engine telemetry emission now serializes sequence allocation and sink delivery so concurrent
  checks retain unique, monotonically ordered event envelopes without exposing workload content.
- Report artifacts are rendered once from an already validated model and published as an atomic
  `results.json`, `report.html`, and `summary.json` set, preventing mixed-version latest/history
  views and duplicate render work.
- Report explorer and offline-report fixes cover empty histories, deleted current/latest reports,
  stale selections, comparison refreshes, singular/plural issue labels, zero-check states,
  filtered totals, run-level remediation toggles, dark-theme contrast, and safe error responses.
- Test configuration now isolates telemetry consent, installation identity, process overrides, and
  delivery keys per test, so persisted user configuration cannot construct a real telemetry client
  or change CLI/profile test behavior.
- Restored the supported Community Edition path by skipping the unavailable Enterprise role gate
  after edition probing while retaining the per-query `EXPLAIN` read guard. This also fixes
  the Community-backed GraphCheck CI smoke job failing setup with exit code 3.
- Run preflight now rejects unsafe or unverifiable Enterprise credentials before any checks execute
  and preserves the resulting error and fix in `results.json`, console output, and the HTML report.
- Corrected Neo4j 5/Cypher 5 and current Neo4j/Cypher 25 diagnostics so missing or unavailable APOC
  procedures remain optional-capability failures, while actual database-not-found/unavailable
  driver codes continue to map to `neo4j.database_not_found`.
