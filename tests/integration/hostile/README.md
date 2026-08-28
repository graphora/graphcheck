# Hostile graph certification set

This directory defines the reproducible environments used to certify `graphcheck debug`,
`graphcheck profile`, and `graphcheck run` against hostile graphs. The integration test invokes
the real command boundary in a subprocess and rejects Python tracebacks.

- `llm-kg-builder.cypher` is a sanitized fixture based on the Neo4j LLM Graph Builder's documented
  `Document`, `Chunk`, `__Entity__`, multi-label entity, and lexical relationship shapes. It adds
  inconsistent runtime property types and escaped identifiers seen in unconstrained extraction.
- `public-scale` downloads Stanford SNAP's anonymized EU email graph. The source artifact and
  SHA-256 are pinned in `cases.yml`; the published graph contains 265,214 nodes and 420,045
  directed relationships.
- `neo4j-4.4-cluster.yml` starts three Neo4j 4.4 Enterprise core members. GraphCheck intentionally
  rejects this legacy server line with `neo4j.unsupported_version`. Docker assigns the host Bolt
  ports, and the test requires `dbms.cluster.overview()` to report all three members with exactly
  one leader before invoking GraphCheck.
- The regular supported Neo4j container has no plugins, so it supplies both the APOC-less and empty
  cases without a fake capability probe.

`cases.yml` is the source of truth for runner selection, opt-in environment variables, fixtures,
suites, expected command exit codes, server versions, and scale-dataset identity.

Run the fast hostile cases with:

```console
uv run python tools/run_hostile_graphs.py --case fast
```

Run the complete certification set, including the public dataset and 4.4 cluster, with:

```console
uv run python tools/run_hostile_graphs.py --case all
```

Neo4j Enterprise is used only for the legacy cluster test and requires acceptance of Neo4j's
license through the container's `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes` setting.

Sources:

- https://github.com/neo4j-labs/llm-graph-builder/blob/main/docs/backend/backend_docs.adoc
- https://snap.stanford.edu/data/email-EuAll.html
