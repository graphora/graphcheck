"""GraphRAG provenance templates and bounded duplicate-name discovery."""

from collections import Counter, defaultdict
from dataclasses import replace
from unicodedata import category, normalize

from graphcheck.engine.compiler import (
    ConformancePlan,
    EvidenceCondition,
    register_conformance_compiler,
)
from graphcheck.engine.core_pack import _compile_no_orphans, _node_pointer, _relationship_path
from graphcheck.engine.identifiers import node_pattern, property_access
from graphcheck.engine.sampling import (
    CYPHER_SAMPLE_MODULUS,
    cypher_hash_expression,
    cypher_hash_parameters,
)
from graphcheck.packs.graphrag import GraphRAGModel

MAX_NAME_LENGTH = 256


def configured_model(config: dict) -> GraphRAGModel | None:
    fields = GraphRAGModel.model_fields
    if any(config.get(name) is None for name, field in fields.items() if field.is_required()):
        return None
    return GraphRAGModel.model_validate({name: config[name] for name in fields if name in config})


def model_presence_query(model: GraphRAGModel) -> tuple[str, dict]:
    labels = list(dict.fromkeys([model.document_label, model.chunk_label, model.entity_label]))
    counts = "\n".join(
        f"CALL {{ MATCH {node_pattern('n', label)} RETURN count(n) AS count_{i} }}"
        for i, label in enumerate(labels)
    )
    missing = ", ".join(
        f"CASE WHEN count_{i} = 0 THEN $labels[{i}] END" for i in range(len(labels))
    )
    return f"{counts}\nRETURN [label IN [{missing}] WHERE label IS NOT NULL] AS missing_labels", {
        "labels": labels
    }


def _reverse(direction: str) -> str:
    return {"out": "in", "in": "out", "any": "any"}[direction]


def _orphan_side(
    label: str, other: str, rel: str, direction: str, cap: int, key: str
) -> ConformancePlan:
    plan = _compile_no_orphans(
        {"label": label, "to_label": other, "rel_type": rel, "direction": direction},
        cap,
        0,
        evidence_projection=(
            f"{{node_id: elementId(n), missing_path: ${key}, pointer: {_node_pointer('n')}}}"
        ),
    )
    path = _relationship_path(
        direction,
        relationship_type=rel,
        other_label=other,
        label=label,
    )
    # The model preflight checks label populations; missing relationship tokens are defects.
    params = {**plan.params, "required_labels": [], "required_relationship_types": [], key: path}
    return replace(plan, params=params, evidence_params=params)


@register_conformance_compiler("orphan_chunks")
def _compile_orphan_chunks(config: dict, evidence_cap: int, sample_seed: int) -> ConformancePlan:
    model = GraphRAGModel.model_validate({key: config[key] for key in GraphRAGModel.model_fields})
    chunks = _orphan_side(
        model.chunk_label,
        model.document_label,
        model.document_chunk_rel,
        _reverse(model.document_chunk_direction),
        evidence_cap,
        "chunk_missing_path",
    )
    documents = _orphan_side(
        model.document_label,
        model.chunk_label,
        model.document_chunk_rel,
        model.document_chunk_direction,
        evidence_cap,
        "document_missing_path",
    )
    params = {**chunks.params, **documents.params}
    return ConformancePlan(
        query=(
            f"CALL {{ {chunks.query}\nUNION ALL\n{documents.query} }}\n"
            "RETURN true AS schema_ok, sum(population) AS population, "
            "sum(violation_count) AS violation_count, [] AS evidence"
        ),
        params=params,
        expected={"orphans": 0},
        name="Chunks and documents have provenance links",
        evidence_query=(
            f"CALL {{ {chunks.evidence_query}\nUNION ALL\n{documents.evidence_query} }}\n"
            "UNWIND evidence AS finding\n"
            "WITH finding ORDER BY finding.node_id LIMIT $evidence_cap\n"
            "RETURN collect(finding) AS evidence"
        ),
        evidence_params=params,
        evidence_condition=EvidenceCondition("violation_count", "gt", 0),
    )


@register_conformance_compiler("entity_without_provenance")
def _compile_entity_without_provenance(
    config: dict, evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    model = GraphRAGModel.model_validate({key: config[key] for key in GraphRAGModel.model_fields})
    return _orphan_side(
        model.entity_label,
        model.chunk_label,
        model.chunk_entity_rel,
        _reverse(model.chunk_entity_direction),
        evidence_cap,
        "missing_path",
    )


def _missing_path_plan(match: str, violation: str, finding: str, params: dict) -> ConformancePlan:
    """Exact fixed-path anti-match with a separate, capped evidence query."""
    return ConformancePlan(
        query=(
            f"MATCH {match}\n"
            "WHERE $extraction_rel_types IS NULL OR type(r) IN $extraction_rel_types\n"
            "RETURN true AS schema_ok, count(r) AS population, "
            f"sum(CASE WHEN {violation} THEN 1 ELSE 0 END) AS violation_count, [] AS evidence"
        ),
        params=params,
        expected={"relationships_without_provenance": 0},
        name="Extraction relationship endpoints have chunk provenance",
        evidence_query=(
            f"MATCH {match}\n"
            "WHERE ($extraction_rel_types IS NULL OR type(r) IN $extraction_rel_types) "
            f"AND ({violation})\n"
            "WITH source, target, r ORDER BY elementId(r) LIMIT $evidence_cap\n"
            f"RETURN collect({finding}) AS evidence"
        ),
        evidence_params=params,
        evidence_condition=EvidenceCondition("violation_count", "gt", 0),
    )


@register_conformance_compiler("dangling_extraction_relationships")
def _compile_dangling_extraction_relationships(
    config: dict, evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    model = GraphRAGModel.model_validate({key: config[key] for key in GraphRAGModel.model_fields})

    def path(variable: str, label: str | None = None) -> str:
        return _relationship_path(
            _reverse(model.chunk_entity_direction),
            relationship_type=model.chunk_entity_rel,
            other_label=model.chunk_label,
            variable=variable,
            label=label,
            relationship_variable="",
        )

    source_missing = f"NOT EXISTS {{ MATCH {path('source')} }}"
    target_missing = f"NOT EXISTS {{ MATCH {path('target')} }}"
    finding = f"""{{rel_id: elementId(r), source_id: elementId(source),
        target_id: elementId(target),
        missing_path: $missing_path,
        missing_node_ids: [id IN [CASE WHEN {source_missing} THEN elementId(source) END,
                                 CASE WHEN {target_missing} THEN elementId(target) END]
                           WHERE id IS NOT NULL],
        pointer: {{kind: 'rel', id: elementId(r), type: type(r)}},
        source: {_node_pointer("source")}, target: {_node_pointer("target")}}}"""
    return _missing_path_plan(
        f"{node_pattern('source', model.entity_label)}-[r]->"
        f"{node_pattern('target', model.entity_label)}",
        f"({source_missing}) OR ({target_missing})",
        finding,
        {
            "extraction_rel_types": model.extraction_rel_types,
            "evidence_cap": evidence_cap,
            "missing_path": path("entity", model.entity_label),
        },
    )


@register_conformance_compiler("near_duplicate_entities")
def _compile_near_duplicate_entities(
    config: dict, evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    node, name = (
        node_pattern("n", config["entity_label"]),
        property_access("n", config["name_property"]),
    )
    rank = cypher_hash_expression(f"id(n) % {CYPHER_SAMPLE_MODULUS}")
    gate = rank.replace("$sample_hash_", "$sample_gate_hash_")
    eligible = (
        f"toStringOrNull({name}) = {name} AND size(toStringOrNull({name})) <= $max_name_length"
    )
    return ConformancePlan(
        query=f"""CYPHER 5
CALL {{ MATCH {node} WHERE {eligible} RETURN count(n) AS population }}
CALL {{
  WITH population
  MATCH {node} WHERE {eligible}
  WITH population, n, {gate} AS gate
  WHERE population <= $sample_size * 32 OR gate < toInteger(ceil(
    toFloat({CYPHER_SAMPLE_MODULUS}) * $sample_size * 32 / population))
  WITH n, {rank} AS rank ORDER BY rank, elementId(n) LIMIT $sample_size
  RETURN collect({{node_id: elementId(n), name: {name},
                   pointer: {_node_pointer("n")}}}) AS candidates
}}
RETURN true AS schema_ok, population, size(candidates) AS sample_size, candidates""",
        params={
            "sample_size": config["sample_size"],
            "max_name_length": MAX_NAME_LENGTH,
            **cypher_hash_parameters(sample_seed),
            **{
                key.replace("sample_hash_", "sample_gate_hash_"): value
                for key, value in cypher_hash_parameters(sample_seed + 1).items()
            },
        },
        expected={
            "threshold": config["threshold"],
            "max_name_length": MAX_NAME_LENGTH,
            "similarity": "character-bigram Dice",
            "grouping": "connected components",
        },
        name="Sampled entity names have no near duplicates",
        sampled=True,
        sampling_preflight=False,
    )


def normalized_name(name: str) -> str:
    return "".join(
        char
        for char in normalize("NFKC", name).casefold()
        if not char.isspace() and not category(char).startswith("P")
    )


def duplicate_groups(candidates: list[dict], threshold: float) -> list[dict]:
    """Connected components of equal keys or sufficiently similar bigram multisets."""
    by_key = defaultdict(list)
    for candidate in candidates:
        if key := normalized_name(candidate["name"]):
            by_key[key].append(candidate["node_id"])
    keys = sorted(by_key)
    parents = list(range(len(keys)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    grams = [Counter(key[index : index + 2] for index in range(len(key) - 1)) for key in keys]
    sizes = [sum(counter.values()) for counter in grams]
    # Fixed-size bucket masks conservatively bound overlap, including Unicode names.
    # Hash collisions only cause extra exact comparisons.
    masks = [
        sum({1 << ((ord(gram[0]) * 131 + ord(gram[1])) % 1024) for gram in row}) for row in grams
    ]
    repeats = [size - mask.bit_count() for size, mask in zip(sizes, masks, strict=True)]
    for right, right_grams in enumerate(grams if threshold < 1 else []):
        for left in range(right):
            if root(left) == root(right):
                continue
            total = sizes[left] + sizes[right]
            upper = (masks[left] & masks[right]).bit_count() + min(repeats[left], repeats[right])
            if not total or 2 * min(sizes[left], sizes[right], upper) <= threshold * total:
                continue
            overlap = sum(
                min(count, right_grams.get(gram, 0)) for gram, count in grams[left].items()
            )
            if 2 * overlap > threshold * total:
                parents[root(right)] = root(left)
    grouped = defaultdict(list)
    for index, key in enumerate(keys):
        grouped[root(index)].append(key)
    return [
        {
            "normalized_key": group[0],
            "normalized_keys": group,
            "node_ids": sorted(node_id for key in group for node_id in by_key[key]),
        }
        for group in grouped.values()
        if sum(len(by_key[key]) for key in group) > 1
    ]
