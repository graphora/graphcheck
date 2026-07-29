from dataclasses import dataclass, replace

import pytest
import yaml
from pydantic import ValidationError

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import (
    Capabilities,
    RunStatus,
    RunTarget,
    SkipReason,
    Verdict,
)
from graphcheck.engine.compiler import CypherCompiler, _parameter_names
from graphcheck.engine.evaluator import _luhn_valid, _verhoeff_valid, evaluate_check
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.engine.sampling import SamplingPolicy
from graphcheck.errors import GraphCheckError
from graphcheck.packs import REGISTRY
from graphcheck.packs.catalog import PackCatalog, builtin_pack_catalog

TARGET = RunTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:pii-graph",
    capabilities=Capabilities(apoc=False, count_store=True),
)


@dataclass(frozen=True)
class RichResult:
    rows: list[dict[str, object]]
    columns: tuple[str, ...]


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run_read_result(self, query, params, *, timeout_s=None):
        self.calls.append((query, dict(params), timeout_s))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _compiled(check: str, with_: dict[str, object] | None = None, *, evidence_cap: int = 3):
    suite = load_suite(
        yaml.safe_dump(
            {
                "suite": "pii",
                "conformance": [{"id": "scan", "check": check, "with": with_ or {}}],
            },
            sort_keys=False,
        )
    )
    return CypherCompiler(evidence_cap=evidence_cap).compile(suite.checks[0], sample_seed=17)


def _pointer(identifier: str, *labels: str) -> dict[str, object]:
    return {"kind": "node", "id": identifier, "labels": list(labels)}


def _summary(
    *,
    population: int,
    candidates: list[dict[str, object]],
    schema_ok: bool = True,
    missing_labels: list[str] | None = None,
    missing_properties: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_ok": schema_ok,
        "missing_labels": missing_labels or [],
        "missing_relationship_types": [],
        "missing_properties": missing_properties or [],
        "population": population,
        "sample_size": len(candidates),
        "candidates": candidates,
    }


def test_executable_catalog_and_public_loader_cover_every_registered_check():
    catalog = builtin_pack_catalog()

    assert set(catalog.checks) == set(REGISTRY)
    assert catalog.pii is not None
    assert catalog.checks["pii_name_match"].template == "pii_name_match"
    assert catalog.checks["pii_value_match"].sampled is True


@pytest.mark.parametrize("check", ["pii_name_match", "pii_value_match"])
def test_pii_checks_compile_from_public_suite_yaml_as_parameterized_sampled_queries(check):
    compiled = _compiled(check)

    assert compiled.check.spec.check == check
    assert compiled.sampled is True
    assert compiled.sampling_preflight is False
    assert compiled.population_query is None
    assert _parameter_names(compiled.query) == compiled.params.keys()
    assert "sample_size" in compiled.params
    for name in ("sample_hash_a", "sample_hash_b", "sample_hash_c", "sample_hash_d"):
        assert name in compiled.params
        assert name.replace("sample_hash_", "sample_gate_hash_") in compiled.params
    assert "sample_population" not in compiled.params
    assert "completeness_notice" in compiled.expected
    assert compiled.evidence_kinds == ("node",)
    assert compiled.evidence_id_fields == ("node_id",)
    if check == "pii_value_match":
        assert "toStringOrNull(raw) = raw" in compiled.query
        assert "toString(raw)" not in compiled.query


@pytest.mark.parametrize("check", ["pii_name_match", "pii_value_match"])
def test_pii_query_sampling_order_is_seeded_and_deterministic(check):
    compiler = CypherCompiler()
    loaded = load_suite(
        yaml.safe_dump(
            {
                "suite": "pii",
                "conformance": [{"id": "scan", "check": check, "with": {}}],
            },
            sort_keys=False,
        )
    ).checks[0]

    first = compiler.compile(loaded, sample_seed=123456789)
    repeated = compiler.compile(loaded, sample_seed=123456789)
    changed = compiler.compile(loaded, sample_seed=987654321)

    assert first.query == repeated.query == changed.query
    assert first.params == repeated.params
    hash_names = ("sample_hash_a", "sample_hash_b", "sample_hash_c", "sample_hash_d")
    assert tuple(first.params[name] for name in hash_names) != tuple(
        changed.params[name] for name in hash_names
    )
    assert "WITH population, n, property, raw ORDER BY id(n), property" in first.query
    assert "_gc_node_properties[_gc_property_index] AS occurrence" in first.query
    assert "+ _gc_property_index) % 2147483647" in first.query
    assert "$sample_hash_a * (_gc_occurrence_key)" in first.query
    assert "$sample_gate_hash_a * (_gc_occurrence_key)" in first.query
    assert "WITH population, n, property, raw, _gc_occurrence_key" in first.query
    assert "$sample_population AS population" not in first.query


@pytest.mark.parametrize("check", ["pii_name_match", "pii_value_match"])
def test_label_scoped_pii_uses_a_native_label_and_no_obsolete_label_parameter(check):
    compiled = _compiled(check, {"label": "Customer`Archive"})

    assert "MATCH (n:`Customer``Archive`)" in compiled.query
    assert "$label" not in compiled.query
    assert "label" not in compiled.params
    assert compiled.params["required_labels"] == ["Customer`Archive"]


def test_configured_pii_properties_use_native_access_while_names_remain_values():
    compiled = _compiled(
        "pii_value_match",
        {"properties": ["email address", "tax`id"]},
    )

    assert "n.`email address`" in compiled.query
    assert "n.`tax``id`" in compiled.query
    assert "n[property]" not in compiled.query
    assert compiled.params["properties"] == ["email address", "tax`id"]


def test_unknown_pii_pattern_fails_loudly_at_compilation():
    with pytest.raises(ValidationError):
        _compiled("pii_value_match", {"patterns": ["not-installed"]})


def test_name_match_emits_findings_evidence_and_estimate_without_property_values():
    compiled = replace(
        _compiled("pii_name_match", {"patterns": ["email"]}),
        sample_population=100,
    )
    rows = [
        _summary(
            population=100,
            candidates=[
                {"evidence": _pointer("n-1", "Customer"), "property": "Email"},
                {"evidence": _pointer("n-2", "Lead"), "property": "email_address"},
            ],
        )
    ]

    evaluation = evaluate_check(compiled, rows)

    assert evaluation.passed is False
    assert evaluation.measured["matches"] == 2
    assert evaluation.measured["confidence"] == "name-match"
    assert len(evaluation.measured["findings"]) == 2
    assert evaluation.estimate is not False
    assert evaluation.estimate.sample_size == 2
    assert evaluation.estimate.population == 100
    assert evaluation.estimate.confidence == pytest.approx(0.95)
    assert evaluation.estimate.ci is not None
    assert {item.id for item in evaluation.evidence.elements} == {"n-1", "n-2"}


def test_value_match_applies_regex_and_luhn_without_leaking_values():
    compiled = replace(
        _compiled("pii_value_match", {"patterns": ["credit_card"]}),
        sample_population=100,
    )
    secret = "4111 1111 1111 1111"
    rows = [
        _summary(
            population=100,
            candidates=[
                {
                    "evidence": _pointer("n-1", "Payment"),
                    "property": "notes",
                    "value": secret,
                },
                {
                    "evidence": _pointer("n-2", "Payment"),
                    "property": "notes",
                    "value": "4111 1111 1111 1112",
                },
            ],
        )
    ]

    evaluation = evaluate_check(compiled, rows)

    assert evaluation.passed is False
    assert evaluation.measured["matches"] == 1
    assert evaluation.measured["findings"] == [
        {
            "pattern": "credit_card",
            "location": {"labels": ["Payment"], "property": "notes"},
            "exposure_count": 50,
            "confidence": "value-match",
        }
    ]
    assert secret not in repr(evaluation.measured)
    assert secret not in repr(evaluation.evidence)
    assert [item.id for item in evaluation.evidence.elements] == ["n-1"]


def test_pii_checksum_helpers_accept_known_valid_and_reject_invalid_values():
    assert _luhn_valid("4111-1111-1111-1111")
    assert not _luhn_valid("4111-1111-1111-1112")
    assert _verhoeff_valid("2363")
    assert not _verhoeff_valid("2364")


def test_empty_pii_population_is_an_exact_pass():
    compiled = replace(_compiled("pii_value_match"), sample_population=0)

    evaluation = evaluate_check(compiled, [_summary(population=0, candidates=[])])

    assert evaluation.passed is True
    assert evaluation.estimate is False
    assert evaluation.measured["findings"] == []


def test_sampled_value_match_pass_still_carries_estimate_and_completeness_notice():
    compiled = replace(
        _compiled("pii_value_match", {"patterns": ["credit_card"]}),
        sample_population=100,
    )
    evaluation = evaluate_check(
        compiled,
        [
            _summary(
                population=100,
                candidates=[
                    {
                        "evidence": _pointer("n-1", "Payment"),
                        "property": "notes",
                        "value": "not a card",
                    },
                    {
                        "evidence": _pointer("n-2", "Payment"),
                        "property": "notes",
                        "value": "still not a card",
                    },
                ],
            )
        ],
    )

    assert evaluation.passed is True
    assert evaluation.estimate is not False
    assert evaluation.estimate.sample_size == 2
    assert evaluation.estimate.population == 100
    assert evaluation.estimate.ci is not None
    assert "never claims complete PII discovery" in evaluation.measured["completeness_notice"]


def test_malformed_pii_candidate_errors_instead_of_passing():
    compiled = replace(_compiled("pii_name_match"), sample_population=1)

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(
            compiled,
            [_summary(population=1, candidates=[{"property": "email"}])],
        )

    assert caught.value.error.code == "engine.invalid_query_result"


def test_name_match_samples_nonmatching_properties_without_false_findings():
    compiled = replace(_compiled("pii_name_match"), sample_population=100)

    evaluation = evaluate_check(
        compiled,
        [
            _summary(
                population=100,
                candidates=[
                    {
                        "evidence": _pointer("n-1", "Customer"),
                        "property": "not_personal_data",
                    }
                ],
            )
        ],
    )

    assert evaluation.passed is True
    assert evaluation.measured["matches"] == 0
    assert evaluation.estimate is not False
    assert evaluation.estimate.sample_size == 1
    assert evaluation.estimate.population == 100


def test_engine_runs_pii_sampling_end_to_end_and_emits_estimate_metadata():
    client = Client(
        [
            RichResult(
                [
                    _summary(
                        population=100,
                        candidates=[
                            {
                                "evidence": _pointer("n-1", "Customer"),
                                "property": "email",
                            },
                            {
                                "evidence": _pointer("n-2", "Customer"),
                                "property": "email_address",
                            },
                        ],
                    )
                ],
                ("schema_ok", "population", "sample_size", "candidates"),
            ),
        ]
    )
    config = EngineConfig(
        sampling=SamplingPolicy(exhaustive_limit=10, sample_size=2, seed="pii-seed")
    )
    suite = """suite: pii
conformance:
  - id: names
    check: pii_name_match
    with: {patterns: [email]}
"""

    results = Engine(client, config=config).run_yaml(suite, target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.FAIL
    assert check.estimate is not False
    assert check.estimate.sample_size == 2
    assert check.estimate.ci is not None
    assert "sample_population" not in check.params
    assert len(client.calls) == 1


def test_engine_uses_the_pii_query_population_without_a_separate_preflight():
    candidates = [
        {"evidence": _pointer(f"n-{index}", "Customer"), "property": "email"} for index in range(5)
    ]
    client = Client(
        [
            RichResult(
                [_summary(population=10, candidates=candidates)],
                ("schema_ok", "population", "sample_size", "candidates"),
            ),
        ]
    )
    suite = """suite: pii
conformance:
  - id: names
    check: pii_name_match
    with: {patterns: [email]}
"""

    results = Engine(client).run_yaml(suite, target=TARGET)

    assert results.checks[0].verdict is Verdict.FAIL
    assert results.checks[0].estimate.population == 10
    assert results.checks[0].estimate.sample_size == 5
    assert len(client.calls) == 1


def test_missing_pii_label_and_query_timeout_are_errored_not_passed():
    missing_label_client = Client(
        [
            RichResult(
                [
                    _summary(
                        population=0,
                        candidates=[],
                        schema_ok=False,
                        missing_labels=["TypoLabel"],
                    )
                ],
                ("schema_ok", "population", "sample_size", "candidates"),
            ),
        ]
    )
    suite = """suite: pii
conformance:
  - id: values
    check: pii_value_match
    with: {label: TypoLabel}
"""

    missing = Engine(missing_label_client).run_yaml(suite, target=TARGET)
    timeout = Engine(
        Client([GraphCheckError("engine.timeout", "query timed out", "reduce sample")])
    ).run_yaml(suite, target=TARGET)

    assert missing.checks[0].verdict is Verdict.ERRORED
    assert missing.checks[0].error.code == "engine.schema_reference_missing"
    assert timeout.checks[0].verdict is Verdict.ERRORED
    assert timeout.checks[0].error.code == "engine.timeout"


def test_broken_pii_query_is_errored_not_passed():
    client = Client(
        [
            GraphCheckError("neo4j.query_failed", "broken PII query", "fix the query"),
        ]
    )
    suite = """suite: pii
conformance:
  - id: values
    check: pii_value_match
    with: {patterns: [email]}
"""

    results = Engine(client).run_yaml(suite, target=TARGET)

    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "neo4j.query_failed"


def test_explicit_missing_pii_property_is_errored_not_an_empty_pass():
    compiled = replace(
        _compiled("pii_value_match", {"properties": ["customer_emali"]}),
        sample_population=0,
    )

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(
            compiled,
            [
                _summary(
                    population=0,
                    candidates=[],
                    schema_ok=False,
                    missing_properties=["customer_emali"],
                )
            ],
        )

    assert caught.value.error.code == "engine.schema_reference_missing"
    assert "customer_emali" in caught.value.error.message


def test_pack_capability_gap_is_explicit_unsupported_partial_skip():
    installed = builtin_pack_catalog()
    checks = dict(installed.checks)
    checks["completeness"] = replace(
        checks["completeness"],
        requires=("read", "apoc"),
    )
    compiler = CypherCompiler(pack_catalog=PackCatalog(checks=checks, pii=installed.pii))
    client = Client([])
    suite = """suite: capability
conformance:
  - id: names
    check: completeness
    with: {label: Customer, property: name}
"""

    results = Engine(client, compiler=compiler).run_yaml(suite, target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.SKIPPED
    assert check.skip_reason is SkipReason.UNSUPPORTED
    assert results.run.status is RunStatus.PARTIAL
    assert "requires missing capability: apoc" in results.run.partial_reason
    assert client.calls == []


@pytest.mark.parametrize(
    ("definition_update", "error_code"),
    [
        ({"template": "not_installed"}, "engine.compiler_missing"),
        ({"sampled": True}, "packs.runtime_mismatch"),
    ],
)
def test_manifest_runtime_binding_errors_fail_loudly(definition_update, error_code):
    installed = builtin_pack_catalog()
    checks = dict(installed.checks)
    checks["completeness"] = replace(checks["completeness"], **definition_update)
    compiler = CypherCompiler(pack_catalog=PackCatalog(checks=checks, pii=installed.pii))
    suite = load_suite(
        """suite: runtime-binding
conformance:
  - id: complete
    check: completeness
    with: {label: Customer, property: name}
"""
    )

    with pytest.raises(GraphCheckError) as caught:
        compiler.compile(suite.checks[0])

    assert caught.value.error.code == error_code


def test_registry_model_without_manifest_definition_fails_loudly():
    installed = builtin_pack_catalog()
    checks = dict(installed.checks)
    del checks["completeness"]
    compiler = CypherCompiler(pack_catalog=PackCatalog(checks=checks, pii=installed.pii))
    suite = load_suite(
        """suite: runtime-binding
conformance:
  - id: complete
    check: completeness
    with: {label: Customer, property: name}
"""
    )

    with pytest.raises(GraphCheckError) as caught:
        compiler.compile(suite.checks[0])

    assert caught.value.error.code == "packs.runtime_missing"
