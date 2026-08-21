"""Generate the repository's canonical fraud-ring HTML sample reports.

This is maintainer tooling, not part of the public GraphCheck CLI.  It always
creates its own disposable Neo4j container; it never reads profiles.yml.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import runpy
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml
from neo4j import GraphDatabase

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.results import Results, RunStatus, Verdict
from graphcheck.engine import Engine, EngineConfig, SuiteInput
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.reporting.html import render_validated_html_report
from graphcheck.reporting.writer import load_results

FIXTURE_COMMIT = "5dc14f81dd6f834f166a102b65e9866a240fa035"
NEO4J_VERSION = "5.26.28"
NEO4J_IMAGE = f"neo4j:{NEO4J_VERSION}"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "graphcheck-c5-samples"
NEO4J_DATABASE = "neo4j"
SUITE_ID = "fraud-ring-conformance"
CANONICAL_STARTED_AT = "2026-01-01T00:00:00Z"
CANONICAL_FINISHED_AT = "2026-01-01T00:00:01Z"

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "vendor" / "graphcheck-fraud-ring-fixture"
MANIFEST_PATH = FIXTURE_ROOT / "fixtures" / "fraud-ring" / "manifest.yml"
SUITE_PATH = FIXTURE_ROOT / "examples" / "fraud-ring-conformance.yml"
CYPHER_UTILS_PATH = FIXTURE_ROOT / "tests" / "cypher_utils.py"
OUTPUTS = {
    "findings": ROOT / "docs" / "samples" / "report-findings.html",
    "clean": ROOT / "docs" / "samples" / "report-clean.html",
}


def _git_output(*args: str, cwd: Path = ROOT) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def verify_fixture_checkout(*, git_output: Callable[..., str] = _git_output) -> dict[str, Any]:
    gitlink = git_output("rev-parse", "HEAD:vendor/graphcheck-fraud-ring-fixture", cwd=ROOT)
    if not FIXTURE_ROOT.is_dir() or not (FIXTURE_ROOT / ".git").exists():
        raise RuntimeError(
            "canonical fixture submodule is not initialized; run "
            "`git submodule update --init --recursive`"
        )
    checkout = git_output("rev-parse", "HEAD", cwd=FIXTURE_ROOT)
    if gitlink != FIXTURE_COMMIT or checkout != FIXTURE_COMMIT:
        raise RuntimeError(
            "canonical fixture mismatch: "
            f"expected {FIXTURE_COMMIT}, gitlink={gitlink}, checkout={checkout}"
        )
    dirty = git_output("status", "--porcelain", "--untracked-files=all", cwd=FIXTURE_ROOT)
    if dirty:
        raise RuntimeError(
            "canonical fixture checkout must be clean before generating sample reports"
        )

    required = (MANIFEST_PATH, SUITE_PATH, CYPHER_UTILS_PATH)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"canonical fixture file is missing: {missing[0]}")

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = {
        "neo4j_version": NEO4J_VERSION,
        "requires_empty_database": True,
        "seed_script": "seed.cypher",
        "clean_seed_script": "seed-clean.cypher",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError(
                f"fixture manifest {key!r} must be {value!r}, got {manifest.get(key)!r}"
            )
    for key in ("seed_script", "clean_seed_script"):
        path = MANIFEST_PATH.parent / manifest[key]
        if not path.is_file():
            raise RuntimeError(f"canonical fixture file is missing: {path}")

    suite = SuiteInput.from_yaml(SUITE_PATH.read_text(encoding="utf-8"), source=str(SUITE_PATH))
    if suite.suite.suite != SUITE_ID:
        raise RuntimeError(f"canonical suite must be {SUITE_ID!r}, got {suite.suite.suite!r}")
    return manifest


def verify_neo4j_version(driver: Any) -> None:
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(
            "CALL dbms.components() YIELD versions RETURN versions[0] AS version"
        ).single(strict=True)
    actual = str(record["version"])
    if actual != NEO4J_VERSION:
        raise RuntimeError(f"Neo4j {NEO4J_VERSION} is required, got {actual}")


def reset_database(driver: Any) -> None:
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
        record = session.run(
            "CALL { MATCH (n) RETURN count(n) AS nodes } "
            "CALL { MATCH ()-[r]->() RETURN count(r) AS relationships } "
            "RETURN nodes, relationships"
        ).single(strict=True)
    counts = (int(record["nodes"]), int(record["relationships"]))
    if counts != (0, 0):
        raise RuntimeError(f"temporary database reset failed; remaining counts={counts}")


def fixture_split_statements(text: str) -> list[str]:
    """Parse Cypher with the canonical fixture repository's tested parser."""

    namespace = runpy.run_path(str(CYPHER_UTILS_PATH))
    split_statements = namespace.get("split_statements")
    if not callable(split_statements):
        raise RuntimeError(f"canonical fixture parser is invalid: {CYPHER_UTILS_PATH}")
    return split_statements(text)


def load_seed(driver: Any, path: Path) -> None:
    statements = fixture_split_statements(path.read_text(encoding="utf-8"))
    with driver.session(database=NEO4J_DATABASE) as session:
        for statement in statements:
            session.run(statement).consume()


def run_suite(profile: ConnectionProfile) -> Results:
    suite = SuiteInput.from_yaml(SUITE_PATH.read_text(encoding="utf-8"), source=str(SUITE_PATH))
    client = Neo4jClient(profile, max_concurrency=1)
    try:
        return Engine(client, config=EngineConfig(max_concurrency=1)).run(
            [suite], selection_suites=[SUITE_ID]
        )
    finally:
        client.close()


def validate_variant(results: Results, variant: str) -> None:
    if results.run.run_status is not RunStatus.COMPLETE:
        raise RuntimeError(f"{variant} run was not complete: {results.run.run_status.value}")
    if len(results.checks) != 2 or any(not check.executed for check in results.checks):
        raise RuntimeError(f"{variant} run did not execute both canonical checks")
    if any(check.verdict in {Verdict.ERRORED, Verdict.SKIPPED} for check in results.checks):
        raise RuntimeError(f"{variant} run contained an error or skip")

    by_id = {check.id: check for check in results.checks}
    expected_ids = {"account-no-orphans", "account-owner-cardinality"}
    if set(by_id) != expected_ids:
        raise RuntimeError(f"{variant} run check IDs were {sorted(by_id)}")
    if variant == "findings":
        if any(check.verdict is not Verdict.FAIL for check in results.checks):
            raise RuntimeError("findings run must fail both canonical checks")
        expected_counts = {"account-no-orphans": 3, "account-owner-cardinality": 4}
        actual_counts = {
            check_id: by_id[check_id].evidence.total_count
            if by_id[check_id].evidence is not None
            else None
            for check_id in expected_ids
        }
        if actual_counts != expected_counts:
            raise RuntimeError(
                f"findings run counts must be {expected_counts}, got {actual_counts}"
            )
    elif variant == "clean":
        if results.run.exit_code != 0 or any(
            check.verdict is not Verdict.PASS for check in results.checks
        ):
            raise RuntimeError("clean run must contain only passes and exit zero")
    else:
        raise ValueError(f"unknown sample variant: {variant}")


def canonicalize_results(results: Results, variant: str) -> Results:
    before = copy.deepcopy(results.model_dump(mode="python", by_alias=True, exclude_none=False))
    canonical = load_results(results)
    canonical.run.id = f"canonical-{variant}"
    canonical.run.started_at = CANONICAL_STARTED_AT
    canonical.run.finished_at = CANONICAL_FINISHED_AT
    for check in canonical.checks:
        if check.executed:
            check.started_at = CANONICAL_STARTED_AT
            check.duration_ms = 0
        if check.compiled_query is not None:
            check.compiled_query = "\n".join(
                line.rstrip() for line in check.compiled_query.splitlines()
            )
        if check.evidence is not None:
            counters: dict[str, int] = {}
            for element in check.evidence.elements:
                counters[element.kind] = counters.get(element.kind, 0) + 1
                element.id = f"{element.kind}-{counters[element.kind]:03d}"
    canonical = load_results(canonical)
    after = results.model_dump(mode="python", by_alias=True, exclude_none=False)
    if after != before:
        raise RuntimeError("canonicalization mutated the live Results object")
    return canonical


_PROTOCOL_RELATIVE = re.compile(r"(['\"(])\s*//[A-Za-z0-9]", re.IGNORECASE)


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_resource_attribute = False
        self.has_link_element = False
        self.inline_styles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.has_link_element = self.has_link_element or tag.casefold() == "link"
        for name, value in attrs:
            normalized = name.casefold().rsplit(":", 1)[-1]
            if normalized in {"src", "href", "srcset", "poster", "data"}:
                self.has_resource_attribute = True
            if normalized == "style" and value is not None:
                self.inline_styles.append(value)


def assert_self_contained_html(html: str) -> None:
    lowered = html.lower()
    violations = []
    parser = _ReferenceParser()
    parser.feed(html)
    styles = "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL))
    if "http://" in lowered or "https://" in lowered:
        violations.append("absolute URL")
    if _PROTOCOL_RELATIVE.search(html):
        violations.append("protocol-relative URL")
    if parser.has_resource_attribute:
        violations.append("resource-bearing HTML attribute")
    if re.search(r"@import\b", styles, re.IGNORECASE):
        violations.append("CSS @import")
    if re.search(r"\burl\s*\(", styles, re.IGNORECASE):
        violations.append("CSS url()")
    if any(re.search(r"\burl\s*\(", style, re.IGNORECASE) for style in parser.inline_styles):
        violations.append("inline CSS url()")
    if re.search(r"<script\b[^>]*\bsrc\s*=", html, re.IGNORECASE):
        violations.append("external script")
    if parser.has_link_element:
        violations.append("link element")
    if violations:
        raise RuntimeError("sample HTML is not self-contained: " + ", ".join(violations))
    if html.count("<script>") != 1 or "<style>" not in html:
        raise RuntimeError("sample HTML must contain exactly one inline script and inline CSS")


def generate_variants(
    driver: Any,
    profile: ConnectionProfile,
    manifest: dict[str, Any],
    *,
    reset: Callable[[Any], None] = reset_database,
    seed: Callable[[Any, Path], None] = load_seed,
    runner: Callable[[ConnectionProfile], Results] = run_suite,
) -> dict[str, bytes]:
    rendered: dict[str, bytes] = {}
    for variant, manifest_key in (("findings", "seed_script"), ("clean", "clean_seed_script")):
        reset(driver)
        seed(driver, MANIFEST_PATH.parent / manifest[manifest_key])
        live = runner(profile)
        validate_variant(live, variant)
        canonical = canonicalize_results(live, variant)
        html = render_validated_html_report(canonical)
        assert_self_contained_html(html)
        rendered[variant] = html.encode("utf-8")
    return rendered


def _write_bytes(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def publish_reports(
    rendered: dict[str, bytes],
    *,
    outputs: dict[str, Path] = OUTPUTS,
    writer: Callable[[Path, bytes], None] = _write_bytes,
) -> None:
    """Stage every report before replacing either canonical destination."""

    staged: dict[str, Path] = {}
    try:
        for variant, destination in outputs.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
            # mkstemp returns an open descriptor, which must be closed before replace on Windows.
            os.close(descriptor)
            temporary = Path(temporary_name)
            staged[variant] = temporary
            writer(temporary, rendered[variant])
        for variant, destination in outputs.items():
            staged[variant].replace(destination)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


@contextmanager
def temporary_database() -> Iterator[tuple[Any, ConnectionProfile]]:
    try:
        from docker.errors import DockerException
        from testcontainers.neo4j import Neo4jContainer
    except ImportError as exc:
        raise RuntimeError(
            "install the development dependencies, including testcontainers"
        ) from exc

    try:
        container = Neo4jContainer(NEO4J_IMAGE, password=NEO4J_PASSWORD)
        with container:
            profile = ConnectionProfile(
                uri=container.get_connection_url(),
                user=NEO4J_USER,
                password=NEO4J_PASSWORD,
                database=NEO4J_DATABASE,
            )
            driver = GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password))
            try:
                driver.verify_connectivity()
                yield driver, profile
            finally:
                driver.close()
    except DockerException as exc:
        raise RuntimeError(
            "Docker Engine is required and must be running to generate canonical sample reports"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="compare regenerated bytes without writing files"
    )
    args = parser.parse_args(argv)
    manifest = verify_fixture_checkout()
    with temporary_database() as (driver, profile):
        verify_neo4j_version(driver)
        rendered = generate_variants(driver, profile, manifest)

    mismatches = []
    for variant, path in OUTPUTS.items():
        content = rendered[variant]
        if args.check and (not path.is_file() or path.read_bytes() != content):
            mismatches.append(str(path.relative_to(ROOT)))
    if mismatches:
        raise RuntimeError("canonical sample report differs: " + ", ".join(mismatches))
    if not args.check:
        publish_reports(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
