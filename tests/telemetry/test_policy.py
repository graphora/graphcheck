import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from graphcheck.telemetry.events import Pattern, SafeErrorCode, Template
from graphcheck.telemetry.policy import (
    CONSENT_VERSION,
    ConsentSource,
    OsFamily,
    assert_private_payload,
    disable_telemetry,
    enable_telemetry,
    os_family,
    os_version,
    python_minor,
    reset_installation_id,
    resolve_consent,
    safe_error_code,
    safe_exception_type,
    safe_pattern,
    safe_template,
    version_major_minor,
)

FIRST_ID = UUID("00000000-0000-4000-8000-000000000001")
SECOND_ID = UUID("00000000-0000-4000-8000-000000000002")


def test_consent_is_default_off_and_constructs_no_id(tmp_path):
    calls = 0

    def make_id():
        nonlocal calls
        calls += 1
        return FIRST_ID

    state = resolve_consent(path=tmp_path / "telemetry.json", environ={}, id_factory=make_id)

    assert state.enabled is False
    assert state.distinct_id is None
    assert calls == 0
    assert not (tmp_path / "telemetry.json").exists()


def test_enable_persists_disable_hides_and_process_override_never_reuses_id(tmp_path):
    path = tmp_path / "telemetry.json"
    enabled = enable_telemetry(path=path, id_factory=lambda: FIRST_ID)
    assert enabled.distinct_id == FIRST_ID
    assert resolve_consent(path=path, environ={}).persistent is True

    disable_telemetry(path=path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["distinct_id"] == str(FIRST_ID)
    process_only = resolve_consent(
        path=path,
        environ={"GRAPHCHECK_TELEMETRY": "1"},
        id_factory=lambda: SECOND_ID,
    )
    assert process_only.source is ConsentSource.ENVIRONMENT
    assert process_only.distinct_id == SECOND_ID
    assert process_only.persistent is False
    assert json.loads(path.read_text(encoding="utf-8")) == stored


def test_do_not_track_and_zero_override_stored_opt_in(tmp_path):
    path = tmp_path / "telemetry.json"
    enable_telemetry(path=path, id_factory=lambda: FIRST_ID)

    assert not resolve_consent(path=path, environ={"DO_NOT_TRACK": "1"}).enabled
    assert not resolve_consent(path=path, environ={"GRAPHCHECK_TELEMETRY": "0"}).enabled


def test_only_consent_version_changes_require_renewal(tmp_path):
    path = tmp_path / "telemetry.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "consent_version": "0.9",
                "distinct_id": str(FIRST_ID),
            }
        ),
        encoding="utf-8",
    )

    state = resolve_consent(path=path, environ={})
    assert state.enabled is False
    assert state.renewal_required is True
    assert CONSENT_VERSION == "1.0"


def test_reset_breaks_linkage_and_allowlists_map_unknown_values(tmp_path):
    path = tmp_path / "telemetry.json"
    enable_telemetry(path=path, id_factory=lambda: FIRST_ID)
    reset = reset_installation_id(path=path, id_factory=lambda: SECOND_ID)

    assert reset.distinct_id == SECOND_ID
    assert safe_template("single-customer-secret-check") is Template.CUSTOM
    assert safe_pattern("future-private-pattern") is Pattern.UNKNOWN
    assert safe_error_code("profile.counts_unavailable") is SafeErrorCode.PROFILE_COLLECTION_FAILED
    assert safe_error_code("baseline.not_found") is SafeErrorCode.BASELINE_MISSING
    assert safe_error_code("customer.secret.failure") is SafeErrorCode.UNKNOWN


def test_custom_exception_with_allowlisted_name_remains_unknown():
    custom_runtime_error = type("RuntimeError", (Exception,), {})

    assert safe_exception_type(RuntimeError("stdlib")).value == "RuntimeError"
    assert safe_exception_type(custom_runtime_error("custom")).value == "unknown"


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Windows", OsFamily.WINDOWS),
        ("Darwin", OsFamily.MACOS),
        ("Linux", OsFamily.LINUX),
        ("FreeBSD", OsFamily.OTHER),
    ],
)
def test_os_family_is_coarse_and_allowlisted(system, expected):
    assert os_family(system) is expected


@pytest.mark.parametrize(
    ("system", "version", "expected"),
    [
        ("Windows", "11", "11"),
        ("Darwin", "15.4.1", "15.4"),
        ("Linux", "6.8.0-64-generic", "6.8"),
        ("FreeBSD", "14.2-RELEASE", "14.2"),
        ("Linux", "private-custom-kernel", "unknown"),
    ],
)
def test_os_version_excludes_build_and_distribution_details(system, version, expected):
    assert os_version(system, version) == expected


def test_python_version_is_limited_to_major_and_minor():
    assert python_minor(SimpleNamespace(major=3, minor=13, micro=7)) == "3.13"


def test_server_version_parsing_keeps_calver_year_and_month_separate():
    assert version_major_minor("5.26.28") == (5, 26)
    assert version_major_minor("2026.06.0") == (2026, 6)


def test_privacy_assertion_rejects_content_fields_and_values():
    with pytest.raises(ValueError, match="privacy-denied"):
        assert_private_payload({"query": "RETURN 1"})
    with pytest.raises(ValueError, match="sensitive value"):
        assert_private_payload(
            {"safe_error_code": "unknown-secret"},
            sensitive_values=("secret",),
        )
