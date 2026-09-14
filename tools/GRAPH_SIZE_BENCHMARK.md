# Graph-size benchmark rig

This extends the C1 performance workload in `tests/performance/test_engine_budget.py`.
Unlike its repeated 30-check subset, `graph_size_suite.yml` selects every core and PII
check exactly once: 12 core checks and two PII checks. `dangling_rels` remains selected
and is recorded as unsupported by the stock adapter. Count-store support is available;
it does not provide the separate store-consistency capability required by that check.

## Workload

Use 100,000, 1,000,000 and 10,000,000 nodes, with respectively 95,000, 950,000 and
9,500,000 relationships. These represent a smaller production audit, a mid-scale
revenue/compliance application, and the large stress target of the existing C1 rig.
They are test workloads, not an empirical distribution of customer databases.

`generate_graph_size.py` follows the pinned fraud-ring fixture's scale generator:
30% Customer, 50% Account, 20% Transaction; OWNS, CONTROLS, SENT and RECEIVED_BY edges;
five customers each control 2% of accounts; synthetic email and national-ID values.
It counts the five hubs inside the requested total and adds `settled_at` one hour
after `ts` so the complete core pack can exercise temporal comparisons. Each label
has an ID uniqueness constraint. There are 3.8 property occurrences and 2.7 string
property occurrences per node across the graph. The shape is sparse and indexed;
results do not establish equivalent performance for dense graphs or arbitrary Cypher.

## Reproduce

Use the locked development environment (`uv sync --group dev`) and a disposable
Neo4j Enterprise instance. Set `GRAPHCHECK_PERFORMANCE_URI`,
`GRAPHCHECK_PERFORMANCE_PASSWORD`, and `GRAPHCHECK_PERFORMANCE_USER` in the environment.
The benchmark user must have only the built-in `reader` role and `PUBLIC`.
The loader uses `GRAPHCHECK_PERFORMANCE_ADMIN_USER` (default `neo4j`) with the same
password to create dedicated databases. No credentials are stored in result files.

For each size, run the loader and then the measurement sequentially; do not load
another dataset while measuring. Example:

```console
uv run python tools/generate_graph_size.py --nodes 100000
uv run python tools/benchmark_graph_size.py --nodes 100000 --database graphcheck-benchmark-100000 --output tools/graph-size-results/100000-run1.json
```

Repeat with `1000000` and `10000000`. The loader creates only databases named
`graphcheck-benchmark-<size>` and refuses to populate a nonempty database. It never
clears existing data. For a repeat measurement, keep the dataset and choose a new
output filename; recorded evidence is never overwritten.

Use `--allow-unsupported-size` explicitly when calibrating a workload beyond the
published ceiling. The raw result records this bypass. It disables only the engine
size guard; the two-worker default, sampling defaults, 295-second deadline and
330-second watchdog still apply. The GraphCheck CLI exposes no size-limit bypass.

On Windows, add `--server-pid <Neo4j Java PID>` to collect server resident memory.
Verify the PID using Neo4j's `java.lang:type=Runtime` JMX `Name` attribute; do not
confuse the DBMS process with the Neo4j Desktop manager. For remote servers or other
operating systems, collect server memory separately; a missing metric stays null.

## Measurement contract

- All 14 checks share the default two workers and one 295-second engine budget.
  Loading, count validation, process startup and CLI HTML rendering are excluded
  from `engine_wall_ms`. `process_wall_ms` also includes worker startup, validation,
  serialization and shutdown. Per-check `duration_ms` includes compilation,
  measurement and evaluation, but excludes waiting for a worker. Concurrent check
  durations must not be summed to infer wall-clock time.
- The global sampling settings remain unchanged (100,000 exhaustive population,
  10,000 maximum sample, seed 0); the hub and PII pack defaults cap samples at 1,000.
  Actual sampling is determined from `results.checks[].estimate`, including the
  observed population and confidence interval. PII samples property occurrences.
- Python memory is the OS process-lifetime peak resident set of a fresh worker,
  including imports and the driver. Server memory is the maximum observed working
  set of the entire Neo4j process, sampled every 100 ms during the engine run. It
  includes other loaded databases and can miss shorter spikes; it is not heap-only
  memory or an isolated database allocation. Raw samples are retained.
- Results include every check's verdict, duration, estimate, error and skip reason,
  plus source fingerprints and the complete GraphCheck result. The adjacent event
  log records query/stage timings and timeout outcomes as they arrive. Query timeout
  sequences are one-based indexes into the suite. The result estimate is authoritative
  for sampling; telemetry's sampled flag can differ for compiler-integrated sampling.
- A separate 330-second parent watchdog kills an unresponsive benchmark worker and
  writes `benchmark.watchdog_timeout` with a `Fix:` diagnostic. This watchdog belongs
  to the rig, not the GraphCheck CLI. A watchdog failure is not a successful size run;
  incomplete measurements remain null and the event log preserves completed work.
- Findings from synthetic PII and hubs are expected. A successful full-pack measurement needs all
  13 executable checks to finish without execution errors or deadline skips. The
  known unsupported `dangling_rels` makes full-pack coverage partial at every size.

Raw measurements and the Markdown report live in [`graph-size-results/`](graph-size-results/).
Keep exploratory and failed runs as well as published measurements; identify cache
conditions and repetitions in the report rather than presenting a single run as a
statistical latency guarantee.
