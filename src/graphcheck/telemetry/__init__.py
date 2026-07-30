"""Opt-in, privacy-preserving telemetry for GraphCheck."""

from graphcheck.telemetry.collector import TelemetryCollector
from graphcheck.telemetry.events import EngineEvent, EngineEventSink
from graphcheck.telemetry.policy import ConsentState, resolve_consent

__all__ = [
    "ConsentState",
    "EngineEvent",
    "EngineEventSink",
    "TelemetryCollector",
    "resolve_consent",
]
