from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from textwrap import dedent

from graphcheck.contracts.check import (
    CompetencyCheck,
    ConformanceCheck,
    DriftCheck,
    LoadedCheck,
)
from graphcheck.contracts.results import Pattern
from graphcheck.errors import GraphCheckError
from graphcheck.packs.catalog import PackCatalog, builtin_pack_catalog


@dataclass(frozen=True)
class CompiledCheck:
    """A parameterized, read-only query and the assertion evaluated from its rows."""

    check: LoadedCheck
    query: str
    params: dict[str, object]
    expected: dict[str, object]
    name: str
    evidence_cap: int
    sampled: bool = False
    sampling_preflight: bool = True
    population_query: str | None = None
    population_params: dict[str, object] | None = None
    sample_population: int | None = None
    evidence_kinds: tuple[str, ...] = ()
    evidence_id_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConformancePlan:
    """Pack-facing compilation result for one conformance template."""

    query: str
    params: dict[str, object]
    expected: dict[str, object]
    name: str
    sampled: bool = False
    sampling_preflight: bool = True
    population_query: str | None = None
    population_params: dict[str, object] | None = None


ConformanceCompiler = Callable[[dict[str, object], int, int], ConformancePlan]
_CONFORMANCE_COMPILERS: dict[str, ConformanceCompiler] = {}


def register_conformance_compiler(
    name: str,
) -> Callable[[ConformanceCompiler], ConformanceCompiler]:
    """Register a pack template without changing SPEC-02's model-only registry."""

    if not name.strip():
        raise ValueError("a conformance compiler name cannot be blank")

    def decorator(compiler: ConformanceCompiler) -> ConformanceCompiler:
        _CONFORMANCE_COMPILERS[name] = compiler
        return compiler

    return decorator


_SCHEMA_CATALOG = """
CALL {
  CALL db.labels() YIELD label
  RETURN collect(label) AS _gc_labels
}
CALL {
  CALL db.relationshipTypes() YIELD relationshipType
  RETURN collect(relationshipType) AS _gc_rel_types
}
WITH _gc_labels, _gc_rel_types
"""

_SCHEMA_PROJECTION = """
all(name IN $required_labels WHERE name IN _gc_labels)
  AND all(name IN $required_relationship_types WHERE name IN _gc_rel_types) AS schema_ok,
[name IN $required_labels WHERE NOT name IN _gc_labels] AS missing_labels,
[name IN $required_relationship_types WHERE NOT name IN _gc_rel_types]
  AS missing_relationship_types
"""


def _node_pointer(variable: str) -> str:
    # id() works on every supported Neo4j 4.4/5.x server. It is deprecated on 5.x,
    # but elementId() is unavailable on 4.4, so v0 serializes the stable string form.
    return f"{{kind: 'node', id: toString(id({variable})), labels: labels({variable})}}"


def _rel_pointer(variable: str) -> str:
    return f"{{kind: 'rel', id: toString(id({variable})), type: type({variable})}}"


@register_conformance_compiler("completeness")
def _compile_completeness(
    config: dict[str, object], evidence_cap: int, sample_seed: int
) -> ConformancePlan:
    del sample_seed
    label_value = config.get("label")
    property_value = config.get("property")
    if not isinstance(label_value, str) or not label_value.strip():
        raise GraphCheckError(
            "engine.invalid_check",
            "Completeness label must be a non-blank string.",
            "Set `with.label` to the node label this check should inspect.",
        )
    if not isinstance(property_value, str) or not property_value.strip():
        raise GraphCheckError(
            "engine.invalid_check",
            "Completeness property must be a non-blank string.",
            "Set `with.property` to the property this check should inspect.",
        )
    label = label_value.strip()
    property_name = property_value.strip()
    threshold = float(config.get("threshold", 1.0))
    if not 0.0 <= threshold <= 1.0:
        raise GraphCheckError(
            "engine.invalid_check",
            "Completeness threshold must be between 0 and 1.",
            "Set `with.threshold` to a number in the closed interval [0, 1].",
        )

    query = dedent(
        f"""
        {_SCHEMA_CATALOG}
        CALL {{
          MATCH (n)
          WHERE $label IN labels(n)
          RETURN count(n) AS population,
                 sum(CASE WHEN n[$property] IS NOT NULL THEN 1 ELSE 0 END)
                   AS conforming_count,
                 sum(CASE WHEN n[$property] IS NULL THEN 1 ELSE 0 END)
                   AS violation_count
        }}
        CALL {{
          MATCH (n)
          WHERE $label IN labels(n) AND n[$property] IS NULL
          WITH n ORDER BY id(n) LIMIT $evidence_cap
          RETURN collect({_node_pointer("n")}) AS evidence
        }}
        RETURN {_SCHEMA_PROJECTION},
               population,
               conforming_count,
               violation_count,
               CASE WHEN population = 0 THEN 1.0
                    ELSE toFloat(conforming_count) / population END AS coverage,
               evidence
        """
    ).strip()
    return ConformancePlan(
        query=query,
        params={
            "label": label,
            "property": property_name,
            "evidence_cap": evidence_cap,
            "required_labels": [label],
            "required_relationship_types": [],
        },
        expected={"threshold": threshold},
        name=f"{label}.{property_name} is present",
    )


def expected_for(check: LoadedCheck) -> dict[str, object]:
    spec = check.spec
    if isinstance(spec, CompetencyCheck):
        return spec.expect.model_dump(exclude_none=True)
    if isinstance(spec, DriftCheck):
        return {
            "baseline": spec.baseline,
            "tolerance": dict(spec.tolerance),
        }
    if isinstance(spec, ConformanceCheck):
        if spec.check == "completeness":
            return {"threshold": float(spec.with_.get("threshold", 1.0))}
        return dict(spec.with_)
    raise TypeError(f"unsupported loaded check spec: {type(spec).__name__}")


def name_for(check: LoadedCheck) -> str:
    spec = check.spec
    if isinstance(spec, CompetencyCheck):
        return spec.question.rstrip().removesuffix("?")
    if isinstance(spec, DriftCheck):
        target = spec.target.get("label") or spec.target.get("type") or "graph"
        if spec.target.get("property") is not None:
            target = f"{target}.{spec.target['property']}"
        return f"{spec.metric} for {target} is within tolerance"
    if isinstance(spec, ConformanceCheck):
        if spec.check == "completeness":
            return f"{spec.with_.get('label')}.{spec.with_.get('property')} is present"
        return spec.check.replace("_", " ")
    return check.id


class CypherCompiler:
    def __init__(
        self,
        *,
        evidence_cap: int = 100,
        pack_catalog: PackCatalog | None = None,
    ) -> None:
        if isinstance(evidence_cap, bool) or not isinstance(evidence_cap, int) or evidence_cap < 1:
            raise ValueError("evidence_cap must be a positive integer")
        self.evidence_cap = evidence_cap
        self.pack_catalog = pack_catalog or builtin_pack_catalog()

    def compile(self, check: LoadedCheck, *, sample_seed: int = 0) -> CompiledCheck:
        if check.pattern is Pattern.CONFORMANCE:
            return self._compile_conformance(check, sample_seed)
        if check.pattern in (Pattern.COMPETENCY_SHAPE, Pattern.COMPETENCY_REGRESSION):
            return self._compile_competency(check)
        if check.pattern is Pattern.DRIFT:
            return self._compile_drift(check)
        raise GraphCheckError(
            "engine.unsupported_pattern",
            f"Check {check.id!r} uses unsupported pattern {check.pattern!s}.",
            "Use a conformance, competency, or drift check pattern supported by this release.",
        )

    def _compile_conformance(self, check: LoadedCheck, sample_seed: int) -> CompiledCheck:
        spec = check.spec
        if not isinstance(spec, ConformanceCheck):
            raise _invalid_loaded_check(check, "conformance")
        definition = self.pack_catalog.checks.get(spec.check)
        if definition is None:
            raise GraphCheckError(
                "packs.runtime_missing",
                f"Conformance check {spec.check!r} is not declared by installed pack metadata.",
                "Install a pack whose validated YAML declares this check and its runtime template.",
            )
        compiler = _CONFORMANCE_COMPILERS.get(definition.template)
        if compiler is None:
            raise GraphCheckError(
                "engine.compiler_missing",
                f"No compiler is registered for pack template {definition.template!r}.",
                "Install a pack version that provides the check's C1 compiler template.",
            )
        runtime_config = dict(spec.with_)
        if definition.pack == "pii":
            runtime_config["__pack_metadata__"] = self.pack_catalog.pii
        plan = compiler(runtime_config, self.evidence_cap, sample_seed)
        if plan.sampled is not definition.sampled:
            raise GraphCheckError(
                "packs.runtime_mismatch",
                f"Pack check {spec.check!r} declares sampled={definition.sampled!r}, "
                f"but template {definition.template!r} compiled sampled={plan.sampled!r}.",
                "Make the pack YAML sampling declaration match its registered compiler.",
            )
        return CompiledCheck(
            check=check,
            query=plan.query,
            params=dict(plan.params),
            expected=dict(plan.expected),
            name=plan.name,
            evidence_cap=self.evidence_cap,
            sampled=plan.sampled,
            sampling_preflight=plan.sampling_preflight,
            population_query=plan.population_query,
            population_params=(
                dict(plan.population_params) if plan.population_params is not None else None
            ),
            evidence_kinds=definition.evidence_elements,
            evidence_id_fields=definition.evidence_id_fields,
        )

    def missing_capabilities(self, check: LoadedCheck, target: object) -> tuple[str, ...]:
        """Return target capabilities that prevent a pack check from being attempted."""
        if not isinstance(check.spec, ConformanceCheck):
            return ()
        definition = self.pack_catalog.checks.get(check.spec.check)
        if definition is None:
            return ()
        capabilities = getattr(target, "capabilities", None)
        missing: list[str] = []
        for requirement in definition.requires:
            if requirement == "read":
                # A successful C2 target probe establishes the engine's read path. Individual
                # permission gaps still surface as errored checks rather than optimistic skips.
                unavailable = False
            else:
                unavailable = not bool(getattr(capabilities, requirement, False))
            if unavailable:
                missing.append(requirement)
        return tuple(missing)

    def _compile_competency(self, check: LoadedCheck) -> CompiledCheck:
        spec = check.spec
        if not isinstance(spec, CompetencyCheck):
            raise _invalid_loaded_check(check, "competency")
        query = spec.query.strip()
        if not query:
            raise GraphCheckError(
                "engine.empty_query",
                f"Competency check {check.id!r} has an empty query.",
                "Add a read-only Cypher query to the check's `query` field.",
            )
        params = dict(spec.params)
        missing = sorted(_parameter_names(query) - params.keys())
        if missing:
            names = ", ".join(f"${name}" for name in missing)
            raise GraphCheckError(
                "engine.parameter_missing",
                f"Competency check {check.id!r} has no value for {names}.",
                "Declare every Cypher parameter under the check's `params` mapping.",
            )
        return CompiledCheck(
            check=check,
            query=query,
            params=params,
            expected=spec.expect.model_dump(exclude_none=True),
            name=name_for(check),
            evidence_cap=self.evidence_cap,
        )

    def _compile_drift(self, check: LoadedCheck) -> CompiledCheck:
        spec = check.spec
        if not isinstance(spec, DriftCheck):
            raise _invalid_loaded_check(check, "drift")
        compiler = {
            "node_count": self._compile_node_count,
            "relationship_count": self._compile_relationship_count,
            "property_coverage": self._compile_property_coverage,
        }.get(spec.metric)
        if compiler is None:
            raise GraphCheckError(
                "engine.metric_unsupported",
                f"Drift metric {spec.metric!r} has no C1 query compiler.",
                "Use node_count, relationship_count, or property_coverage, "
                "or install its provider.",
            )
        query, params = compiler(spec)
        return CompiledCheck(
            check=check,
            query=query,
            params=params,
            expected=expected_for(check),
            name=name_for(check),
            evidence_cap=self.evidence_cap,
        )

    def _compile_node_count(self, spec: DriftCheck) -> tuple[str, dict[str, object]]:
        unknown = set(spec.target) - {"label"}
        if unknown:
            raise _unknown_target(spec.metric, unknown)
        label = spec.target.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise _bad_target(spec.metric, "target.label must be a non-blank string")
        required_labels = [label] if label is not None else []
        predicate = "WHERE $label IN labels(n)" if label is not None else ""
        query = dedent(
            f"""
            {_SCHEMA_CATALOG}
            CALL {{
              MATCH (n)
              {predicate}
              RETURN count(n) AS current
            }}
            RETURN {_SCHEMA_PROJECTION}, current, current AS population, [] AS evidence
            """
        ).strip()
        return query, {
            **({"label": label} if label is not None else {}),
            "evidence_cap": self.evidence_cap,
            "required_labels": required_labels,
            "required_relationship_types": [],
        }

    def _compile_relationship_count(self, spec: DriftCheck) -> tuple[str, dict[str, object]]:
        unknown = set(spec.target) - {"type"}
        if unknown:
            raise _unknown_target(spec.metric, unknown)
        rel_type = spec.target.get("type")
        if rel_type is not None and (not isinstance(rel_type, str) or not rel_type.strip()):
            raise _bad_target(spec.metric, "target.type must be a non-blank string")
        required_types = [rel_type] if rel_type is not None else []
        predicate = "WHERE type(r) = $relationship_type" if rel_type is not None else ""
        query = dedent(
            f"""
            {_SCHEMA_CATALOG}
            CALL {{
              MATCH ()-[r]->()
              {predicate}
              RETURN count(r) AS current
            }}
            RETURN {_SCHEMA_PROJECTION}, current, current AS population, [] AS evidence
            """
        ).strip()
        return query, {
            **({"relationship_type": rel_type} if rel_type is not None else {}),
            "evidence_cap": self.evidence_cap,
            "required_labels": [],
            "required_relationship_types": required_types,
        }

    def _compile_property_coverage(self, spec: DriftCheck) -> tuple[str, dict[str, object]]:
        unknown = set(spec.target) - {"label", "type", "property"}
        if unknown:
            raise _unknown_target(spec.metric, unknown)
        label = spec.target.get("label")
        rel_type = spec.target.get("type")
        property_name = spec.target.get("property")
        if (label is None) == (rel_type is None):
            raise _bad_target(spec.metric, "target requires exactly one of label or type")
        if not isinstance(property_name, str) or not property_name.strip():
            raise _bad_target(spec.metric, "target.property must be a non-blank string")
        if label is not None:
            if not isinstance(label, str) or not label.strip():
                raise _bad_target(spec.metric, "target.label must be a non-blank string")
            match = "MATCH (element)\n              WHERE $label IN labels(element)"
            missing = (
                "MATCH (element)\n"
                "              WHERE $label IN labels(element) "
                "AND element[$property] IS NULL"
            )
            pointer = _node_pointer("element")
            required_labels = [label]
            required_types: list[object] = []
        else:
            if not isinstance(rel_type, str) or not rel_type.strip():
                raise _bad_target(spec.metric, "target.type must be a non-blank string")
            match = "MATCH ()-[element]->()\n              WHERE type(element) = $relationship_type"
            missing = (
                "MATCH ()-[element]->()\n"
                "              WHERE type(element) = $relationship_type "
                "AND element[$property] IS NULL"
            )
            pointer = _rel_pointer("element")
            required_labels = []
            required_types = [rel_type]
        query = dedent(
            f"""
            {_SCHEMA_CATALOG}
            CALL {{
              {match}
              RETURN count(element) AS population,
                     count(element[$property]) AS present
            }}
            CALL {{
              {missing}
              WITH element ORDER BY id(element) LIMIT $evidence_cap
              RETURN collect({pointer}) AS evidence
            }}
            RETURN {_SCHEMA_PROJECTION}, population, present,
                   CASE WHEN population = 0 THEN 100.0
                        ELSE 100.0 * present / population END AS current,
                   evidence
            """
        ).strip()
        return query, {
            "label": label,
            "relationship_type": rel_type,
            "property": property_name,
            "evidence_cap": self.evidence_cap,
            "required_labels": required_labels,
            "required_relationship_types": required_types,
        }


def compile_check(
    check: LoadedCheck, *, evidence_cap: int = 100, sample_seed: int = 0
) -> CompiledCheck:
    return CypherCompiler(evidence_cap=evidence_cap).compile(check, sample_seed=sample_seed)


def _parameter_names(query: str) -> set[str]:
    """Return Cypher parameter names while ignoring quoted strings and comments."""

    names: set[str] = set()
    index = 0
    length = len(query)
    while index < length:
        char = query[index]
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            while index < length:
                if query[index] == "\\" and quote != "`":
                    index += 2
                    continue
                if query[index] == quote:
                    if index + 1 < length and query[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if query.startswith("//", index):
            newline = query.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if query.startswith("/*", index):
            end = query.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char == "$" and index + 1 < length:
            start = index + 1
            if query[start].isalpha() or query[start] == "_":
                end = start + 1
                while end < length and (query[end].isalnum() or query[end] == "_"):
                    end += 1
                names.add(query[start:end])
                index = end
                continue
        index += 1
    return names


def _invalid_loaded_check(check: LoadedCheck, expected: str) -> GraphCheckError:
    return GraphCheckError(
        "engine.invalid_check",
        f"Check {check.id!r} is marked {expected} but carries {type(check.spec).__name__}.",
        "Reload the suite with the SPEC-02 loader before compiling it.",
    )


def _unknown_target(metric: str, keys: set[str]) -> GraphCheckError:
    rendered = ", ".join(sorted(keys))
    return GraphCheckError(
        "engine.invalid_target",
        f"Drift metric {metric!r} does not accept target keys: {rendered}.",
        "Remove the unknown target keys or use the metric that defines them.",
    )


def _bad_target(metric: str, detail: str) -> GraphCheckError:
    return GraphCheckError(
        "engine.invalid_target",
        f"Invalid target for drift metric {metric!r}: {detail}.",
        "Fix the drift check's `target` mapping and run it again.",
    )


def _register_builtin_pack_compilers() -> None:
    # The pack registry remains model-only. Importing the C1 bridge installs compiler
    # callbacks for the template names carried by C3's data-only core pack.
    from graphcheck.engine import core_pack as loaded_core_pack
    from graphcheck.engine import pii_pack as loaded_pii_pack

    del loaded_core_pack, loaded_pii_pack


_register_builtin_pack_compilers()
