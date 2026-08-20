from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from graphcheck.contracts.check import (
    DuplicateKeyError,
    SuiteValidationError,
    UnknownCheckError,
    load_suite,
)
from graphcheck.contracts.results import Capabilities
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import BlockedCheck, Visibility
from graphcheck.packs import CapabilityRequirement
from graphcheck.packs.catalog import PackCatalogError, load_pack_requirements
from graphcheck.project import load_project_config

APOC_INSTALL_FIX = (
    "Install APOC for this Neo4j DBMS, restart Neo4j, then run `graphcheck debug` again."
)

SUPPORTED_REQUIREMENTS: tuple[CapabilityRequirement, ...] = (
    "read",
    "show_procedures",
    "apoc",
    "count_store",
    "store_consistency",
)


@dataclass(frozen=True)
class CapabilityContext:
    read: bool
    show_procedures: bool
    apoc: bool
    count_store: bool
    store_consistency: bool = False

    @classmethod
    def from_probe(cls, capabilities: Capabilities, visibility: Visibility) -> CapabilityContext:
        return cls(
            read=visibility.can_read,
            show_procedures=visibility.can_show_procedures,
            apoc=capabilities.apoc,
            count_store=capabilities.count_store,
            # Cypher cannot observe corrupt relationship endpoint records. A future C2 store
            # consistency adapter can set this explicitly without changing SPEC-01 capabilities.
            store_consistency=False,
        )


def blocked_checks_for_project(
    root: Path,
    context: CapabilityContext,
    *,
    pack_paths: Iterable[Path] | None = None,
) -> list[BlockedCheck]:
    """Report checks that cannot run because a probed database capability is missing."""
    config = load_project_config(root)
    checks_dir = root / config.checks
    if not checks_dir.exists():
        return []

    try:
        pack_requirements = load_pack_requirements(pack_paths)
    except PackCatalogError as exc:
        raise GraphCheckError(
            "packs.invalid",
            f"Could not load check pack capability metadata: {exc}",
            "Fix the check pack YAML, then run `graphcheck debug` again.",
        ) from exc

    blocked: list[BlockedCheck] = []
    for path in _suite_paths(checks_dir):
        try:
            suite = load_suite(path.read_text(encoding="utf-8"), source=str(path))
        except (
            OSError,
            yaml.YAMLError,
            ValidationError,
            DuplicateKeyError,
            UnknownCheckError,
            SuiteValidationError,
        ) as exc:
            raise GraphCheckError(
                "checks.invalid",
                f"Could not load check suite {path}: {exc}",
                "Fix the check YAML, then run `graphcheck debug` again.",
            ) from exc
        for check in suite.checks:
            if check.generated:
                continue
            if check.pattern.value != "conformance":
                continue
            requirements = pack_requirements.get(check.spec.check)
            if requirements is None:
                raise GraphCheckError(
                    "packs.requirements_missing",
                    f"Check {check.spec.check!r} has no capability declaration in pack metadata.",
                    "Add the check to its pack YAML with a non-empty `requires` list.",
                )
            for capability in requirements:
                if _capability_present(context, capability):
                    continue
                blocked.append(
                    BlockedCheck(
                        suite=suite.suite,
                        check_id=check.id,
                        check=check.spec.check,
                        missing_capability=capability,
                        fix=_capability_fix(context, capability),
                    )
                )
    return blocked


def _suite_paths(checks_dir: Path) -> list[Path]:
    return sorted({*checks_dir.glob("*.yml"), *checks_dir.glob("*.yaml")})


def _capability_present(context: CapabilityContext, capability: CapabilityRequirement) -> bool:
    if capability not in SUPPORTED_REQUIREMENTS:
        raise RuntimeError(f"unsupported pack capability requirement: {capability!r}")
    return bool(getattr(context, capability))


def _capability_fix(context: CapabilityContext, capability: CapabilityRequirement) -> str:
    if capability == "apoc":
        if not context.show_procedures:
            return (
                "Grant procedure visibility and execution to the configured Neo4j user; "
                "if APOC is not installed, install it and restart Neo4j, then run "
                "`graphcheck debug` again."
            )
        return APOC_INSTALL_FIX
    if capability == "read":
        return "Grant read access to the configured Neo4j user, then run `graphcheck debug` again."
    if capability == "show_procedures":
        return (
            "Grant procedure visibility to the configured Neo4j user, "
            "then run `graphcheck debug` again."
        )
    if capability == "count_store":
        return "Enable count-store support for this check, then run `graphcheck debug` again."
    if capability == "store_consistency":
        return (
            "Run Neo4j's offline consistency checker or install a connector that exposes a "
            "read-only relationship-store consistency probe."
        )
    return f"Enable the `{capability}` capability, then run `graphcheck debug` again."
