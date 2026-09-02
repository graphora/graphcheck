from pathlib import Path

import yaml

from graphcheck.application.suites import load_suite_inputs
from graphcheck.contracts.results import Pattern
from graphcheck.engine.runner import SuiteInput

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "minimal"


def test_minimal_first_run_discovers_only_baseline_free_checks():
    project = yaml.safe_load((EXAMPLE / "graphcheck.yml").read_text(encoding="utf-8"))
    discovered = load_suite_inputs(EXAMPLE / project["checks"], [])

    assert {check.id for suite in discovered for check in suite.suite.checks} == {
        "customer-name-present",
        "customers-can-be-counted",
    }
    assert all(
        check.pattern is not Pattern.DRIFT for suite in discovered for check in suite.suite.checks
    )


def test_minimal_optional_drift_example_stays_valid_but_outside_discovery():
    optional = EXAMPLE / "optional-checks" / "customer-count-drift.yml"
    suite = SuiteInput.from_yaml(optional.read_text(encoding="utf-8"), source=str(optional))

    assert optional.parent != EXAMPLE / "checks"
    assert [check.pattern for check in suite.suite.checks] == [Pattern.DRIFT]
