# GraphRAG quality suite

Copy `graphrag.yml` into the `checks/` directory of an initialized GraphCheck project, edit
the model fields in the first check, and run:

```sh
graphcheck run --suite hostile-graphrag
```

The YAML anchor shares the model across all five checks. No `packs.graphrag` configuration is
required. `PART_OF` runs from Document to Chunk in this fixture; if your builder uses the reverse
direction, set `document_chunk_direction: in`. Adapt labels, relationships, and property names
to your graph. Near-duplicate discovery uses its defaults: 1,000 names and a 0.9 threshold.

The suite checks orphan chunks/documents, entities without provenance, extraction relationships
without endpoint provenance, sampled duplicate entity names, and embedding consistency. Embedding
counts cover all chunks; only capped summaries and element IDs are returned, never vectors.

The sanitized [hostile fixture](../../tests/integration/hostile/llm-kg-builder.cypher) intentionally
fails every check (exit 1). Its [clean variant](../../tests/integration/hostile/llm-kg-builder-clean.cypher)
passes (exit 0). Singleton labels and uncovered chunks are fixture assertions only.

See [GraphRAG semantics](../../docs/graphrag.md) for model absence, sampling, and evidence details.
