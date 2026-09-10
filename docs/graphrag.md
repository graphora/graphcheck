# GraphRAG pack

Enable the four checks with `graphcheck init --pack graphrag`, then edit the example model
in `graphcheck.yml`. Run only this pack with `graphcheck run --suite graphrag`.

```yaml
packs:
  graphrag:
    enabled: true
    model:
      document_label: Document
      chunk_label: Chunk
      entity_label: __Entity__
      document_chunk_rel: PART_OF
      document_chunk_direction: out
      chunk_entity_rel: HAS_ENTITY
      chunk_entity_direction: out
      extraction_rel_types: [WORKED-WITH, LOCATED_IN, MENTORED]
      name_property: name
      embedding_property: embedding
    near_duplicate_entities:
      threshold: 0.9
      sample_size: 1000
```

These identifiers are editable examples. No node label, relationship type, or property is
hard-coded into a query. Identifiers are escaped, including spaces and backticks. Directions
are `out`, `in`, or `any`, relative to Document for the document/chunk link and Chunk for the
chunk/entity link. Omit `extraction_rel_types` to inspect all relationships whose endpoints
both have the configured entity label. An explicit list must be nonempty and unique.
`embedding_property` is reserved model configuration; these four checks do not inspect embeddings.

Enabling the pack adds a virtual `graphrag` suite containing all four checks, tagged `graphrag`.
Its effective configuration contributes to the suite hash and sampling seed. Setting
`enabled: false` removes that automatic suite. Explicit GraphRAG checks in other suites still run.
Do not name another suite `graphrag` while the automatic suite is enabled.

Individual checks can override model fields, threshold, and sample size in their `with` block:

```yaml
suite: organization-quality
conformance:
  - id: duplicate-organizations
    check: near_duplicate_entities
    with:
      entity_label: Organization
      name_property: legal_name
      threshold: 0.95
      sample_size: 500
```

All other model fields come from `packs.graphrag.model`. Without project configuration, declare
the model fields directly in each check's `with` block.

## New semantics requiring separate approval

### Model absence and results

Each GraphRAG check first verifies that its model is configured and that every configured role
label has at least one node. If a required model field is unbound or any role population is empty,
the check is **not evaluated**. A partially populated model therefore skips all automatic pack
checks as well. Existing but unused label tokens do not count as a present model.

The result uses `verdict: skipped`, the new `skip_reason: model_absent`, and a readable
`expected.not_evaluated_reason`. It has no error, measurement, estimate, or evidence. Existing
core/PII missing-schema behavior is unchanged. Real connection, query, permission, and timeout
errors still produce errors, not model-absence skips.

A completed run containing only `model_absent` skips exits **0**. Its score stays null and its
report explicitly says no checks were evaluated, with incomplete coverage. Empty selections,
generated-only selections, unsupported capabilities, interrupted checks, warnings, and failures
retain their existing exit behavior. The portable results schema adds `model_absent` to the skip
reason enum. Telemetry uses its existing unsupported bucket for these skips; no graph identifiers
or reason text are added to telemetry.

Relationship and property availability do not gate the pack: deleting every provenance link must
produce findings when all role populations still exist. A graph with no configured model, such
as the fraud-ring fixture, is therefore harmless to scan with this optional pack enabled.

### Provenance

| Check | Violations | Deliberate boundary |
| --- | --- | --- |
| `orphan_chunks` | Each Chunk lacking a configured Document link, plus each Document lacking a configured Chunk link. | At least one link is sufficient; multiple documents per chunk are allowed. |
| `entity_without_provenance` | Each Entity lacking a direct link from a Chunk of the configured type and direction. | The chunk need not itself have a document; that is covered by `orphan_chunks`. |
| `dangling_extraction_relationships` | Each selected Entity-to-Entity relationship with either endpoint lacking that direct Chunk link. | Count the relationship once even when both ends lack provenance. Endpoints need not share a chunk. This does not detect store corruption. |

These checks are exhaustive, require only read access, and fail on any violation. Alternate
relationship types, wrong endpoint labels, reversed directions (unless `any`), and indirect paths
do not satisfy the configured path. Source truth and whether text supports an extracted fact are
outside their scope. Self-loops are checked once; parallel extraction relationships count separately.

Node checks reuse `no_orphans`; its new optional `with.to_label` restricts the opposite endpoint
label. Omitting it preserves existing behavior. `cardinality` still means exactly N; using it here
would impose an unrequested one-document-per-chunk rule. The extraction check adds a fixed-path
absence template. Counts are measured separately from evidence collection in a read transaction;
evidence collection is capped, not the count.

Evidence includes Neo4j element IDs and the missing configured path in the message and structured
`measured.findings`. Extraction findings include `rel_id`, `source_id`, `target_id`, and
`missing_node_ids`. Node findings include `node_id`. Standard evidence pointers remain capped by
the engine's evidence limit, with truncation reported.

### Near-duplicate entity names

- Scope: one configured entity label and one name property; no comparisons across other labels.
  Additional labels on the same node do not split its group.
- Normalization: Unicode NFKC, case folding, then removal of Unicode punctuation and whitespace.
  Empty normalized keys do not form groups. Symbols are retained.
- Match: equal nonempty normalized keys, or multiset character-bigram Sørensen–Dice similarity
  **strictly above** `threshold` (`0 < threshold <= 1`, default `0.9`). Threshold 1 checks only
  equal normalized keys. This is a spelling heuristic, not entity identity.
- Grouping: connected components of matching names. Transitive matches can put A and C in the same
  group through B even if A and C do not directly meet the threshold. `normalized_key` is the
  lexicographically first key; `normalized_keys` lists every key and `node_ids` every sampled member.
- Sampling: use the same per-check seeded cubic hash and independent 32× candidate gate as the
  hub/PII approach, followed by stable rank and element-ID order. Sample size defaults to 1,000,
  accepts 1–2,000, and is additionally capped by the engine sampling policy. Samples reproduce for
  unchanged graph identity, suite configuration, check identity, and engine seed.
- Eligibility: string names of at most 256 characters before normalization. Missing/non-string and
  longer names are excluded, not truncated. Empty/punctuation-only names can occupy sample slots
  but do not produce duplicate groups. Evidence and measurements state actual sample size and
  eligible population. A zero eligible population passes with zero comparisons.
- Bounds: count/selection scans the eligible label; only the bounded sample is transferred and
  compared in Python. At most `s * (s - 1) / 2` unique-name pairs are considered; there is no
  database Cartesian product. Length/bigram bounds and known components avoid unnecessary work.
- Output: violations count sampled nodes belonging to duplicate groups. Findings and evidence
  messages carry normalized keys and group element IDs; they never suggest an automatic merge.
  A sampled clean result means no duplicate was found within that sample.
- Estimates: exhaustive eligible samples use `estimate: false`; strict subsets carry sample size
  and population with `ci: null`. The existing estimate shape retains its nominal `confidence: 0.95`,
  but no confidence interval or population duplicate rate is claimed: discovering a pair requires
  sampling both endpoints, so the individual Bernoulli assumption behind a Wilson interval is invalid.

## Verification

Unit tests cover configuration, metadata/schema parity, escaping, missing models, failure isolation,
paths and evidence IDs, normalization, similarity thresholds, deterministic sampling, limits, and
malformed-result rejection. Opt-in Neo4j tests cover the planted and clean `llm-kg-builder`
fixtures, the fraud-ring fixture, wholly missing links, wrong endpoint labels, and reversed custom
models. The existing public-scale hostile test also exercises duplicate discovery on its 265,214
nodes with a 1,000-name limit and a 60-second CLI ceiling.

```sh
pytest tests/unit/engine/test_graphrag_pack.py
GRAPHCHECK_NEO4J_INTEGRATION=1 pytest tests/integration/test_graphrag.py
GRAPHCHECK_NEO4J_INTEGRATION=1 GRAPHCHECK_HOSTILE_SCALE=1 pytest tests/integration/test_hostile_graphs.py -k public_scale
```
