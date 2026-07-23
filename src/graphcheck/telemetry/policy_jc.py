"""jc starter for the SPEC-10 consent and privacy policy.

This module is intentionally isolated from runtime imports.  Implement it against the scoped
handoff in ``docs/jc-handoff-spec-10.md``
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from graphcheck.telemetry.events import SafeErrorCode, SafeExceptionType, Template
from graphcheck.telemetry.policy import ConsentSource

CONSENT_VERSION = "1.0"


@dataclass(frozen=True)
class ConsentState:
    enabled: bool
    source: ConsentSource
    consent_version: str | None = None
    distinct_id: UUID | None = None
    persistent: bool = False
    renewal_required: bool = False

    # TODO(jc): enabled states require an ID/version; disabled states must expose no ID.


def resolve_consent(
    *,
    path: Path,
    environ: Mapping[str, str],
    id_factory,
) -> ConsentState:
    """Resolve stored consent plus DNT/process overrides without writing to disk."""

    raise NotImplementedError("jc task: implement the precedence table in the handoff")


def enable_telemetry(*, path: Path, id_factory) -> ConsentState:
    """Atomically store a user-level opt-in and installation UUID."""

    raise NotImplementedError("jc task: implement explicit, idempotent enable")


def disable_telemetry(*, path: Path) -> ConsentState:
    """Disable collection while ensuring an inactive UUID is never exposed or reused."""

    raise NotImplementedError("jc task: implement disable")


def reset_installation_id(*, path: Path, id_factory) -> ConsentState:
    """Break linkage while preserving the enabled/disabled state."""

    raise NotImplementedError("jc task: implement reset")


def safe_template(value: object) -> Template:
    """Map built-in check names to broad families; unknown values become custom."""

    raise NotImplementedError("jc task: implement the template allowlist")


def safe_error_code(value: object | None) -> SafeErrorCode | None:
    """Map jcal errors to SPEC-10 codes without forwarding arbitrary strings."""

    raise NotImplementedError("jc task: implement the safe error-code map")


def safe_exception_type(
    exc_or_type: BaseException | type[BaseException] | object,
) -> SafeExceptionType:
    """Return only allowlisted standard-library exception names."""

    raise NotImplementedError("jc task: implement the exception allowlist")


def assert_private_payload(
    payload: Mapping[str, object],
    *,
    sensitive_values: Iterable[object] = (),
) -> None:
    """Recursively reject forbidden field names and representative sensitive values."""

    raise NotImplementedError("jc task: implement recursive defense-in-depth checks")
