# PR 08 — Reduce sampling work and tune execution concurrency

- Category: Neo4j execution performance
- Roadmap source: Step 4, sampling/concurrency phase
- Prerequisites: PRs 02 and 04
- Suggested PR title: `perf: bound sampling candidates and expose workload concurrency`

## Goal

Reduce full-population sorting/count duplication in hub and PII checks, and let operators prevent
concurrent scan-heavy checks from overwhelming a shared Neo4j server.

## Scope

- Reuse eligible population counts where semantics permit.
- Evaluate a deterministic pre-gate before sample ordering.
- Preserve auditable estimate metadata.
- Expose bounded engine concurrency through project configuration and CLI.
- Configure driver pool/fetch/timeout settings consistently with the workload.

## Non-goals

- Profiler modes or profiler-stage concurrency.
- Replacing deterministic sampling with nondeterministic `rand()`.
- Claiming an approximate population is exact.
- Automatically choosing concurrency without benchmark evidence.

## Files expected to change

- `src/graphcheck/engine/core_pack.py`
- `src/graphcheck/engine/pii_pack.py`
- `src/graphcheck/engine/sampling.py`
- `src/graphcheck/engine/runner.py`
- `src/graphcheck/neo4j_adapter.py`
- project/CLI configuration models and docs
- sampling property tests and performance tests
- SPEC-04 and SPEC-09 if probability/estimate semantics change

## Sampling design requirements

- Same graph identity, check, and seed produce the same sample.
- Different seeds change selection.
- Every eligible element has a defensible selection probability.
- `estimate.population`, `estimate.sample_size`, and confidence interval inputs match the actual
  algorithm.
- A fast approximate mode is explicitly labeled and never used where an exact contract is required.

Candidate alternatives:

- deterministic hash gate followed by top-N ordering over the reduced candidate set;
- deterministic oversampling followed by stable truncation;
- count-store-backed population queries;
- reuse of the runner's population preflight;
- exact and fast modes with distinct metadata.

Choose through plan and distribution evidence, not query length.

## Concurrency design

Expose a positive integer setting with precedence documented across CLI, project config, and engine
default. Tie the Neo4j driver pool to the maximum worker count and set explicit:

- connection timeout;
- connection-acquisition timeout;
- fetch size;
- retry budget compatible with the GraphCheck deadline.

Benchmark concurrency 1, 2, and 4 on scan-heavy and selective workloads. A higher worker count is
not automatically the default.

## Tests

Run:

```console
uv run pytest tests/engine/test_sampling.py tests/engine/test_runner.py tests/engine/test_pii_pack_runtime.py -q
```

Required coverage:

- seed determinism and sensitivity;
- distribution across many seeds;
- empty and smaller-than-requested populations;
- population count reuse;
- exact versus approximate metadata;
- concurrency precedence and validation;
- worker limit enforcement;
- pool settings consistent with concurrency;
- run deadlines under queued and active work.

## Acceptance criteria

- Sampling avoids duplicated population work where semantics allow.
- Any reduced candidate algorithm passes distribution tolerances.
- Approximation is visible in result metadata and specs.
- Operators can set concurrency without editing code.
- Default concurrency is selected from benchmark evidence.
- Scan-heavy benchmark impact on Neo4j is lower or clearly bounded.

## Rollback

Retain the current exact deterministic sampling path behind an internal strategy switch until the
new algorithm is validated. Concurrency configuration can remain even if the sampling strategy is
reverted.

## PR checklist

- [ ] Statistical evidence is attached.
- [ ] Exact/approximate semantics are not conflated.
- [ ] Concurrency benchmarks include 1, 2, and 4 workers.
- [ ] Driver timeouts cannot exceed GraphCheck's command/check deadlines.
