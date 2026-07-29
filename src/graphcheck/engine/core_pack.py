from __future__ import annotations

import math
from textwrap import dedent

from graphcheck.engine.compiler import ConformancePlan, register_conformance_compiler
from graphcheck.engine.identifiers import (
    cypher_identifier,
    node_pattern,
    property_access,
    relationship_pattern,
)
from graphcheck.engine.sampling import (
    CYPHER_SAMPLE_MODULUS,
    cypher_hash_expression,
    cypher_hash_parameters,
)
from graphcheck.errors import GraphCheckError

_DEFAULT_HUB_SAMPLE_SIZE = 1000

_SCHEMA_CATALOG = """
CALL {
  CALL db.labels() YIELD label
  RETURN collect(label) AS _gc_labels
}
CALL {
  CALL db.relationshipTypes() YIELD relationshipType
  RETURN collect(relationshipType) AS _gc_rel_types
}
"""

_SCHEMA_PROJECTION = """
all(name IN $required_labels WHERE name IN _gc_labels)
  AND all(name IN $required_relationship_types WHERE name IN _gc_rel_types) AS schema_ok,
[name IN $required_labels WHERE NOT name IN _gc_labels] AS missing_labels,
[name IN $required_relationship_types WHERE NOT name IN _gc_rel_types]
  AS missing_relationship_types
"""


def _node_pointer(variable: str) -> str:
    # id() is available on both Neo4j 4.4 and 5.x. The result contract serializes it as a string.
    return f"{{kind: 'node', id: toString(id({variable})), labels: labels({variable})}}"


def _rel_pointer(variable: str) -> str:
    return f"{{kind: 'rel', id: toString(id({variable})), type: type({variable})}}"


def _invalid(detail: str, fix: str) -> GraphCheckError:
    return GraphCheckError("engine.invalid_check", detail, fix)


def _string(config: dict[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            f"Conformance field {key!r} must be a non-blank string.",
            f"Set `with.{key}` to the identifier required by this check.",
        )
    return value


def _optional_string(config: dict[str, object], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            f"Conformance field {key!r} must be null or a non-blank string.",
            f"Remove `with.{key}` or set it to a valid graph identifier.",
        )
    return value


def _integer(
    config: dict[str, object], key: str, *, default: int | None = None, minimum: int = 0
) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid(
            f"Conformance field {key!r} must be an integer of at least {minimum}.",
            f"Set `with.{key}` to a valid integer.",
        )
    return value


def _number(
    config: dict[str, object], key: str, *, default: float | None = None, positive: bool = False
) -> float:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(
            f"Conformance field {key!r} must be numeric.",
            f"Set `with.{key}` to a finite number.",
        )
    numeric = float(value)
    if not math.isfinite(numeric) or (positive and numeric <= 0.0):
        qualifier = "a finite positive number" if positive else "a finite number"
        raise _invalid(
            f"Conformance field {key!r} must be {qualifier}.",
            f"Set `with.{key}` to {qualifier}.",
        )
    return numeric


def _direction(config: dict[str, object], *, default: str = "any") -> str:
    value = config.get("direction", default)
    if value not in {"out", "in", "any"}:
        raise _invalid(
            "Conformance direction must be one of 'out', 'in', or 'any'.",
            "Set `with.direction` to out, in, or any.",
        )
    return str(value)


def _relationship_path(
    direction: str, *, relationship_type: str | None = None, other_label: str | None = None
) -> str:
    relationship = relationship_pattern("r", relationship_type)
    other = node_pattern("other", other_label)
    return {
        "out": f"(n)-{relationship}->{other}",
        "in": f"(n)<-{relationship}-{other}",
        "any": f"(n)-{relationship}-{other}",
    }[direction]


def _schema_params(
    *, labels: list[str], relationship_types: list[str], evidence_cap: int
) -> dict[str, object]:
    return {
        "required_labels": labels,
        "required_relationship_types": relationship_types,
        "evidence_cap": evidence_cap,
    }


def _node_predicate_query(*, match: str, population: str, violation: str) -> str:
    return dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          MATCH {match}
          WHERE {population}
          RETURN count(n) AS population,
                 sum(CASE WHEN {violation} THEN 1 ELSE 0 END) AS violation_count
        }}
        CALL {{
          MATCH {match}
          WHERE ({population}) AND ({violation})
          WITH n ORDER BY id(n) LIMIT $evidence_cap
          RETURN collect({_node_pointer("n")}) AS evidence
        }}
        RETURN {_SCHEMA_PROJECTION},
               population, violation_count, evidence
        """
    ).strip()


def _relationship_predicate_query(*, match: str, population: str, violation: str) -> str:
    return dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          MATCH {match}
          WHERE {population}
          RETURN count(r) AS population,
                 sum(CASE WHEN {violation} THEN 1 ELSE 0 END) AS violation_count
        }}
        CALL {{
          MATCH {match}
          WHERE ({population}) AND ({violation})
          WITH r ORDER BY id(r) LIMIT $evidence_cap
          RETURN collect({_rel_pointer("r")}) AS evidence
        }}
        RETURN {_SCHEMA_PROJECTION},
               population, violation_count, evidence
        """
    ).strip()


def _degree_query(*, node: str, pattern: str, violation: str) -> str:
    return dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          MATCH {node}
          OPTIONAL MATCH {pattern}
          WITH n, count(r) AS degree
          RETURN count(n) AS population,
                 sum(CASE WHEN {violation} THEN 1 ELSE 0 END) AS violation_count
        }}
        CALL {{
          MATCH {node}
          OPTIONAL MATCH {pattern}
          WITH n, count(r) AS degree
          WHERE {violation}
          WITH n ORDER BY id(n) LIMIT $evidence_cap
          RETURN collect({_node_pointer("n")}) AS evidence
        }}
        RETURN {_SCHEMA_PROJECTION},
               population, violation_count, evidence
        """
    ).strip()


@register_conformance_compiler("cardinality")
def _compile_cardinality(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    from_label = _string(config, "from_label")
    rel_type = _string(config, "rel_type")
    to_label = _string(config, "to_label")
    direction = _direction(config, default="out")
    exactly = _integer(config, "exactly", default=1)
    query = _degree_query(
        node=node_pattern("n", from_label),
        pattern=_relationship_path(direction, relationship_type=rel_type, other_label=to_label),
        violation="degree <> $exactly",
    )
    params = {
        **_schema_params(
            labels=[from_label, to_label],
            relationship_types=[rel_type],
            evidence_cap=evidence_cap,
        ),
        "exactly": exactly,
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"exactly": exactly},
        name=f"{from_label} has exactly {exactly} {rel_type} relationship(s) to {to_label}",
    )


@register_conformance_compiler("no_orphans")
def _compile_no_orphans(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label = _string(config, "label")
    rel_type = _optional_string(config, "rel_type")
    direction = _direction(config)
    query = _degree_query(
        node=node_pattern("n", label),
        pattern=_relationship_path(direction, relationship_type=rel_type),
        violation="degree = 0",
    )
    params = {
        **_schema_params(
            labels=[label],
            relationship_types=[rel_type] if rel_type is not None else [],
            evidence_cap=evidence_cap,
        ),
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"orphans": 0},
        name=f"{label} nodes are connected",
    )


@register_conformance_compiler("dangling_rels")
def _compile_dangling_rels(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del config, evidence_cap, sample_seed
    # Cypher exposes a relationship only after resolving both endpoints, so MATCH can never
    # observe the backing-store corruption this C3 check describes. Reporting zero would be an
    # optimistic false pass; fail closed until C2 exposes a supported store-consistency probe.
    raise GraphCheckError(
        "engine.check_unobservable",
        "dangling_rels cannot be observed through Neo4j's read-only Cypher surface.",
        "Run Neo4j's offline consistency checker, or install a connector capability that "
        "provides a read-only relationship-store integrity probe.",
    )


_TYPE_MATCH_TEMPLATE = """
coalesce(
  CASE $expected_type
    WHEN 'string' THEN toStringOrNull(__GC_PROPERTY__) = __GC_PROPERTY__
    WHEN 'integer' THEN
      toIntegerOrNull(__GC_PROPERTY__) = __GC_PROPERTY__
      AND toStringOrNull(__GC_PROPERTY__) =~ '^-?[0-9]+$'
    WHEN 'float' THEN
      toFloatOrNull(__GC_PROPERTY__) = __GC_PROPERTY__
      AND NOT (
        toIntegerOrNull(__GC_PROPERTY__) = __GC_PROPERTY__
        AND toStringOrNull(__GC_PROPERTY__) =
          toStringOrNull(toIntegerOrNull(__GC_PROPERTY__))
      )
    WHEN 'boolean' THEN toBooleanOrNull(__GC_PROPERTY__) = __GC_PROPERTY__
    WHEN 'date' THEN
      NOT (toStringOrNull(__GC_PROPERTY__) = __GC_PROPERTY__)
      AND toStringOrNull(__GC_PROPERTY__) =~ '^-?[0-9]{4,}-[0-9]{2}-[0-9]{2}$'
    WHEN 'datetime' THEN
      NOT (toStringOrNull(__GC_PROPERTY__) = __GC_PROPERTY__)
      AND toStringOrNull(__GC_PROPERTY__) =~ '^-?[0-9]{4,}-[0-9]{2}-[0-9]{2}T.*$'
    ELSE false
  END,
  false
)
""".strip()


@register_conformance_compiler("property_type")
def _compile_property_type(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label = _string(config, "label")
    property_name = _string(config, "property")
    expected_type = _string(config, "type")
    property_ref = property_access("n", property_name)
    query = _node_predicate_query(
        match=node_pattern("n", label),
        population=f"{property_ref} IS NOT NULL",
        violation=f"NOT ({_TYPE_MATCH_TEMPLATE.replace('__GC_PROPERTY__', property_ref)})",
    )
    params = {
        **_schema_params(labels=[label], relationship_types=[], evidence_cap=evidence_cap),
        "expected_type": expected_type,
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"type": expected_type},
        name=f"{label}.{property_name} values have type {expected_type}",
    )


@register_conformance_compiler("property_format")
def _compile_property_format(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label = _string(config, "label")
    property_name = _string(config, "property")
    regex = _string(config, "regex")
    property_ref = property_access("n", property_name)
    query = _node_predicate_query(
        match=node_pattern("n", label),
        population=f"{property_ref} IS NOT NULL",
        violation=(
            f"NOT coalesce(toStringOrNull({property_ref}) = {property_ref} "
            f"AND toStringOrNull({property_ref}) =~ $regex, false)"
        ),
    )
    params = {
        **_schema_params(labels=[label], relationship_types=[], evidence_cap=evidence_cap),
        "regex": regex,
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"regex": regex},
        name=f"{label}.{property_name} matches its required format",
    )


@register_conformance_compiler("value_in_set")
def _compile_value_in_set(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label = _string(config, "label")
    property_name = _string(config, "property")
    values = config.get("values")
    if not isinstance(values, list) or not values:
        raise _invalid(
            "Conformance field 'values' must be a non-empty list.",
            "Set `with.values` to the finite set accepted by this property.",
        )
    normalized_values = list(values)
    property_ref = property_access("n", property_name)
    query = _node_predicate_query(
        match=node_pattern("n", label),
        population=f"{property_ref} IS NOT NULL",
        violation=f"NOT {property_ref} IN $values",
    )
    params = {
        **_schema_params(labels=[label], relationship_types=[], evidence_cap=evidence_cap),
        "values": normalized_values,
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"values": normalized_values},
        name=f"{label}.{property_name} is in the allowed value set",
    )


@register_conformance_compiler("uniqueness")
def _compile_uniqueness(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label = _string(config, "label")
    property_name = _string(config, "property")
    node = node_pattern("n", label)
    property_ref = property_access("n", property_name)
    query = dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          MATCH {node}
          WHERE {property_ref} IS NOT NULL
          RETURN count(n) AS population
        }}
        CALL {{
          MATCH {node}
          WHERE {property_ref} IS NOT NULL
          WITH {property_ref} AS value, count(n) AS frequency
          WHERE frequency > 1
          RETURN coalesce(sum(frequency), 0) AS violation_count
        }}
        CALL {{
          MATCH {node}
          WHERE {property_ref} IS NOT NULL
          WITH {property_ref} AS value, collect(n) AS duplicate_nodes
          WHERE size(duplicate_nodes) > 1
          UNWIND duplicate_nodes AS n
          WITH n ORDER BY id(n) LIMIT $evidence_cap
          RETURN collect({_node_pointer("n")}) AS evidence
        }}
        RETURN {_SCHEMA_PROJECTION},
               population, violation_count, evidence
        """
    ).strip()
    params = {
        **_schema_params(labels=[label], relationship_types=[], evidence_cap=evidence_cap),
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"unique": True},
        name=f"{label}.{property_name} is unique",
    )


@register_conformance_compiler("hub_outlier")
def _compile_hub_outlier(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    label = _string(config, "label")
    rel_type = _optional_string(config, "rel_type")
    direction = _direction(config)
    z_threshold = _number(config, "z_threshold", default=3.0, positive=True)
    requested_sample_size = (
        _DEFAULT_HUB_SAMPLE_SIZE
        if config.get("sample_size") is None
        else _integer(config, "sample_size", minimum=1)
    )
    if isinstance(sample_seed, bool) or not isinstance(sample_seed, int) or sample_seed < 0:
        raise _invalid(
            "Hub sample_seed must be a non-negative integer.",
            "Use the C1 sampling policy to derive a deterministic check seed.",
        )
    node = node_pattern("n", label)
    pattern = _relationship_path(direction, relationship_type=rel_type)
    hash_params = cypher_hash_parameters(sample_seed)
    hash_expression = cypher_hash_expression("_gc_sample_input")
    query = dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          MATCH {node}
          RETURN count(n) AS population
        }}
        CALL {{
          MATCH {node}
          WITH n, id(n) % {CYPHER_SAMPLE_MODULUS} AS _gc_sample_input
          WITH n, {hash_expression} AS _gc_sample_key
          ORDER BY _gc_sample_key, id(n)
          LIMIT $sample_size
          OPTIONAL MATCH {pattern}
          WITH n, count(r) AS degree
          WITH collect({{node: n, degree: degree}}) AS sampled_nodes,
               coalesce(avg(toFloat(degree)), 0.0) AS mean_degree,
               coalesce(stDevP(toFloat(degree)), 0.0) AS degree_stddev
          WITH sampled_nodes, mean_degree, degree_stddev,
               [item IN sampled_nodes
                WHERE degree_stddev > 0.0
                  AND toFloat(item.degree) > mean_degree + $z_threshold * degree_stddev]
                 AS violations
          RETURN size(sampled_nodes) AS sample_size,
                 size(violations) AS violation_count,
                 mean_degree,
                 degree_stddev,
                 [item IN violations[0..$evidence_cap] |
                   {{kind: 'node', id: toString(id(item.node)), labels: labels(item.node)}}]
                   AS evidence
        }}
        RETURN {_SCHEMA_PROJECTION},
               population, sample_size, violation_count,
               mean_degree, degree_stddev, evidence
        """
    ).strip()
    params = {
        **_schema_params(
            labels=[label],
            relationship_types=[rel_type] if rel_type is not None else [],
            evidence_cap=evidence_cap,
        ),
        "z_threshold": z_threshold,
        "sample_size": requested_sample_size,
        **hash_params,
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"z_threshold": z_threshold, "sample_size": requested_sample_size},
        name=f"{label} relationship degree stays within {z_threshold:g} standard deviations",
        sampled=True,
        population_query=f"MATCH {node} RETURN count(n) AS population",
        population_params={},
    )


@register_conformance_compiler("label_cooccurrence")
def _compile_label_cooccurrence(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label_a = _string(config, "label_a")
    label_b = _string(config, "label_b")
    label_a_predicate = f"n:{cypher_identifier(label_a)}"
    label_b_predicate = f"n:{cypher_identifier(label_b)}"
    query = _node_predicate_query(
        match=node_pattern("n"),
        population=f"{label_a_predicate} OR {label_b_predicate}",
        violation=f"{label_a_predicate} AND {label_b_predicate}",
    )
    params = {
        **_schema_params(
            labels=[label_a, label_b], relationship_types=[], evidence_cap=evidence_cap
        ),
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"cooccurrences": 0},
        name=f"{label_a} and {label_b} do not co-occur",
    )


@register_conformance_compiler("rel_direction")
def _compile_rel_direction(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    from_label = _string(config, "from_label")
    rel_type = _string(config, "rel_type")
    to_label = _string(config, "to_label")
    from_token = cypher_identifier(from_label)
    to_token = cypher_identifier(to_label)
    population = (
        f"(source:{from_token} AND target:{to_token}) "
        f"OR (source:{to_token} AND target:{from_token})"
    )
    violation = f"source:{to_token} AND target:{from_token}"
    query = _relationship_predicate_query(
        match=f"(source)-{relationship_pattern('r', rel_type)}->(target)",
        population=population,
        violation=violation,
    )
    params = {
        **_schema_params(
            labels=[from_label, to_label],
            relationship_types=[rel_type],
            evidence_cap=evidence_cap,
        ),
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"wrong_direction": 0},
        name=f"{rel_type} points from {from_label} to {to_label}",
    )


@register_conformance_compiler("temporal_sanity")
def _compile_temporal_sanity(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label = _string(config, "label")
    start_property = _string(config, "start_property")
    end_property = _string(config, "end_property")
    start_ref = property_access("n", start_property)
    end_ref = property_access("n", end_property)
    query = _node_predicate_query(
        match=node_pattern("n", label),
        population=f"{start_ref} IS NOT NULL AND {end_ref} IS NOT NULL",
        violation=f"{end_ref} < {start_ref}",
    )
    params = {
        **_schema_params(labels=[label], relationship_types=[], evidence_cap=evidence_cap),
    }
    return ConformancePlan(
        query=query,
        params=params,
        expected={"end_not_before_start": True},
        name=f"{label}.{end_property} is not before {start_property}",
    )
