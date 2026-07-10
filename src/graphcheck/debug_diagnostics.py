from __future__ import annotations

from pathlib import Path

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import Capabilities
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import BlockedCheck
from graphcheck.project import load_project_config

APOC_INSTALL_FIX = (
    "Install APOC for this Neo4j DBMS, restart Neo4j, then run `graphcheck debug` again."
)

CHECK_CAPABILITY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "completeness": (),
}


def blocked_checks_for_project(root: Path, capabilities: Capabilities) -> list[BlockedCheck]:
    """Report checks that cannot run because a probed database capability is missing."""
    config = load_project_config(root)
    checks_dir = root / config.checks
    if not checks_dir.exists():
        return []

    blocked: list[BlockedCheck] = []
    for path in sorted(checks_dir.glob("*.yml")):
        try:
            suite = load_suite(path.read_text(encoding="utf-8"), source=str(path))
        except Exception as exc:
            raise GraphCheckError(
                "checks.invalid",
                f"Could not load check suite {path}: {exc}",
                "Fix the check YAML, then run `graphcheck debug` again.",
            ) from exc
        for check in suite.checks:
            if check.pattern.value != "conformance":
                continue
            requirements = CHECK_CAPABILITY_REQUIREMENTS.get(check.spec.check, ())
            for capability in requirements:
                if _capability_present(capabilities, capability):
                    continue
                blocked.append(
                    BlockedCheck(
                        suite=suite.suite,
                        check_id=check.id,
                        check=check.spec.check,
                        missing_capability=capability,
                        fix=_capability_fix(capability),
                    )
                )
    return blocked


def _capability_present(capabilities: Capabilities, capability: str) -> bool:
    return bool(getattr(capabilities, capability, False))


def _capability_fix(capability: str) -> str:
    if capability == "apoc":
        return APOC_INSTALL_FIX
    return f"Enable the `{capability}` capability, then run `graphcheck debug` again."
