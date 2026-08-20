"""Opt-in telemetry with lazy public imports."""

from importlib import import_module

__all__ = [
    "ConsentState",
    "EngineEvent",
    "EngineEventSink",
    "TelemetryCollector",
    "resolve_consent",
]


def __getattr__(name: str):
    public = {
        "ConsentState": ("graphcheck.telemetry.types", "ConsentState"),
        "EngineEvent": ("graphcheck.telemetry.events", "EngineEvent"),
        "EngineEventSink": ("graphcheck.telemetry.events", "EngineEventSink"),
        "TelemetryCollector": ("graphcheck.telemetry.collector", "TelemetryCollector"),
        "resolve_consent": ("graphcheck.telemetry.consent", "resolve_consent"),
    }
    if name in public:
        module_name, attribute = public[name]
        return getattr(import_module(module_name), attribute)
    try:
        return import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(name) from exc
