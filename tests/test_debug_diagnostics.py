import pytest

from graphcheck.debug_diagnostics import CapabilityContext, blocked_checks_for_project
from graphcheck.errors import GraphCheckError
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
