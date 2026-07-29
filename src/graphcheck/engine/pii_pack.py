from __future__ import annotations

from textwrap import dedent

from graphcheck.engine.compiler import ConformancePlan, register_conformance_compiler
from graphcheck.engine.identifiers import node_pattern, property_access
from graphcheck.engine.sampling import (
    CYPHER_SAMPLE_MODULUS,
    cypher_hash_expression,
    cypher_hash_parameters,
)
from graphcheck.errors import GraphCheckError
from graphcheck.packs.metadata import PiiPackMetadata

_DEFAULT_SAMPLE_SIZE = 1000
_SAMPLE_NODE_MULTIPLIER = 1_103_515_245
_SAMPLE_GATE_MULTIPLIER = 32

_SCHEMA_CATALOG = """
CALL {
  CALL db.labels() YIELD label
  RETURN collect(label) AS _gc_labels
}
CALL {
  CALL db.relationshipTypes() YIELD relationshipType
  RETURN collect(relationshipType) AS _gc_rel_types
}
CALL {
  CALL db.propertyKeys() YIELD propertyKey
  RETURN collect(propertyKey) AS _gc_properties
}
"""

_SCHEMA_PROJECTION = """
all(name IN $required_labels WHERE name IN _gc_labels)
  AND all(name IN $required_relationship_types WHERE name IN _gc_rel_types)
  AND all(name IN $required_properties WHERE name IN _gc_properties) AS schema_ok,
[name IN $required_labels WHERE NOT name IN _gc_labels] AS missing_labels,
[name IN $required_relationship_types WHERE NOT name IN _gc_rel_types]
  AS missing_relationship_types,
[name IN $required_properties WHERE NOT name IN _gc_properties] AS missing_properties
"""


def _node_pointer(variable: str) -> str:
    return f"{{kind: 'node', id: toString(id({variable})), labels: labels({variable})}}"


def _candidate_query(*, strings_only: bool, label: str | None, properties: list[str]) -> str:
    node = node_pattern("n", label)
    configured_rows = (
        "["
        + ", ".join(
            f"{{property: $properties[{index}], raw: {property_access('n', property_name)}}}"
            for index, property_name in enumerate(properties)
        )
        + "]"
        if properties
        else None
    )
    population_unwind = (
        f"UNWIND {configured_rows} AS occurrence\n"
        "        WITH occurrence.property AS property, occurrence.raw AS raw"
        if configured_rows
        else "UNWIND keys(n) AS property\n        WITH property, n[property] AS raw"
    )
    candidate_unwind = (
        f"UNWIND {configured_rows} AS occurrence\n"
        "          WITH n, occurrence.property AS property, occurrence.raw AS raw"
        if configured_rows
        else "UNWIND keys(n) AS property\n          WITH n, property, n[property] AS raw"
    )
    string_predicate = "AND toStringOrNull(raw) = raw" if strings_only else ""
    value_projection = ", value: raw" if strings_only else ""
    population_query = (
        f"""
        MATCH {node}
        {population_unwind}
        WHERE raw IS NOT NULL
          AND toStringOrNull(raw) = raw
        RETURN count(*) AS population
        """
        if strings_only
        else f"""
        MATCH {node}
        RETURN coalesce(sum(size(keys(n))), 0) AS population
        """
    )
    hash_expression = cypher_hash_expression("_gc_occurrence_key")
    gate_hash_expression = cypher_hash_expression("id(n) % 2147483647").replace(
        "$sample_hash_",
        "$sample_gate_hash_",
    )
    return dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          {dedent(population_query).strip()}
        }}
        CALL {{
          WITH population
          WITH population
          WHERE population > 0
          MATCH {node}
          WITH population, n, {gate_hash_expression} AS _gc_gate_key
          WHERE population <= $sample_size * $sample_gate_multiplier
             OR _gc_gate_key < toInteger(ceil(
                  toFloat({CYPHER_SAMPLE_MODULUS})
                  * toFloat($sample_size)
                  * toFloat($sample_gate_multiplier)
                  / toFloat(population)
                ))
          {candidate_unwind}
          WHERE raw IS NOT NULL
            {string_predicate}
          WITH n, property, raw ORDER BY id(n), property
          WITH n, collect({{property: property, raw: raw}}) AS _gc_node_properties
          UNWIND range(0, size(_gc_node_properties) - 1) AS _gc_property_index
          WITH n, _gc_property_index,
               _gc_node_properties[_gc_property_index] AS occurrence
          WITH n, occurrence.property AS property, occurrence.raw AS raw,
               _gc_property_index
          WITH n, property, raw,
               (((id(n) % {CYPHER_SAMPLE_MODULUS}) * {_SAMPLE_NODE_MULTIPLIER}
                 + _gc_property_index) % {CYPHER_SAMPLE_MODULUS}) AS _gc_occurrence_key
          WITH n, property, raw, {hash_expression} AS _gc_sample_key
          ORDER BY _gc_sample_key, id(n), property
          LIMIT $sample_size
          RETURN collect({{
            evidence: {_node_pointer("n")},
            property: property{value_projection}
          }}) AS candidates
        }}
        RETURN {_SCHEMA_PROJECTION},
               population,
               size(candidates) AS sample_size,
               candidates
        """
    ).strip()


def _common_config(
    config: dict[str, object],
    *,
    evidence_cap: int,
    sample_seed: int,
    properties: list[str],
    required_properties: list[str] | None = None,
) -> dict[str, object]:
    label = _configured_label(config)
    requested_sample_size = config.get("sample_size")
    if requested_sample_size is None:
        requested_sample_size = _DEFAULT_SAMPLE_SIZE
    if (
        isinstance(requested_sample_size, bool)
        or not isinstance(requested_sample_size, int)
        or requested_sample_size < 1
    ):
        raise _invalid("PII sample_size must be a positive integer.")
    if isinstance(sample_seed, bool) or not isinstance(sample_seed, int) or sample_seed < 0:
        raise _invalid("PII sample_seed must be a non-negative integer.")
    hash_params = cypher_hash_parameters(sample_seed)
    gate_hash_params = {
        name.replace("sample_hash_", "sample_gate_hash_"): value
        for name, value in cypher_hash_parameters(sample_seed + 1).items()
    }
    params = {
        **({"properties": properties} if properties else {}),
        "sample_size": requested_sample_size,
        "sample_gate_multiplier": _SAMPLE_GATE_MULTIPLIER,
        **hash_params,
        **gate_hash_params,
        "required_labels": [label] if label is not None else [],
        "required_relationship_types": [],
        "required_properties": required_properties or [],
    }
    del evidence_cap
    return params


def _configured_label(config: dict[str, object]) -> str | None:
    label = config.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip()):
        raise _invalid("PII label must be a non-blank string when supplied.")
    return label.strip() if isinstance(label, str) else None


@register_conformance_compiler("pii_name_match")
def _compile_pii_name_match(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    metadata = _pii_metadata(config)
    selected = _selected_patterns(config, metadata.name_match.patterns)
    pattern_rows = [
        {"id": pattern_id, "keys": list(metadata.name_match.patterns[pattern_id].keys)}
        for pattern_id in selected
    ]
    params = _common_config(
        config,
        evidence_cap=evidence_cap,
        sample_seed=sample_seed,
        properties=[],
    )
    return ConformancePlan(
        query=_candidate_query(
            strings_only=False,
            label=_configured_label(config),
            properties=[],
        ),
        params=params,
        expected={
            "confidence": metadata.name_match.confidence,
            "patterns": pattern_rows,
            "completeness_notice": metadata.completeness_notice,
        },
        name="Property names do not expose likely personal data",
        sampled=True,
        sampling_preflight=False,
    )


@register_conformance_compiler("pii_value_match")
def _compile_pii_value_match(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    metadata = _pii_metadata(config)
    selected = _selected_patterns(config, metadata.value_match.patterns)
    pattern_rows = [
        {
            "id": pattern_id,
            "regex": metadata.value_match.patterns[pattern_id].regex,
            "checksum": metadata.value_match.patterns[pattern_id].checksum,
        }
        for pattern_id in selected
    ]
    configured_properties = config.get("properties")
    if configured_properties is not None and (
        not isinstance(configured_properties, list)
        or not configured_properties
        or not all(isinstance(item, str) and item.strip() for item in configured_properties)
    ):
        raise _invalid("PII properties must be a non-empty list of non-blank strings.")
    properties = (
        [] if configured_properties is None else [item.strip() for item in configured_properties]
    )
    params = _common_config(
        config,
        evidence_cap=evidence_cap,
        sample_seed=sample_seed,
        properties=properties,
        required_properties=properties,
    )
    return ConformancePlan(
        query=_candidate_query(
            strings_only=True,
            label=_configured_label(config),
            properties=properties,
        ),
        params=params,
        expected={
            "confidence": metadata.value_match.confidence,
            "patterns": pattern_rows,
            "completeness_notice": metadata.completeness_notice,
        },
        name="Sampled property values do not match known personal-data formats",
        sampled=True,
        sampling_preflight=False,
    )


def _pii_metadata(config: dict[str, object]) -> PiiPackMetadata:
    metadata = config.pop("__pack_metadata__", None)
    if not isinstance(metadata, PiiPackMetadata):
        raise GraphCheckError(
            "packs.pii_missing",
            "The installed pack catalog does not contain PII metadata.",
            "Install the built-in pii.yml pack metadata with GraphCheck.",
        )
    return metadata


def _selected_patterns(config: dict[str, object], available: object) -> list[str]:
    if not isinstance(available, dict):
        available = dict(available)
    requested = config.get("patterns")
    if requested is not None and (
        not isinstance(requested, list)
        or not requested
        or not all(isinstance(item, str) and item.strip() for item in requested)
    ):
        raise _invalid("PII patterns must be a non-empty list of non-blank strings.")
    selected = list(available) if requested is None else [item.strip() for item in requested]
    unknown = sorted(set(selected) - set(available))
    if unknown:
        rendered = ", ".join(unknown)
        raise GraphCheckError(
            "packs.pii_pattern_unknown",
            f"Unknown PII pattern(s): {rendered}.",
            "Use pattern ids declared by the installed pii.yml metadata.",
        )
    return selected


def _invalid(message: str) -> GraphCheckError:
    return GraphCheckError(
        "engine.invalid_check",
        message,
        "Fix the PII check's `with` payload and run it again.",
    )
