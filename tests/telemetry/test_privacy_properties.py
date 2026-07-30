import json

from hypothesis import given, settings
from hypothesis import strategies as st

from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.engine.runner import Engine, SuiteInput
from graphcheck.neo4j_adapter import QueryResult
from graphcheck.telemetry.collector import TelemetryCollector

TARGET = RunTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:test",
    capabilities=Capabilities(apoc=False, count_store=True),
)


@settings(max_examples=20, deadline=None)
@given(
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./:@ ",
        min_size=1,
        max_size=40,
    )
)
def test_sensitive_check_config_query_parameter_and_result_never_reach_payload(secret):
    marker = f"private<{secret}>"
    suite = f"""\
suite: privacy
competency:
  - id: hidden
    question: {json.dumps(marker)}
    query: RETURN $value AS value
    params:
      value: {json.dumps(marker)}
    expect: {{rows: {{exactly: 1}}}}
"""

    class Client:
        def run_read_result(self, query, params, *, timeout_s=None):
            assert marker in repr((query, params))
            return QueryResult([{"value": marker}], ("value",), ())

    collector = TelemetryCollector()
    Engine(Client(), event_sink=collector).run(
        [SuiteInput.from_yaml(suite)],
        target=TARGET,
    )

    assert marker not in repr(collector.posthog_events())
