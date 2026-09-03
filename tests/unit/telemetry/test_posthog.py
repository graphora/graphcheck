import threading
import time
from uuid import UUID

import pytest

from graphcheck.telemetry import posthog as posthog_module
from graphcheck.telemetry.collector import PostHogEvent
from graphcheck.telemetry.events import EventOutcome
from graphcheck.telemetry.policy import (
    ArtifactOutcome,
    CommandCompleted,
    CommandName,
    ConsentSource,
    ConsentState,
    OsFamily,
    OutputMode,
    ProcessOutcome,
    ProfileCompleted,
    ProfileOutcome,
    ProfilerStage,
)
from graphcheck.telemetry.posthog import (
    PostHogAdapter,
    TelemetrySession,
    telemetry_delivery_configured,
)

DISTINCT_ID = UUID("00000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("00000000-0000-4000-8000-000000000002")
SESSION_ID = UUID("00000000-0000-4000-8000-000000000003")


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def send(self, event, properties):
        self.calls.append((event, dict(properties)))


def _session():
    consent = ConsentState(
        True,
        ConsentSource.STORED,
        "1.0",
        DISTINCT_ID,
        persistent=True,
    )
    return TelemetrySession.create(
        consent,
        command_id_factory=lambda: COMMAND_ID,
        session_id=SESSION_ID,
    )


def _command():
    return CommandCompleted(
        command=CommandName.RUN,
        action=None,
        process_outcome=ProcessOutcome.SUCCESS,
        failure_stage=None,
        duration_ms=100,
        setup_ms=10,
        artifact_write_ms=20,
        render_ms=5,
        output_mode=OutputMode.HUMAN,
        results_artifact=ArtifactOutcome.WRITTEN,
        report_artifact=ArtifactOutcome.WRITTEN,
        baseline_artifact=ArtifactOutcome.NOT_REQUESTED,
        generated_artifact=ArtifactOutcome.NOT_REQUESTED,
        telemetry_command_id=COMMAND_ID,
        telemetry_run_id=None,
        probe_outcome=None,
        probe_duration_ms=None,
        server_version_major=None,
        server_version_minor=None,
        apoc_available=None,
        count_store_available=None,
        interactive=False,
        ci=False,
        os_family=OsFamily.LINUX,
        os_version="6.8",
        python_minor="3.12",
        graphcheck_version="0.1.0",
        safe_error_code=None,
    )


def test_adapter_adds_common_privacy_properties_and_flushes():
    transport = RecordingTransport()
    adapter = PostHogAdapter(_session(), transport)
    adapter.capture_command(_command())

    assert adapter.close(timeout_s=1.0) is True
    assert len(transport.calls) == 1
    name, properties = transport.calls[0]
    assert name == "graphcheck_command_completed"
    assert properties["distinct_id"] == str(DISTINCT_ID)
    assert properties["session_id"] == str(SESSION_ID)
    assert properties["telemetry_command_id"] == str(COMMAND_ID)
    assert properties["process_person_profile"] is False
    assert properties["geoip_enrichment"] is False
    assert properties["$process_person_profile"] is False
    assert properties["$geoip_disable"] is True
    assert properties["os_version"] == "6.8"
    assert properties["telemetry_schema_version"] == "1.1"


def test_final_flush_is_bounded_when_transport_blocks():
    release = threading.Event()

    class BlockingTransport:
        def send(self, event, properties):
            release.wait(1.0)

    adapter = PostHogAdapter(_session(), BlockingTransport())
    adapter.capture_command(_command())
    started = time.monotonic()
    assert adapter.close(timeout_s=0.01) is False
    assert time.monotonic() - started < 0.2
    release.set()


def test_outbound_event_requires_the_exact_reviewed_property_schema():
    with pytest.raises(ValueError, match="allowlisted schema"):
        PostHogEvent(
            "graphcheck_run_started",
            {
                "payload": "bolt://neo4j:secret@example/private",
                "verdict": "fail",
            },
        )


def test_release_key_seam_and_operator_override_are_explicit(monkeypatch):
    monkeypatch.setattr(posthog_module, "POSTHOG_PROJECT_API_KEY", None)

    assert telemetry_delivery_configured({}) is False
    assert telemetry_delivery_configured({"GRAPHCHECK_POSTHOG_API_KEY": "phc_test"}) is True


def test_profile_payload_uses_the_reviewed_schema_and_common_properties():
    transport = RecordingTransport()
    adapter = PostHogAdapter(_session(), transport)
    adapter.capture_profile(
        ProfileCompleted(
            outcome=ProfileOutcome.COMPLETE,
            duration_ms=500,
            schema_ms=100,
            property_coverage_ms=200,
            degree_distribution_ms=100,
            deadline_exhausted=False,
            last_completed_stage=ProfilerStage.DEGREE_DISTRIBUTION,
            partial_reason=None,
            probe_outcome=EventOutcome.SUCCESS,
            probe_duration_ms=20,
            server_version_major=5,
            server_version_minor=18,
            apoc_available=False,
            count_store_available=True,
            safe_error_code=None,
        )
    )

    assert adapter.close(timeout_s=1.0) is True
    assert len(transport.calls) == 1
    name, properties = transport.calls[0]
    assert name == "graphcheck_profile_completed"
    assert properties["telemetry_command_id"] == str(COMMAND_ID)
    assert properties["last_completed_stage"] == "degree_distribution"
