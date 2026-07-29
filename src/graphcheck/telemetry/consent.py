"""Standard-library-only telemetry consent storage and resolution."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from graphcheck.telemetry.types import CONSENT_VERSION, ConsentSource, ConsentState

_CONFIG_ENV = "GRAPHCHECK_TELEMETRY_CONFIG"


def user_config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(_CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and env.get("APPDATA"):
        return Path(env["APPDATA"]) / "GraphCheck" / "telemetry.json"
    base = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "graphcheck" / "telemetry.json"


def resolve_consent(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    id_factory=uuid.uuid4,
) -> ConsentState:
    """Resolve telemetry without writing a file or creating an ID while disabled."""

    env = os.environ if environ is None else environ
    stored = _read_consent(path or user_config_path(env))
    stored_active = bool(
        stored
        and stored.get("enabled") is True
        and stored.get("consent_version") == CONSENT_VERSION
        and _parse_uuid(stored.get("distinct_id")) is not None
    )
    renewal_required = bool(
        stored
        and stored.get("enabled") is True
        and stored.get("consent_version") != CONSENT_VERSION
    )
    if env.get("DO_NOT_TRACK") == "1":
        return ConsentState(False, ConsentSource.DO_NOT_TRACK, renewal_required=renewal_required)
    if env.get("GRAPHCHECK_TELEMETRY") == "0":
        return ConsentState(False, ConsentSource.ENVIRONMENT, renewal_required=renewal_required)
    if env.get("GRAPHCHECK_TELEMETRY") == "1":
        if stored_active:
            return ConsentState(
                True,
                ConsentSource.STORED,
                CONSENT_VERSION,
                _parse_uuid(stored["distinct_id"]),
                persistent=True,
            )
        return ConsentState(
            True,
            ConsentSource.ENVIRONMENT,
            CONSENT_VERSION,
            _process_only_distinct_id(id_factory),
            renewal_required=renewal_required,
        )
    if stored_active:
        return ConsentState(
            True,
            ConsentSource.STORED,
            CONSENT_VERSION,
            _parse_uuid(stored["distinct_id"]),
            persistent=True,
        )
    return ConsentState(False, ConsentSource.DEFAULT, renewal_required=renewal_required)


def enable_telemetry(*, path: Path | None = None, id_factory=uuid.uuid4) -> ConsentState:
    destination = path or user_config_path()
    stored = _read_consent(destination)
    existing_id = (
        _parse_uuid(stored.get("distinct_id"))
        if stored
        and stored.get("enabled") is True
        and stored.get("consent_version") == CONSENT_VERSION
        else None
    )
    if existing_id is not None:
        return ConsentState(
            True,
            ConsentSource.STORED,
            CONSENT_VERSION,
            existing_id,
            persistent=True,
        )
    distinct_id = id_factory()
    _write_consent(
        destination,
        {
            "enabled": True,
            "consent_version": CONSENT_VERSION,
            "distinct_id": str(distinct_id),
        },
    )
    return ConsentState(
        True,
        ConsentSource.STORED,
        CONSENT_VERSION,
        distinct_id,
        persistent=True,
    )


def disable_telemetry(*, path: Path | None = None) -> ConsentState:
    destination = path or user_config_path()
    stored = _read_consent(destination) or {}
    _write_consent(
        destination,
        {
            "enabled": False,
            "consent_version": stored.get("consent_version", CONSENT_VERSION),
            "distinct_id": stored.get("distinct_id"),
        },
    )
    return ConsentState(False, ConsentSource.STORED)


def reset_installation_id(
    *,
    path: Path | None = None,
    id_factory=uuid.uuid4,
) -> ConsentState:
    destination = path or user_config_path()
    stored = _read_consent(destination) or {}
    active = bool(
        stored.get("enabled") is True and stored.get("consent_version") == CONSENT_VERSION
    )
    if active:
        distinct_id = id_factory()
        _write_consent(
            destination,
            {
                "enabled": True,
                "consent_version": CONSENT_VERSION,
                "distinct_id": str(distinct_id),
            },
        )
        return ConsentState(
            True,
            ConsentSource.STORED,
            CONSENT_VERSION,
            distinct_id,
            persistent=True,
        )
    _write_consent(
        destination,
        {
            "enabled": False,
            "consent_version": stored.get("consent_version", CONSENT_VERSION),
            "distinct_id": None,
        },
    )
    return ConsentState(False, ConsentSource.STORED)


def _read_consent(path: Path) -> dict[str, object] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_consent(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _parse_uuid(value: object) -> UUID | None:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed.version == 4 else None


def _process_only_distinct_id(id_factory) -> UUID:
    global _PROCESS_ONLY_DISTINCT_ID
    if id_factory is not uuid.uuid4:
        return id_factory()
    if _PROCESS_ONLY_DISTINCT_ID is None:
        _PROCESS_ONLY_DISTINCT_ID = uuid.uuid4()
    return _PROCESS_ONLY_DISTINCT_ID


_PROCESS_ONLY_DISTINCT_ID: UUID | None = None
