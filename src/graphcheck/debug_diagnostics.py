from __future__ import annotations

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
from graphcheck.packs import PACK_REQUIREMENTS, CapabilityRequirement
from graphcheck.project import load_project_config

APOC_INSTALL_FIX = (
    "Install APOC for this Neo4j DBMS, restart Neo4j, then run `graphcheck debug` again."
)

SUPPORTED_REQUIREMENTS: tuple[CapabilityRequirement, ...] = (
    "read",
    "show_procedures",
    "apoc",
    "count_store",
)


@dataclass(frozen=True)
class CapabilityContext:
    read: bool
    show_procedures: bool
    apoc: bool
    count_store: bool

    @classmethod
    def from_probe(cls, capabilities: Capabilities, visibility: Visibility) -> CapabilityContext:
        return cls(
            read=visibility.can_read,
            show_procedures=visibility.can_show_procedures,
            apoc=capabilities.apoc,
            count_store=capabilities.count_store,
        )


def blocked_checks_for_project(root: Path, context: CapabilityContext) -> list[BlockedCheck]:
    """Report checks that cannot run because a probed database capability is missing."""
    config = load_project_config(root)
    checks_dir = root / config.checks
    if not checks_dir.exists():
        return []

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
            requirements = PACK_REQUIREMENTS.get(check.spec.check, ())
            for capability in requirements:
                if _capability_present(context, capability):
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


def _suite_paths(checks_dir: Path) -> list[Path]:
    return sorted({*checks_dir.glob("*.yml"), *checks_dir.glob("*.yaml")})


def _capability_present(context: CapabilityContext, capability: CapabilityRequirement) -> bool:
    if capability not in SUPPORTED_REQUIREMENTS:
        raise RuntimeError(f"unsupported pack capability requirement: {capability!r}")
    return bool(getattr(context, capability))


def _capability_fix(capability: CapabilityRequirement) -> str:
    if capability == "apoc":
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
    return f"Enable the `{capability}` capability, then run `graphcheck debug` again."
