from pathlib import Path

import pytest

from graphcheck.debug_diagnostics import CapabilityContext, blocked_checks_for_project
from graphcheck.errors import GraphCheckError
from graphcheck.packs.catalog import PACKS_DIRECTORY, load_pack_requirements
from graphcheck.project import write_default_project


def _context(
    *,
    read: bool = True,
    show_procedures: bool = True,
    apoc: bool = True,
    count_store: bool = True,
) -> CapabilityContext:
    return CapabilityContext(
        read=read,
        show_procedures=show_procedures,
        apoc=apoc,
        count_store=count_store,
    )


def _write_suite(path, *, suite: str, check_id: str, generated: str = ""):
    path.write_text(
        f"""suite: {suite}
conformance:
  - id: {check_id}
    check: completeness
    with: {{ label: Customer, property: name }}
{generated}""",
        encoding="utf-8",
    )


def _write_apoc_pack(path: Path) -> Path:
    source = (PACKS_DIRECTORY / "core.yml").read_text(encoding="utf-8")
    updated = source.replace("    requires: [read]", "    requires: [read, apoc]", 1)
    assert updated != source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return path


def test_blocked_checks_use_production_pack_requirements_for_yml_and_yaml(tmp_path):
    write_default_project(tmp_path)
    checks = tmp_path / "checks"
    _write_suite(checks / "a.yml", suite="a", check_id="a-read")
    _write_suite(checks / "b.yaml", suite="b", check_id="b-read")

    blocked = blocked_checks_for_project(tmp_path, _context(read=False))

    assert [(item.suite, item.check_id, item.missing_capability) for item in blocked] == [
        ("a", "a-read", "read"),
        ("b", "b-read", "read"),
    ]


def test_read_requirement_uses_visibility_not_target_capabilities(tmp_path):
    write_default_project(tmp_path)
    _write_suite(tmp_path / "checks" / "suite.yaml", suite="s", check_id="active")

    assert blocked_checks_for_project(tmp_path, _context(read=True)) == []


def test_apoc_requirement_is_loaded_from_pack_yaml_and_names_blocked_check(tmp_path):
    write_default_project(tmp_path)
    _write_suite(tmp_path / "checks" / "suite.yaml", suite="onboarding", check_id="names")
    pack_path = _write_apoc_pack(tmp_path / "packs" / "core.yaml")

    blocked = blocked_checks_for_project(
        tmp_path,
        _context(apoc=False),
        pack_paths=[pack_path],
    )

    assert len(blocked) == 1
    assert blocked[0].suite == "onboarding"
    assert blocked[0].check_id == "names"
    assert blocked[0].check == "completeness"
    assert blocked[0].missing_capability == "apoc"
    assert "Install APOC" in blocked[0].fix


def test_apoc_pack_check_is_not_blocked_when_probe_finds_apoc(tmp_path):
    write_default_project(tmp_path)
    _write_suite(tmp_path / "checks" / "suite.yml", suite="onboarding", check_id="names")
    pack_path = _write_apoc_pack(tmp_path / "packs" / "core.yml")

    assert (
        blocked_checks_for_project(
            tmp_path,
            _context(apoc=True),
            pack_paths=[pack_path],
        )
        == []
    )


def test_apoc_blocker_distinguishes_hidden_procedures_from_confirmed_absence(tmp_path):
    write_default_project(tmp_path)
    _write_suite(tmp_path / "checks" / "suite.yml", suite="onboarding", check_id="names")
    pack_path = _write_apoc_pack(tmp_path / "packs" / "core.yml")

    blocked = blocked_checks_for_project(
        tmp_path,
        _context(apoc=False, show_procedures=False),
        pack_paths=[pack_path],
    )

    assert len(blocked) == 1
    assert "Grant procedure visibility and execution" in blocked[0].fix


def test_pack_requirement_catalog_accepts_yaml_extension(tmp_path):
    pack_path = _write_apoc_pack(tmp_path / "packs" / "core.yaml")

    requirements = load_pack_requirements([pack_path])

    assert requirements["completeness"] == ("read", "apoc")


def test_invalid_pack_metadata_is_reported_as_fixable_error(tmp_path):
    write_default_project(tmp_path)
    _write_suite(tmp_path / "checks" / "suite.yaml", suite="s", check_id="active")
    pack_path = tmp_path / "packs" / "core.yaml"
    pack_path.parent.mkdir()
    pack_path.write_text("pack: core\nchecks: [\n", encoding="utf-8")

    with pytest.raises(GraphCheckError) as caught:
        blocked_checks_for_project(tmp_path, _context(), pack_paths=[pack_path])

    assert caught.value.error.code == "packs.invalid"
    assert "Fix the check pack YAML" in caught.value.error.fix


def test_generated_checks_are_not_reported_as_active_blockers(tmp_path):
    write_default_project(tmp_path)
    _write_suite(
        tmp_path / "checks" / "suite.yaml",
        suite="s",
        check_id="draft",
        generated="    generated: true\n",
    )

    assert blocked_checks_for_project(tmp_path, _context(read=False)) == []


def test_generated_suite_is_not_reported_as_active_blockers(tmp_path):
    write_default_project(tmp_path)
    (tmp_path / "checks" / "suite.yaml").write_text(
        """suite: s
generated: true
conformance:
  - id: draft
    check: completeness
    with: { label: Customer, property: name }
""",
        encoding="utf-8",
    )

    assert blocked_checks_for_project(tmp_path, _context(read=False)) == []


def test_invalid_suite_is_reported_as_fixable_check_error(tmp_path):
    write_default_project(tmp_path)
    (tmp_path / "checks" / "broken.yaml").write_text("suite: [\n", encoding="utf-8")

    with pytest.raises(GraphCheckError) as caught:
        blocked_checks_for_project(tmp_path, _context())

    assert caught.value.error.code == "checks.invalid"
    assert "Fix the check YAML" in caught.value.error.fix


def test_unexpected_loader_errors_are_not_reported_as_invalid_yaml(tmp_path, monkeypatch):
    write_default_project(tmp_path)
    _write_suite(tmp_path / "checks" / "suite.yaml", suite="s", check_id="active")

    def crash(text, *, source=None):
        raise RuntimeError("loader bug")

    monkeypatch.setattr("graphcheck.debug_diagnostics.load_suite", crash)

    with pytest.raises(RuntimeError, match="loader bug"):
        blocked_checks_for_project(tmp_path, _context())
