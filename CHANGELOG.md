# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Contributor docs: dev setup, tests/lint, and PR flow sections in CONTRIBUTING.md, plus bug/feature/docs issue templates and three good-first-issue labels.
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
  diagnostics; a complete public event/field inventory in `docs/telemetry.md`; CI enforcement
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
  a conservative default of one worker, and Neo4j pool sizing matched to effective concurrency.
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
  with privileges outside GraphCheck's explicit read-only model. The new safe error codes are also
  covered by telemetry policy and report rendering.
- Live Neo4j integration coverage for Community Edition `debug` and `run`, Enterprise administrator
  rejection, and custom boosted-procedure/schema-administration rejection across the supported
  Neo4j/Cypher matrix.

### Changed

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
  planner-guarded policy because it has no RBAC, while Enterprise permits only database access,
  graph reads, the default non-mutating `LOAD ON ALL DATA` grant, and non-boosted
  procedure/function execution and fails closed when privilege evidence is unavailable.
- Neo4j error mapping now considers driver error codes, nested causes, and the selected profile to
  distinguish authentication, reachability, TLS, permission, query, and database failures and to
  provide profile-specific remediation.
- Failed run artifacts and offline HTML reports now retain the root connection or credential
  diagnostic and show its remediation in an `Action required` callout.

### Fixed

- Neo4j driver deprecation notifications no longer flood GraphCheck CLI output; GraphCheck still
  consumes the notification metadata needed to reject missing schema references.
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
- Restored the supported Community Edition path by skipping the unavailable Enterprise privilege
  query after edition probing while retaining the per-query `EXPLAIN` read guard. This also fixes
  the Community-backed GraphCheck CI smoke job failing setup with exit code 3.
- Closed the custom-role privilege bypass by inspecting privilege action, resource, and segment and
  rejecting boosted execution plus graph-write, schema, database, transaction-management, and
  DBMS-administrative grants instead of relying on built-in role names alone.
- Accepted Neo4j's default `PUBLIC`-role `LOAD ON ALL DATA` privilege as non-mutating while still
  rejecting scoped `LOAD ON CIDR` grants, preventing valid restricted Enterprise credentials from
  failing every supported Neo4j/Cypher integration lane.
- Run preflight now rejects unsafe or unverifiable Enterprise credentials before any checks execute
  and preserves the resulting error and fix in `results.json`, console output, and the HTML report.
- Corrected Neo4j 5/Cypher 5 and current Neo4j/Cypher 25 diagnostics so missing or unavailable APOC
  procedures remain optional-capability failures, while actual database-not-found/unavailable
  driver codes continue to map to `neo4j.database_not_found`.
