# GraphCheck supported graph size: raw benchmark report

Date: 2026-09-14. Default concurrency: **two workers**. Core + PII: **14 selected checks**.

## Decision

GraphCheck retains **10,000,000 nodes** as the published upper support boundary.
This policy limit is distinct from the outcomes recorded below.

<!-- BEGIN GENERATED: summary -->
At **10,000,000 nodes**, **11 of 11 attempted core checks passed**. Skipped checks and PII outcomes are reported separately below.

The largest recorded size where all executable checks completed is **1,000,000 nodes**. Findings (`fail`/`warn`) count as completed evaluations; execution errors do not.

- [10000000-run1](10000000-run1.json): `pii_name_match` errored with `neo4j.query_failed`.
- [10000000-run1](10000000-run1.json): `pii_value_match` errored with `neo4j.query_failed`.

An errored run's wall time is time to return, not a successful audit runtime.
<!-- END GENERATED: summary -->

The CLI now rejects graphs above 10M before dispatching checks, with exit code 3 and
`engine.graph_size_exceeded`, followed by a `Fix:` line. Relationships are measured
workload context, not a second hard cap. Exactly 10M nodes is admitted.

## Why these sizes

| Nodes | Relationships | Purpose |
| ---: | ---: | --- |
| 100,000 | 95,000 | Smaller production audit; below the default hub sampling transition |
| 1,000,000 | 950,000 | Mid-scale customer/account/transaction revenue or compliance workload |
| 10,000,000 | 9,500,000 | Large stress case matching the existing C1 benchmark target |

These are engineering workload choices, and the fixture follows the pinned fraud-ring scale generator: 30% Customer, 50% Account,
20% Transaction; OWNS, CONTROLS, SENT and RECEIVED_BY; indexed unique IDs; synthetic
PII; five high-degree customers. Hubs count inside the node total, correcting the
original generator's additional five nodes. Transactions also have `settled_at` one
hour after `ts`, so temporal_sanity checks real paired fields. There are 3.8 total
properties and 2.7 string properties per node across the fixture. See
[the fixture loader](../generate_graph_size.py) and [the full suite](../graph_size_suite.yml).

## Rig and methodology

- Windows 11, 8 logical CPUs, approximately 16 GiB physical RAM.
- Neo4j 2026.06.0 Enterprise; Python 3.12.10; Neo4j driver 6.2.0; JVM 21.0.11.
- Heap: 2 GiB; page cache: 512 MiB; shared transaction-memory maximum: 2 GiB.
- Reader credential: built-in `reader` plus `PUBLIC`; count-store capability was true.
- Every run selects all 12 core checks and both PII checks, in one suite, with default
  two-worker concurrency and a 295-second total engine budget. Sampling settings and
  check sample sizes are left at their defaults; seed is 0. No fail-fast selection.
- Engine wall time includes target probing, compilation, queries and evaluation.
  Loading, count validation, child-process startup and HTML rendering are excluded.
  Raw process wall time additionally includes worker startup/validation/serialization/shutdown.
  Per-check durations include active compilation/query/evaluation, not queue waiting.
  Concurrent per-check durations must not be added to infer elapsed run time.
- Python memory is OS process-lifetime peak RSS of a fresh worker. Neo4j memory is the
  whole Java DBMS process working set sampled every 100 ms during the run, including
  other loaded databases. Samples can miss short spikes; this is not transaction
  allocation accounting and does not measure heap alone. The initial 100k observation
  preceded server-RSS instrumentation, so that metric is unavailable rather than zero.
- No cache flush or DBMS restart was imposed. Both 100k observations are retained;
  the repeat exposes warm-up/cache variability. This small sample is not an SLA or p95.
- The measured query/evaluator implementation derives from repository commit
  `ab4ccfabec7bec7f805e44f19462357702ee3a8d`; new rig and size-guard work was present
  during the task. JSON records include the script/suite fingerprints, and the 10M
  record also fingerprints engine sources. The 10M stress command explicitly bypassed
  the size guard, while retaining two workers, sampling defaults and the time budget.
- A separate 330-second rig watchdog can terminate a stuck worker and preserve a
  diagnostic and event log. It was not needed here and is not a CLI feature.

[Environment metadata](environment.json), [transaction-memory settings](memory-limit-settings.json),
[reproduction instructions](../GRAPH_SIZE_BENCHMARK.md).

## Total runtime and peak memory

<!-- BEGIN GENERATED: totals -->
| Raw run | Nodes | Engine seconds | Process seconds | Python peak MiB | Neo4j observed peak MiB | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| [100000-run1](100000-run1.json) | 100,000 | 16.500 | 19.224 | 74.71 | Not collected | 13 completed; 0 errored; 1 skipped |
| [100000-run2](100000-run2.json) | 100,000 | 3.252 | 4.971 | 74.46 | 3159.41 | 13 completed; 0 errored; 1 skipped |
| [1000000-run1](1000000-run1.json) | 1,000,000 | 32.153 | 33.756 | 74.65 | 3157.69 | 13 completed; 0 errored; 1 skipped |
| [10000000-run1](10000000-run1.json) | 10,000,000 | 146.906 | 156.423 | 73.47 | 2477.83 | 11 completed; 2 errored; 1 skipped |
<!-- END GENERATED: totals -->

All full-pack runs have partial coverage because dangling_rels
is explicitly unsupported. **No check timed out and no watchdog terminated a run.**

## Wall-clock per check

Durations in seconds. `not executed` is not a zero-duration success.
The two 100k columns show the initial observation and repeat, respectively.

<!-- BEGIN GENERATED: checks -->
| Check | 100000-run1 | 100000-run2 | 1000000-run1 | 10000000-run1 | Last run outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| `completeness` | 0.579 | 0.062 | 0.484 | 5.328 | pass |
| `cardinality` | 1.016 | 0.094 | 1.109 | 8.953 | pass |
| `no_orphans` | 0.656 | 0.078 | 1.203 | 6.609 | pass |
| `dangling_rels` | not executed | not executed | not executed | not executed | skipped |
| `property_type` | 1.578 | 0.125 | 2.234 | 9.609 | pass |
| `property_format` | 1.140 | 0.110 | 1.828 | 8.844 | pass |
| `value_in_set` | 0.875 | 0.046 | 0.829 | 4.547 | pass |
| `uniqueness` | 1.219 | 0.047 | 0.828 | 4.406 | pass |
| `hub_outlier` | 4.157 | 0.141 | 1.250 | 1.407 | pass |
| `label_cooccurrence` | 1.156 | 0.031 | 0.719 | 4.671 | pass |
| `rel_direction` | 1.406 | 0.109 | 2.188 | 9.297 | pass |
| `temporal_sanity` | 1.094 | 0.063 | 0.828 | 2.688 | pass |
| `pii_name_match` | 8.047 | 2.078 | 21.578 | 117.797 | errored |
| `pii_value_match` | 8.250 | 2.718 | 24.718 | 115.188 | errored |
<!-- END GENERATED: checks -->

[Per-check CSV](checks.csv) retains verdicts, exact millisecond durations, observed
populations, sample sizes, errors and skip reasons. Complete result JSON also retains
compiled queries, parameters, measurements, estimates and evidence. The rig generates
JSONL query/stage telemetry, but those logs are not retained here. Result `estimate` is authoritative
for actual sampling; integrated sampling can differ from telemetry's sampled flag.

## Sampling and coverage

| Check | Default transition | Sample | Observed runs |
| --- | --- | ---: | --- |
| hub_outlier | More than 100,000 nodes with the configured label | 1,000 nodes | Exhaustive at 100k (30k customers); sampled at 1M (300k customers) and 10M (3M customers) |
| pii_name_match | More than 1,000 property occurrences | 1,000 occurrences | Sampled at 100k and 1M; sampled query errored at 10M |
| pii_value_match | More than 1,000 eligible string-property occurrences | 1,000 occurrences | Sampled at 100k and 1M; sampled query errored at 10M |
| Other executable core checks | Never switch to sampling | Exhaustive | Completed at all three sizes |

PII name populations were 380,000 and 3,800,000 on the successful 100k/1M runs;
PII string-value populations were 270,000 and 2,700,000. At 10M there is no successful
PII estimate: `estimate: false` on the errors means no estimate was produced, not
that an exhaustive audit completed. Explicit check sample sizes can lower the default
sample/transition. The global 10,000-sample cap does not replace the packs' 1,000 defaults.

Sampling bounds the evaluated sample and evidence, but the PII query plans still
perform population scans. [Captured 10M EXPLAIN plans](pii-plans-10m.json) include
AllNodesScan, per-node Sort/EagerAggregation and Top. These are diagnostic plans,
not PROFILE measurements; they do not identify a precise allocator as the root cause.
A small returned sample does not guarantee constant server memory or complete PII discovery.

The five planted hubs were detected by the exhaustive 100k hub check. The sampled
1M and 10M hub checks returned pass despite those planted hubs, demonstrating that
rare outliers can be missed by the default sample.

`dangling_rels` is included in every run and recorded as `skipped: unsupported`.
The adapter supports **count stores** (`count_store: true` in every target), which
provide aggregate counts. Detecting relationship records with missing endpoints
requires the separate **store_consistency** capability, which the stock adapter
cannot expose through read-only Cypher. Its recorded fix is Neo4j's offline consistency
checker or a connector exposing a read-only relationship-store consistency probe.

## Beyond the 10M ceiling

The live CLI was run against the existing 10,000,005-node / 9,500,000-relationship
fixture. It returned exit code 3 before dispatching checks and wrote normal run artifacts.
The final boundary verification took 2.064 seconds including CLI startup and artifact writing.
The original generator's extra five hubs explain why this nominal 10M fixture is
slightly above the exact ceiling. [Raw CLI proof](beyond-ceiling-cli.json).

```text
engine.graph_size_exceeded: Graph size (10000005 nodes, 9500000 relationships) exceeds the supported ceiling of 10,000,000 nodes.
Fix: Audit a smaller database or partition within the node limit. Selecting fewer checks or enabling sampling does not bypass the graph-size ceiling. See docs/reference/compatibility.md#supported-graph-size.
```

The normal CLI has no size-guard bypass. The benchmark-only explicit bypass is for
capacity investigation and retains the default concurrency/deadline plus its watchdog.
The boundary test verifies immediate rejection on a healthy reachable server; it does
not turn the rig watchdog into a general CLI network-failure guarantee.
