# Evaluated agent authoring

This direct harness measures first-submission check authoring, not the SPEC-11 provider's proposal
repair loop. See [RESULTS.md](RESULTS.md) for the measured matrix and
[authoring findings](../../docs/maintainers/agent-authoring-findings.md) for the fixes.

## Fixed inputs and sessions

The task set is [task-set.json](task-set.json): ten natural-language intents with expected verdicts,
independent Cypher reference queries, and exact expected query rows. Reference queries were verified
before launching the models. The fixture is the initialized fraud-ring submodule at
`5dc14f81dd6f834f166a102b65e9866a240fa035`, using `seed.cypher` on Neo4j Community 5.26.28 without APOC.
It contains 5,011 nodes and 5,872 relationships. The frozen profile is also the supplied `latest`
baseline; drift measures this same state against itself.

The identical [context](context/) contains the original agent guide, original llms.txt, original
check schema, a real complete GraphCheck profile, fixture schema, and intents without the answer
key. [manifest.json](manifest.json) records input hashes and the fixture digest. The current docs
contain fixes; the snapshots retain what the models actually read. Frozen inputs and raw YAML have
Git text conversion disabled. External fixture sources use LF-normalized hashes for portability;
the original Windows byte hashes are retained as provenance.

[sessions.json](sessions.json) records one separate Codex agent session per model: `gpt-5.6-luna`,
`gpt-5.6-terra`, and `gpt-5.6-sol`, all at `max` effort with `fork_turns: none`. Each received
[PROMPT.md](PROMPT.md), the six allowed context files, and its own output directory. Expected
verdicts, reference queries, source code, and other outputs were withheld. Each session authored
all ten intents. There was no validation, execution feedback, best-of selection, or repair loop.
Initial usage-limit failures wrote no files; those sessions resumed once usage was available.

[raw/](raw/) preserves the submitted YAML bytes. [submissions.json](submissions.json) hashes all
30 files before scoring. The harness refuses changed inputs, changed submissions, a mismatched
fixture, and overwriting an existing results table.

## Four independent gates

1. **Valid:** the unmodified UTF-8 YAML parses with duplicate-key detection and satisfies the frozen
   `check.schema.json` using Draft 2020-12 plus format checking.
2. **Loads:** the original text passes the real `load_suite()` semantic checks. This is measured
   independently of the JSON Schema gate.
3. **Runs:** exactly the requested suite/check executes through `Engine` and `Neo4jClient` and
   finishes with `pass`, `fail`, or `warn`. `errored`/`skipped` do not count. Community uses the
   connector's server-side planner preflight and READ access mode; no unrestricted driver is
   handed to generated checks. A 30-second budget and one worker apply per suite.
4. **Correct:** execution succeeded and the actual verdict equals the fixed expected verdict.
   This measures fixture verdict agreement, not general semantic equivalence across graphs.

All raw files retain `generated: true`. The user's evaluation request authorized a temporary
in-memory copy with only the generated flags disabled. Nothing activates these proposals in the
project's checks directory. Complete graph labels, properties, relationship endpoints, and types
are hashed before and after scoring; the fixture remained unchanged.

[results/results.csv](results/results.csv) is the 30-row table.
[results/results.json](results/results.json) includes errors, hashes, versions, and read-guard
statistics. Every loaded submission also has a full validated GraphCheck result under
`results/<model>/<intent>.json`. The run records GraphCheck source version 0.3.0 separately from
the environment's stale installed distribution metadata (0.2.0).

## Replay the unchanged submissions

From the repository root, initialize the pinned submodule and install dependencies with
`git submodule update --init --recursive` and `uv sync --group dev`. Start a **new disposable**
Neo4j instance; the harness never reads `profiles.yml` and refuses a nonempty database when seeding.

```console
docker run --detach --rm --name graphcheck-authoring-benchmark --publish 127.0.0.1:17689:7687 --env NEO4J_AUTH=neo4j/agent-benchmark neo4j:5.26.28
```

Wait until `docker logs graphcheck-authoring-benchmark` reports `Started`, then run:

```console
uv run python tools/benchmark_agent_authoring.py seed
uv run python tools/benchmark_agent_authoring.py score --output .test-tmp/authoring-replay
docker stop graphcheck-authoring-benchmark
```

`seed` verifies the pinned fixture and all reference query rows without replacing the frozen
profile/context. `score` uses the frozen structural schema and current loader/engine. The changes
in this commit add descriptions and diagnostics; verdicts are unchanged. Execution timestamps,
Neo4j element IDs, and diagnostic text can differ from the original artifacts.

The recorded run used the official Windows Community distribution and an existing Java 21 runtime.
Its Windows Unix-domain socket failure required setting `jdk.net.unixdomain.tmpdir` to a nonexistent
path so Java used its internal TCP fallback; see the
[OpenJDK implementation](https://raw.githubusercontent.com/openjdk/jdk21u/master/src/java.base/windows/classes/sun/nio/ch/PipeImpl.java).
This is a runtime setup detail, not a model failure. Docker replay does not require that workaround.

## Repeating authoring with the revised guide

Create a new benchmark directory beneath the repository, copy only `task-set.json` and `PROMPT.md`
into it, and start a fresh empty instance. Run `prepare --benchmark-dir <new-directory>` to capture
the current guide/schema/profile. Launch three new isolated sessions with the same explicit model
IDs, `max` effort, and no inherited conversation. Give each the saved prompt, only the six context
files, and its own `raw/<model>` directory. Record returned session identifiers/settings, await all
three completions, then run `collect --benchmark-dir <new-directory>` followed by
`score --benchmark-dir <new-directory> --output <new-directory>/results`.

No new authoring trial was run after the fixes, so no model improvement is claimed. The separate
[remediations.yml](remediations.yml) contains verified repair examples, not model submissions.
Their [full results](remediation-results.json) and the
[replay/test verification record](verification.json) are saved separately from the model matrix.

For scorer regression tests, run `uv run pytest tests/repository/test_agent_authoring_benchmark.py`.
Set `GRAPHCHECK_AUTHORING_URI=bolt://127.0.0.1:17689` to also test real passing/failing assertions,
syntax errors, missing evidence, and a rejected write against the seeded disposable graph.
