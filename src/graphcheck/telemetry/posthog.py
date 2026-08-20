"""Best-effort PostHog transport kept outside the engine dependency boundary."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from graphcheck import __version__
from graphcheck.telemetry.collector import PostHogEvent, TelemetryCollector
from graphcheck.telemetry.policy import (
    TELEMETRY_SCHEMA_VERSION,
    CommandCompleted,
    ConsentState,
    ProfileCompleted,
    assert_allowlisted_posthog_payload,
)
from graphcheck.telemetry.release import POSTHOG_HOST, POSTHOG_PROJECT_API_KEY

DEFAULT_POSTHOG_HOST = POSTHOG_HOST
DEFAULT_FLUSH_TIMEOUT_S = 0.5


class TelemetryTransport(Protocol):
    def send(self, event: str, properties: Mapping[str, object]) -> None: ...


@dataclass(frozen=True)
class TelemetrySession:
    consent: ConsentState
    telemetry_command_id: UUID
    session_id: UUID
    graphcheck_version: str = __version__

    @classmethod
    def create(
        cls,
        consent: ConsentState,
        *,
        command_id_factory=uuid.uuid4,
        session_id: UUID | None = None,
    ) -> TelemetrySession:
        if not consent.enabled:
            raise ValueError("a telemetry session requires active consent")
        return cls(
            consent=consent,
            telemetry_command_id=command_id_factory(),
            session_id=session_id or _PROCESS_SESSION_ID,
        )

    def common_properties(self) -> dict[str, object]:
        assert self.consent.distinct_id is not None
        assert self.consent.consent_version is not None
        return {
            "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
            "consent_version": self.consent.consent_version,
            "graphcheck_version": self.graphcheck_version,
            "distinct_id": str(self.consent.distinct_id),
            "session_id": str(self.session_id),
            "telemetry_command_id": str(self.telemetry_command_id),
            "process_person_profile": False,
            "geoip_enrichment": False,
            "$process_person_profile": False,
            "$geoip_disable": True,
        }


class HttpPostHogTransport:
    """Tiny capture API client; no PostHog SDK is imported or required."""

    def __init__(
        self,
        api_key: str,
        *,
        host: str = DEFAULT_POSTHOG_HOST,
        request_timeout_s: float = 0.4,
    ) -> None:
        self._api_key = api_key
        self._url = f"{host.rstrip('/')}/capture/"
        self._request_timeout_s = request_timeout_s

    def send(self, event: str, properties: Mapping[str, object]) -> None:
        body = json.dumps(
            {"api_key": self._api_key, "event": event, "properties": dict(properties)},
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._request_timeout_s) as response:
            status = int(getattr(response, "status", 200))
            if status < 200 or status >= 300:
                raise OSError(f"PostHog capture returned HTTP {status}")


class PostHogAdapter:
    """Queue events and swallow all transport failures on a daemon worker."""

    def __init__(
        self,
        session: TelemetrySession,
        transport: TelemetryTransport,
        *,
        queue_size: int = 2048,
    ) -> None:
        self.session = session
        self._transport = transport
        self._queue: queue.Queue[_QueuedPostHogEvent | object] = queue.Queue(maxsize=queue_size)
        self._stopped = False
        self._worker = threading.Thread(
            target=self._send_loop,
            name="graphcheck-telemetry",
            daemon=True,
        )
        self._worker.start()

    def capture(self, event: PostHogEvent) -> None:
        if self._stopped:
            return
        try:
            properties = {**event.properties, **self.session.common_properties()}
            assert_allowlisted_posthog_payload(
                event.name,
                properties,
                includes_common=True,
            )
            queued = _QueuedPostHogEvent(
                event.name,
                MappingProxyType(dict(properties)),
            )
            self._queue.put_nowait(queued)
        except Exception:
            # Event loss is preferable to delaying or failing a user command.
            return

    def capture_collector(self, collector: TelemetryCollector) -> None:
        try:
            events = collector.posthog_events()
        except Exception:
            return
        for event in events:
            self.capture(event)

    def capture_command(self, event: CommandCompleted) -> None:
        if event.telemetry_command_id != self.session.telemetry_command_id:
            return
        self.capture(
            PostHogEvent(
                "graphcheck_command_completed",
                event.model_dump(mode="json", exclude={"telemetry_command_id"}),
            )
        )

    def capture_profile(self, event: ProfileCompleted) -> None:
        self.capture(
            PostHogEvent(
                "graphcheck_profile_completed",
                event.model_dump(mode="json"),
            )
        )

    def flush(self, timeout_s: float = DEFAULT_FLUSH_TIMEOUT_S) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        return self._queue.unfinished_tasks == 0

    def close(self, timeout_s: float = DEFAULT_FLUSH_TIMEOUT_S) -> bool:
        if self._stopped:
            return self._queue.unfinished_tasks == 0
        flushed = self.flush(timeout_s)
        self._stopped = True
        with suppress(queue.Full):
            self._queue.put_nowait(_STOP)
        return flushed

    def _send_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _QueuedPostHogEvent)
                with suppress(Exception):
                    self._transport.send(item.name, item.properties)
            finally:
                self._queue.task_done()


def create_posthog_adapter(
    consent: ConsentState,
    *,
    transport: TelemetryTransport | None = None,
    environ: Mapping[str, str] | None = None,
    command_id_factory=uuid.uuid4,
    session: TelemetrySession | None = None,
) -> PostHogAdapter | None:
    """Construct no client or worker when disabled or when no product key is configured."""

    if not consent.enabled:
        return None
    env = os.environ if environ is None else environ
    selected_transport = transport
    if selected_transport is None:
        api_key = _posthog_api_key(env)
        if not api_key:
            return None
        selected_transport = HttpPostHogTransport(
            api_key,
            host=env.get("GRAPHCHECK_POSTHOG_HOST", DEFAULT_POSTHOG_HOST),
        )
    return PostHogAdapter(
        session or TelemetrySession.create(consent, command_id_factory=command_id_factory),
        selected_transport,
    )


def telemetry_delivery_configured(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether this process has a release or operator-supplied PostHog project key."""

    env = os.environ if environ is None else environ
    return _posthog_api_key(env) is not None


def _posthog_api_key(environ: Mapping[str, str]) -> str | None:
    candidate = environ.get("GRAPHCHECK_POSTHOG_API_KEY") or POSTHOG_PROJECT_API_KEY
    if candidate is None:
        return None
    stripped = candidate.strip()
    return stripped or None


_PROCESS_SESSION_ID = uuid.uuid4()
_STOP = object()


@dataclass(frozen=True)
class _QueuedPostHogEvent:
    name: str
    properties: Mapping[str, object]
