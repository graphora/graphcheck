from pathlib import Path

from graphcheck.contracts.check import load_suite


def test_agents_example_is_valid():
    text = Path("tests/unit/contracts/fixtures/agent-suite.yml").read_text()
    suite = load_suite(
        text,
        source="tests/unit/contracts/fixtures/agent-suite.yml",
    )

    assert suite.suite == "Fraud Ring"
