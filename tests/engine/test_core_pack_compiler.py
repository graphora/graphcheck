import re

import pytest
import yaml

from graphcheck.contracts.check import ConformanceCheck, LoadedCheck, load_suite
from graphcheck.contracts.results import Pattern, Severity
from graphcheck.engine.compiler import CypherCompiler, _parameter_names
from graphcheck.errors import GraphCheckError
from graphcheck.packs.metadata import CORE_CHECK_NAMES


def _loaded(name: str, config: dict[str, object]) -> LoadedCheck:
    spec = ConformanceCheck(id=f"test-{name}", check=name, **{"with": config})
    return LoadedCheck(
        id=spec.id,
        pattern=Pattern.CONFORMANCE,
        severity=Severity.ERROR,
        tags=[],
        generated=False,
        spec=spec,
    )


CASES = [
    (
        "completeness",
        {"label": "GcComplete", "property": "gc_required_property", "threshold": 0.95},
        "node",
    ),
    (
        "cardinality",
        {
            "from_label": "GcCardinalitySource",
            "rel_type": "GC_CARDINALITY_REL",
            "to_label": "GcCardinalityTarget",
            "direction": "out",
            "exactly": 2,
        },
        "node",
    ),
    (
        "no_orphans",
        {"label": "GcOrphan", "rel_type": "GC_ORPHAN_REL", "direction": "any"},
        "node",
    ),
    (
        "property_type",
        {"label": "GcTyped", "property": "gc_typed_property", "type": "integer"},
        "node",
    ),
    (
        "property_format",
        {"label": "GcFormatted", "property": "gc_formatted_property", "regex": "^GC-[0-9]+$"},
        "node",
    ),
    (
        "value_in_set",
        {
            "label": "GcFiniteSet",
            "property": "gc_status_property",
            "values": ["GcActive", "GcClosed"],
        },
        "node",
    ),
    (
        "uniqueness",
        {"label": "GcUnique", "property": "gc_unique_property"},
        "node",
    ),
    (
        "hub_outlier",
        {
            "label": "GcHub",
            "rel_type": "GC_HUB_REL",
            "direction": "in",
            "z_threshold": 2.75,
            "sample_size": 123,
        },
        "node",
    ),
    (
        "label_cooccurrence",
        {"label_a": "GcExclusiveA", "label_b": "GcExclusiveB"},
        "node",
    ),
    (
        "rel_direction",
        {
            "from_label": "GcDirectionSource",
            "rel_type": "GC_DIRECTION_REL",
            "to_label": "GcDirectionTarget",
        },
        "rel",
    ),
    (
        "temporal_sanity",
        {
            "label": "GcTemporal",
            "start_property": "gc_start_property",
            "end_property": "gc_end_property",
        },
        "node",
    ),
]


def test_public_compiler_cases_cover_every_registered_observable_core_check():
    assert {name for name, _config, _kind in CASES} == set(CORE_CHECK_NAMES) - {"dangling_rels"}


@pytest.mark.parametrize(("name", "config", "evidence_kind"), CASES)
def test_public_suite_loader_reaches_each_observable_core_compiler(name, config, evidence_kind):
    suite = load_suite(
        yaml.safe_dump(
            {
                "suite": "core-loader",
                "conformance": [
                    {"id": f"loaded-{name}", "check": name, "with": config},
                ],
            }
        )
    )

    compiled = CypherCompiler().compile(suite.checks[0], sample_seed=91)

    assert compiled.check.spec.check == name
    assert f"kind: '{evidence_kind}'" in compiled.query
    assert _parameter_names(compiled.query) == compiled.params.keys()


def test_dangling_relationship_check_fails_closed_instead_of_optimistically_passing():
    suite = load_suite(
        yaml.safe_dump(
            {
                "suite": "core-loader",
                "conformance": [
                    {
                        "id": "loaded-dangling-rels",
                        "check": "dangling_rels",
                        "with": {"rel_type": "OWNS"},
                    },
                ],
            }
        )
    )

    with pytest.raises(GraphCheckError) as caught:
        CypherCompiler().compile(suite.checks[0])

    assert caught.value.error.code == "engine.check_unobservable"


@pytest.mark.parametrize(("name", "config", "evidence_kind"), CASES)
def test_core_compilers_are_read_only_parameterized_one_row_plans(name, config, evidence_kind):
    compiled = CypherCompiler(evidence_cap=7).compile(_loaded(name, config), sample_seed=91)

    assert not re.search(
        r"\b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b",
        compiled.query,
        flags=re.IGNORECASE,
    )
    for field in (
        "schema_ok",
        "missing_labels",
        "missing_relationship_types",
        "population",
        "violation_count",
        "evidence",
    ):
        assert field in compiled.query
    assert "$evidence_cap" in compiled.query
    assert compiled.params["evidence_cap"] == 7
    assert f"kind: '{evidence_kind}'" in compiled.query
    assert _parameter_names(compiled.query) == compiled.params.keys()

    # Identifiers, regexes, enum values and pinned values stay in params. The property-type
    # query contains every supported type branch, so equality across two compilations below
    # proves that selecting a type does not interpolate it.
    for key, value in config.items():
        if key in {"direction", "type"}:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str):
                assert item not in compiled.query
    assert compiled.expected
    assert compiled.name


def test_property_type_selection_is_a_parameter_not_a_query_fragment():
    base = {"label": "GcTyped", "property": "gc_typed_property"}

    integer = CypherCompiler().compile(_loaded("property_type", {**base, "type": "integer"}))
    string = CypherCompiler().compile(_loaded("property_type", {**base, "type": "string"}))

    assert integer.query == string.query
    assert integer.params["expected_type"] == "integer"
    assert string.params["expected_type"] == "string"


@pytest.mark.parametrize(
    ("name", "base", "direction", "fragment"),
    [
        (
            "cardinality",
            {"from_label": "From", "rel_type": "REL", "to_label": "To", "exactly": 1},
            "out",
            "(n)-[r]->(other)",
        ),
        (
            "cardinality",
            {"from_label": "From", "rel_type": "REL", "to_label": "To", "exactly": 1},
            "in",
            "(n)<-[r]-(other)",
        ),
        (
            "no_orphans",
            {"label": "Node", "rel_type": "REL"},
            "any",
            "(n)-[r]-(other)",
        ),
        (
            "hub_outlier",
            {"label": "Node", "rel_type": "REL", "z_threshold": 3.0, "sample_size": 10},
            "in",
            "(n)<-[r]-(other)",
        ),
    ],
)
def test_validated_direction_selects_only_a_fixed_pattern(name, base, direction, fragment):
    compiled = CypherCompiler().compile(_loaded(name, {**base, "direction": direction}))

    assert fragment in compiled.query
    assert "direction" not in compiled.params


def test_hub_sampling_is_seeded_deterministic_and_reports_actual_sample_size():
    config = {
        "label": "GcHub",
        "rel_type": "GC_HUB_REL",
        "direction": "any",
        "z_threshold": 3.0,
        "sample_size": 1000,
    }
    compiler = CypherCompiler(evidence_cap=11)

    first = compiler.compile(_loaded("hub_outlier", config), sample_seed=123456789)
    repeated = compiler.compile(_loaded("hub_outlier", config), sample_seed=123456789)
    changed = compiler.compile(_loaded("hub_outlier", config), sample_seed=987654321)

    assert first.sampled is True
    assert first.query == repeated.query == changed.query
    assert first.params == repeated.params
    assert first.params["sample_seed"] != changed.params["sample_seed"]
    assert first.params["sample_size"] == 1000
    assert "sample_size" in first.query
    assert "ORDER BY _gc_sample_key, id(n)" in first.query


def test_compiler_applies_pack_defaults_when_optional_values_are_normalized_to_none():
    cardinality = CypherCompiler().compile(
        _loaded(
            "cardinality",
            {"from_label": "From", "rel_type": "REL", "to_label": "To", "exactly": 1},
        )
    )
    hub = CypherCompiler().compile(
        _loaded(
            "hub_outlier",
            {
                "label": "Hub",
                "rel_type": None,
                "direction": "any",
                "z_threshold": 3.0,
                "sample_size": None,
            },
        )
    )

    assert "(n)-[r]->(other)" in cardinality.query
    assert hub.params["sample_size"] == 1000


def test_property_type_query_avoids_neo4j_5_only_type_introspection():
    compiled = CypherCompiler().compile(
        _loaded(
            "property_type",
            {"label": "GcTyped", "property": "gc_typed_property", "type": "datetime"},
        )
    )

    assert "valueType(" not in compiled.query
    assert "elementId(" not in compiled.query
    assert " IS :: " not in compiled.query
