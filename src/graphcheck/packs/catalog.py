from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from graphcheck.packs.metadata import (
    CapabilityRequirement,
    CorePackMetadata,
    PiiPackMetadata,
    load_pack_metadata_yaml,
)

PACKS_DIRECTORY = Path(__file__).resolve().parent


class PackCatalogError(ValueError):
    """A pack metadata catalog could not be loaded safely."""


@dataclass(frozen=True)
class PackCheckDefinition:
    pack: str
    name: str
    template: str
    requires: tuple[CapabilityRequirement, ...]
    sampled: bool
    evidence_elements: tuple[str, ...]
    evidence_id_fields: tuple[str, ...]


@dataclass(frozen=True)
class PackCatalog:
    checks: dict[str, PackCheckDefinition]
    pii: PiiPackMetadata | None


def pack_metadata_paths(directory: Path | None = None) -> list[Path]:
    """Return supported pack metadata files in deterministic order."""
    directory = PACKS_DIRECTORY if directory is None else directory
    return sorted({*directory.glob("*.yml"), *directory.glob("*.yaml")})


def load_pack_catalog(
    paths: Iterable[Path] | None = None,
) -> PackCatalog:
    """Load executable check definitions from validated pack YAML."""
    selected_paths = list(paths) if paths is not None else pack_metadata_paths()
    if not selected_paths:
        raise PackCatalogError("no pack metadata .yml or .yaml files were found")

    checks: dict[str, PackCheckDefinition] = {}
    core_pack_found = False
    pii_metadata: PiiPackMetadata | None = None
    for path in selected_paths:
        try:
            metadata = load_pack_metadata_yaml(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise PackCatalogError(f"could not load pack metadata {path}: {exc}") from exc

        if isinstance(metadata, CorePackMetadata):
            core_pack_found = True
            pack_checks = metadata.checks.items()
        elif isinstance(metadata, PiiPackMetadata):
            if pii_metadata is not None:
                raise PackCatalogError("more than one PII pack metadata file was found")
            pii_metadata = metadata
            pack_checks = metadata.checks.items()
        else:  # pragma: no cover - the discriminated metadata adapter is exhaustive
            continue

        for check_name, check_metadata in pack_checks:
            if check_name in checks:
                raise PackCatalogError(
                    f"check {check_name!r} is declared by more than one pack metadata file"
                )
            checks[check_name] = PackCheckDefinition(
                pack=metadata.pack,
                name=check_name,
                template=check_metadata.template,
                requires=tuple(check_metadata.requires),
                sampled=check_metadata.sampled,
                evidence_elements=tuple(check_metadata.evidence.elements),
                evidence_id_fields=tuple(check_metadata.evidence.id_fields),
            )

    if not core_pack_found:
        raise PackCatalogError("pack metadata catalog does not contain a core pack")
    return PackCatalog(checks=checks, pii=pii_metadata)


def load_pack_requirements(
    paths: Iterable[Path] | None = None,
) -> dict[str, tuple[CapabilityRequirement, ...]]:
    """Load conformance capability requirements from validated pack YAML."""
    catalog = load_pack_catalog(paths)
    return {name: definition.requires for name, definition in catalog.checks.items()}


@lru_cache(maxsize=1)
def builtin_pack_catalog() -> PackCatalog:
    """Load the immutable installed pack catalog once for engine compilation."""
    return load_pack_catalog()
