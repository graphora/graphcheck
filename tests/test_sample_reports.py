from __future__ import annotations

import re
from pathlib import Path

import pytest

from graphcheck.reporting.html import render_validated_html_report
from graphcheck.reporting.writer import load_results
from scripts import generate_sample_reports as samples

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "contracts" / "fixtures"


def _results(name: str):
    return load_results(FIXTURES / f"results.{name}.json")


def test_canonicalization_does_not_mutate_live_results():
    live = _results("complete")
    before = live.model_dump(mode="python", by_alias=True, exclude_none=False)

    samples.canonicalize_results(live, "findings")

    assert live.model_dump(mode="python", by_alias=True, exclude_none=False) == before


def test_all_volatile_fields_are_canonicalized():
    canonical = samples.canonicalize_results(_results("complete"), "findings")

    assert canonical.run.id == "canonical-findings"
    assert canonical.run.started_at == samples.CANONICAL_STARTED_AT
    assert canonical.run.finished_at == samples.CANONICAL_FINISHED_AT
    assert all(
        check.started_at == samples.CANONICAL_STARTED_AT and check.duration_ms == 0
        for check in canonical.checks
        if check.executed
    )
    assert all(
        line == line.rstrip()
        for check in canonical.checks
        for line in (check.compiled_query or "").splitlines()
    )
    for check in canonical.checks:
        if check.evidence is not None:
            counters: dict[str, int] = {}
            for element in check.evidence.elements:
                counters[element.kind] = counters.get(element.kind, 0) + 1
                assert element.id == f"{element.kind}-{counters[element.kind]:03d}"


def test_canonicalization_is_idempotent_and_contract_valid():
    once = samples.canonicalize_results(_results("complete"), "findings")
    twice = samples.canonicalize_results(once, "findings")

    assert twice == once
    assert load_results(twice) == once


def test_volatile_differences_render_byte_identically_after_canonicalization():
    first = _results("complete")
    second = _results("complete")
    second.run.id = "another-run"
    second.run.started_at = "2027-02-03T04:05:06Z"
    second.run.finished_at = "2027-02-03T04:05:07Z"
    for index, check in enumerate(second.checks):
        if check.executed:
            check.started_at = "2027-02-03T04:05:06Z"
            check.duration_ms = 900 + index
        if check.evidence is not None:
            for element_index, element in enumerate(check.evidence.elements):
                element.id = f"volatile-{index}-{element_index}"

    first_html = render_validated_html_report(
        samples.canonicalize_results(first, "findings")
    ).encode("utf-8")
    second_html = render_validated_html_report(
        samples.canonicalize_results(second, "findings")
    ).encode("utf-8")

    assert second_html == first_html


@pytest.mark.parametrize(
    "bad",
    [
        '<img src="image.png">',
        "<img src=relative.png>",
        '<a href="/elsewhere">link</a>',
        '<svg><use xlink:href="icons.svg#check"></use></svg>',
        '<div style="background: url(relative.png)"></div>',
        "https://example.test/a.css",
        "'//cdn.example.test/a.js'",
        "@import 'theme.css'",
        "body { background: url(icon.svg); }",
    ],
)
def test_self_containment_validation_rejects_references(bad: str):
    with pytest.raises(RuntimeError, match="not self-contained"):
        samples.assert_self_contained_html(
            f"<!doctype html><style>{bad}</style><script></script>{bad}"
        )


def test_self_containment_validation_accepts_renderer_output():
    html = render_validated_html_report(
        samples.canonicalize_results(_results("clean"), "clean")
    )
    samples.assert_self_contained_html(html)


def test_fixture_metadata_matches_generator_expectations():
    manifest = samples.verify_fixture_checkout()
    suite = samples.SuiteInput.from_yaml(samples.SUITE_PATH.read_text(encoding="utf-8"))

    assert manifest["neo4j_version"] == samples.NEO4J_VERSION == "5.26.28"
    assert manifest["requires_empty_database"] is True
    assert manifest["seed_script"] == "seed.cypher"
    assert manifest["clean_seed_script"] == "seed-clean.cypher"
    assert suite.suite.suite == samples.SUITE_ID == "fraud-ring-conformance"


@pytest.mark.parametrize("seed_name", ["seed.cypher", "seed-clean.cypher"])
def test_canonical_seed_uses_fixture_statement_parser(seed_name: str):
    seed_path = samples.MANIFEST_PATH.parent / seed_name

    statements = samples.fixture_split_statements(seed_path.read_text(encoding="utf-8"))
    starts = [statement.lstrip().lower() for statement in statements]

    assert starts[0].startswith("create constraint customer_id")
    assert not any(statement.startswith("compatible") for statement in starts)
    assert not any(statement.startswith("this loop happens to give") for statement in starts)


def test_wrong_fixture_commit_is_rejected():
    def wrong_git(*args, cwd):
        del args, cwd
        return "0" * 40

    with pytest.raises(RuntimeError, match="canonical fixture mismatch"):
        samples.verify_fixture_checkout(git_output=wrong_git)


def test_dirty_fixture_checkout_is_rejected():
    def dirty_git(*args, cwd):
        del cwd
        if args[0] == "status":
            return " M fixtures/fraud-ring/seed.cypher"
        return samples.FIXTURE_COMMIT

    with pytest.raises(RuntimeError, match="fixture checkout must be clean"):
        samples.verify_fixture_checkout(git_output=dirty_git)


def test_wrong_neo4j_version_is_rejected():
    class Result:
        def single(self, *, strict):
            assert strict
            return {"version": "5.26.27"}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def run(self, query):
            assert "dbms.components" in query
            return Result()

    class Driver:
        def session(self, *, database):
            assert database == samples.NEO4J_DATABASE
            return Session()

    with pytest.raises(RuntimeError, match="5.26.28 is required"):
        samples.verify_neo4j_version(Driver())


def test_reset_seed_run_ordering(monkeypatch):
    events = []
    returned = iter((_results("complete"), _results("clean")))
    manifest = {"seed_script": "seed.cypher", "clean_seed_script": "seed-clean.cypher"}

    monkeypatch.setattr(samples, "validate_variant", lambda result, variant: events.append(variant))
    monkeypatch.setattr(samples, "canonicalize_results", lambda result, variant: result)
    monkeypatch.setattr(samples, "render_validated_html_report", lambda result: "<html></html>")
    monkeypatch.setattr(samples, "assert_self_contained_html", lambda html: None)

    samples.generate_variants(
        "driver",
        "profile",
        manifest,
        reset=lambda driver: events.append("reset"),
        seed=lambda driver, path: events.append(f"seed:{path.name}"),
        runner=lambda profile: next(returned),
    )

    assert events == [
        "reset",
        "seed:seed.cypher",
        "findings",
        "reset",
        "seed:seed-clean.cypher",
        "clean",
    ]


def test_report_publication_stages_every_file_before_replacing(tmp_path: Path):
    outputs = {
        "findings": tmp_path / "report-findings.html",
        "clean": tmp_path / "report-clean.html",
    }
    outputs["findings"].write_bytes(b"old-findings")
    outputs["clean"].write_bytes(b"old-clean")
    writes = 0

    def fail_second_write(path: Path, content: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated staging failure")
        path.write_bytes(content)

    with pytest.raises(OSError, match="simulated staging failure"):
        samples.publish_reports(
            {"findings": b"new-findings", "clean": b"new-clean"},
            outputs=outputs,
            writer=fail_second_write,
        )

    assert outputs["findings"].read_bytes() == b"old-findings"
    assert outputs["clean"].read_bytes() == b"old-clean"
    assert not list(tmp_path.glob("*.tmp"))

    samples.publish_reports(
        {"findings": b"new-findings", "clean": b"new-clean"}, outputs=outputs
    )
    assert outputs["findings"].read_bytes() == b"new-findings"
    assert outputs["clean"].read_bytes() == b"new-clean"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("variant", ["findings", "clean"])
def test_committed_sample_is_complete_and_self_contained(variant: str):
    path = samples.OUTPUTS[variant]
    assert path.is_file(), f"missing canonical sample report: {path.relative_to(ROOT)}"
    html = path.read_bytes().decode("utf-8")

    samples.assert_self_contained_html(html)
    assert html.startswith("<!doctype html>\n")
    assert html.endswith("</html>\n")
    assert html.count("<script>") == 1
    assert html.count("<style>") == 1


def test_committed_samples_embed_current_renderer_assets():
    current = render_validated_html_report(
        samples.canonicalize_results(_results("clean"), "clean")
    )

    def embedded_assets(html: str) -> tuple[str, str]:
        style = re.search(r"<style>\n(.*?)\n</style>", html, re.DOTALL)
        script = re.search(r"<script>\n(.*?)\n</script>", html, re.DOTALL)
        assert style is not None and script is not None
        return style.group(1), script.group(1)

    expected = embedded_assets(current)
    for path in samples.OUTPUTS.values():
        assert path.is_file(), f"missing canonical sample report: {path.relative_to(ROOT)}"
        assert embedded_assets(path.read_text(encoding="utf-8")) == expected


def test_findings_artifact_contains_expected_findings():
    path = samples.OUTPUTS["findings"]
    assert path.is_file(), f"missing canonical sample report: {path.relative_to(ROOT)}"
    html = path.read_text(encoding="utf-8")

    assert "account-no-orphans" in html
    assert "account-owner-cardinality" in html
    assert html.count('class="check-card check-fail"') == 2
    assert "3 total" in html
    assert "4 total" in html


def test_clean_artifact_contains_only_passing_results():
    path = samples.OUTPUTS["clean"]
    assert path.is_file(), f"missing canonical sample report: {path.relative_to(ROOT)}"
    html = path.read_text(encoding="utf-8")

    assert "account-no-orphans" in html
    assert "account-owner-cardinality" in html
    assert html.count('class="check-card check-pass"') == 2
    assert 'class="check-card check-fail"' not in html
    assert 'class="check-card check-warn"' not in html
    assert 'class="check-card check-errored"' not in html
    assert 'class="check-card check-skipped"' not in html
