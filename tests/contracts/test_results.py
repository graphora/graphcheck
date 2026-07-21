import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.results import (
    SCHEMA_VERSION,
    WEIGHTS,
    CheckError,
    CheckResult,
    Estimate,
    Evidence,
    EvidenceElement,
    Pattern,
    RedactionPolicy,
    Results,
    RunStatus,
    Score,
    Severity,
    SkipReason,
    Totals,
    Verdict,
    exit_code,
    score_value,
    totals,
)
from graphcheck.contracts.schemas import SPECS_DIR, results_schema


def test_weights_are_severity_keyed():
    assert WEIGHTS[Severity.ERROR] == 3
    assert WEIGHTS[Severity.WARN] == 1


def test_verdict_values():
    assert {v.value for v in Verdict} == {"pass", "fail", "warn", "errored", "skipped"}


def test_evidence_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        Evidence(message="x", elements=[], truncated=False, cap=50, total_count=0, bogus=1)


def test_check_error_shape():
    err = CheckError(code="c", message="m", fix="f")
    assert err.fix == "f"


def _full_check(verdict, severity):
    """A complete check-result dict (every frozen key present) for the given verdict/severity."""
    data = dict(
        id="c1",
        suite_id="s",
        pattern=Pattern.CONFORMANCE,
        name="n",
        provenance=None,
        severity=severity,
        verdict=verdict,
        skip_reason=None,
        started_at=None,
        duration_ms=None,
        compiled_query=None,
        params=None,
        measured=None,
        expected={},
        estimate=False,
        evidence=None,
        error=None,
    )
    if verdict is not Verdict.SKIPPED:
        data.update(started_at="t", duration_ms=5)
    if verdict in (Verdict.PASS, Verdict.FAIL, Verdict.WARN):
        data.update(compiled_query="RETURN 1", params={}, measured={})
    if verdict in (Verdict.FAIL, Verdict.WARN):
        data["evidence"] = Evidence(
            message="m",
            elements=[{"kind": "node", "id": "node-1", "labels": ["Customer"], "type": None}],
            truncated=False,
            cap=50,
            total_count=1,
        )
    if verdict is Verdict.ERRORED:
        data["error"] = CheckError(code="c", message="m", fix="f")
    if verdict is Verdict.SKIPPED:
        data["skip_reason"] = SkipReason.GENERATED
    return data


def _default_severity(verdict):
    # fail must be error-severity, warn must be warn-severity (SPEC-01 rule 1).
    return Severity.WARN if verdict is Verdict.WARN else Severity.ERROR


def _score(value, weights=None):
    return Score(
        value=value,
        method="weighted-by-severity",
        weights=weights if weights is not None else {"error": 3, "warn": 1},
    )


def _base(**over):
    """A valid record for the given verdict; override any field to make it invalid."""
    verdict = over.get("verdict", Verdict.PASS)
    severity = over.get("severity", _default_severity(verdict))
    data = _full_check(verdict, severity)
    data.update(over)
    return data


def test_valid_record_for_each_verdict():
    for v in Verdict:
        CheckResult(**_base(verdict=v))  # must not raise


def test_fail_requires_evidence():
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.FAIL, evidence=None))


def test_evidence_requires_at_least_one_pointer():
    with pytest.raises(ValidationError):
        Evidence(message="m", elements=[], truncated=False, cap=50, total_count=0)


def test_aggregate_measurement_pointer_is_in_typed_and_json_schema_contracts():
    pointer = EvidenceElement(kind="aggregate", id="node_count:label=Customer")

    jsonschema.validate(
        pointer.model_dump(exclude_none=False),
        results_schema()["$defs"]["EvidenceElement"],
    )


def test_pass_forbids_evidence():
    ev = Evidence(
        message="m",
        elements=[{"kind": "node", "id": "node-1", "labels": ["Customer"], "type": None}],
        truncated=False,
        cap=50,
        total_count=1,
    )
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.PASS, evidence=ev))


def test_pass_requires_execution_fields():
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.PASS, measured=None))


def test_errored_requires_error_forbids_measured_and_is_executed():
    assert CheckResult(**_base(verdict=Verdict.ERRORED)).executed is True
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, error=None))
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, measured={"rows": 1}))


def test_attempted_check_requires_timing():
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, started_at=None))


def test_skipped_requires_skip_reason_and_null_execution_fields():
    assert CheckResult(**_base(verdict=Verdict.SKIPPED)).executed is False
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.SKIPPED, skip_reason=None))
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.SKIPPED, duration_ms=5))


def test_errored_and_skipped_forbid_estimate_object():
    est = Estimate(sample_size=10, population=100, confidence=0.95)
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, estimate=est))
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.SKIPPED, estimate=est))


def test_severity_verdict_mismatch_rejected():
    with pytest.raises(ValidationError):
        CheckResult(
            **_base(verdict=Verdict.WARN, severity=Severity.ERROR)
        )  # warn needs warn-severity
    with pytest.raises(ValidationError):
        CheckResult(
            **_base(verdict=Verdict.FAIL, severity=Severity.WARN)
        )  # fail needs error-severity


def test_check_result_requires_all_frozen_keys_present():
    for key in ("provenance", "skip_reason", "estimate", "evidence", "error"):
        data = _base(verdict=Verdict.PASS)
        data.pop(key)
        with pytest.raises(ValidationError):
            CheckResult(**data)


def test_totals_rejects_passed_field_name():
    ok = {"checks": 0, "pass": 0, "fail": 0, "warn": 0, "errored": 0, "skipped": 0}
    Totals.model_validate(ok)  # alias `pass` accepted
    renamed = {k: v for k, v in ok.items() if k != "pass"} | {"passed": 0}
    with pytest.raises(ValidationError):
        Totals.model_validate(renamed)


def _chk(verdict, severity=None, **over):
    data = _full_check(verdict, severity or _default_severity(verdict))
    data["id"] = "x"
    data.update(over)
    return CheckResult(**data)


def test_score_matches_design_example():
    checks = [_chk(Verdict.FAIL), _chk(Verdict.PASS), _chk(Verdict.WARN, Severity.WARN)]
    assert score_value(checks) == 43  # 100 * 3 / (3+3+1)


def test_score_null_on_empty_denominator():
    assert score_value([_chk(Verdict.SKIPPED)]) is None
    assert score_value([]) is None


def test_totals_tally():
    checks = [_chk(Verdict.PASS), _chk(Verdict.FAIL), _chk(Verdict.SKIPPED)]
    assert totals(checks) == {
        "checks": 3,
        "pass": 1,
        "fail": 1,
        "warn": 0,
        "errored": 0,
        "skipped": 1,
    }


def test_exit_code_precedence():
    assert exit_code(RunStatus.FAILED, []) == 3
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.FAIL)]) == 1
    assert exit_code(RunStatus.PARTIAL, [_chk(Verdict.FAIL)]) == 1  # fail dominates partial
    assert exit_code(RunStatus.PARTIAL, [_chk(Verdict.PASS)]) == 2  # clean partial
    assert (
        exit_code(RunStatus.COMPLETE, [_chk(Verdict.ERRORED, Severity.ERROR)]) == 1
    )  # error-errored
    assert (
        exit_code(RunStatus.COMPLETE, [_chk(Verdict.ERRORED, Severity.WARN)]) == 2
    )  # warn-errored
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.SKIPPED)]) == 2  # nothing evaluated
    assert exit_code(RunStatus.COMPLETE, []) == 2  # empty selection
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.WARN, Severity.WARN)]) == 2
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.PASS)]) == 0


def _run(**over):
    data = dict(
        id="r",
        started_at="t",
        finished_at="t",
        graphcheck_version="0.1.0",
        pack_version="0.1.0",
        status=RunStatus.COMPLETE,
        partial_reason=None,
        exit_code=0,
        selection={"suites": [], "tags": [], "fail_fast": False},
        redaction={"policy": RedactionPolicy.NONE, "applied": False},
        target={
            "database": "neo4j",
            "server_version": "5",
            "edition": "community",
            "fingerprint": "sha256:x",
            "capabilities": {"apoc": True, "count_store": True},
        },
        error=None,
    )
    data.update(over)
    return data


def _results(checks, status=RunStatus.COMPLETE, **run_over):
    sc = score_value(checks)
    return Results(
        schema_version=SCHEMA_VERSION,
        run=_run(status=status, exit_code=exit_code(status, checks), **run_over),
        score=None if sc is None else _score(sc),
        totals=totals(checks),
        suites=[{"id": "s", "source_sha": "x", "score": sc, "totals": totals(checks)}],
        checks=checks,
    )


def test_consistent_results_validate():
    _results([_chk(Verdict.PASS)])


def test_wrong_totals_rejected():
    with pytest.raises(ValidationError):
        Results(
            schema_version=SCHEMA_VERSION,
            run=_run(exit_code=0),
            score=_score(100),
            totals={"checks": 9, "pass": 9, "fail": 0, "warn": 0, "errored": 0, "skipped": 0},
            suites=[],
            checks=[_chk(Verdict.PASS)],
        )


def test_partial_reason_iff_partial():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.PASS)], partial_reason="stale")  # complete + reason
    with pytest.raises(ValidationError):
        _results(
            [_chk(Verdict.SKIPPED, skip_reason=SkipReason.NOT_RUN)], status=RunStatus.PARTIAL
        )  # partial needs partial_reason (None here)


def test_unsupported_skip_forces_partial():
    with pytest.raises(ValidationError):
        _results(
            [_chk(Verdict.SKIPPED, skip_reason=SkipReason.UNSUPPORTED)]
        )  # complete, should be partial


def test_generated_only_scores_null_and_exits_2():
    r = _results([_chk(Verdict.SKIPPED, skip_reason=SkipReason.GENERATED)])
    assert r.score is None
    assert r.run.exit_code == 2


def test_complete_requires_target():
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(
            schema_version=SCHEMA_VERSION,
            run=_run(target=None, exit_code=0),
            score=_score(100),
            totals=totals(checks),
            suites=[{"id": "s", "source_sha": "x", "score": 100, "totals": totals(checks)}],
            checks=checks,
        )


def test_orphan_suite_id_rejected():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.PASS, suite_id="other")])  # suites[] only has "s"


def test_per_suite_totals_checked():
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(
            schema_version=SCHEMA_VERSION,
            run=_run(exit_code=0),
            score=_score(100),
            totals=totals(checks),
            suites=[
                {
                    "id": "s",
                    "source_sha": "x",
                    "score": 100,
                    "totals": {
                        "checks": 5,
                        "pass": 5,
                        "fail": 0,
                        "warn": 0,
                        "errored": 0,
                        "skipped": 0,
                    },
                }
            ],
            checks=checks,
        )


def test_bogus_score_weights_rejected():
    with pytest.raises(ValidationError):
        _score(50, {"error": 1, "warn": 1})


def test_score_requires_method_and_weights():
    with pytest.raises(ValidationError):
        Score(value=100)  # method + weights are frozen keys, not defaulted


def test_schema_version_required():
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(
            run=_run(exit_code=0),
            score=_score(100),
            totals=totals(checks),
            suites=[{"id": "s", "source_sha": "x", "score": 100, "totals": totals(checks)}],
            checks=checks,
        )  # missing schema_version


def test_duplicate_check_identity_rejected():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.PASS, id="dup"), _chk(Verdict.PASS, id="dup")])


def test_duplicate_suite_id_rejected():
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(
            schema_version=SCHEMA_VERSION,
            run=_run(exit_code=0),
            score=_score(100),
            totals=totals(checks),
            suites=[
                {"id": "s", "source_sha": "x", "score": 100, "totals": totals(checks)},
                {"id": "s", "source_sha": "y", "score": 100, "totals": totals(checks)},
            ],
            checks=checks,
        )


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["complete", "partial", "generated-only", "failed"])
def test_fixture_validates_against_schema_and_round_trips(name):
    raw = json.loads((FIXTURES / f"results.{name}.json").read_text())
    jsonschema.validate(raw, results_schema())  # structural (JSON Schema)
    model = Results.model_validate(raw)  # + derived invariants (Pydantic)
    assert json.loads(model.model_dump_json(by_alias=True, exclude_none=False)) == raw


def test_committed_results_schema_is_current():
    committed = json.loads((SPECS_DIR / "results.schema.json").read_text())
    assert committed == results_schema()  # regenerate + recommit if this fails
