from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from graphcheck.packs.metadata import (
    CapabilityRequirement,
    CorePackMetadata,
    load_pack_metadata_yaml,
)

PACKS_DIRECTORY = Path(__file__).resolve().parent


class PackCatalogError(ValueError):
    """A pack metadata catalog could not be loaded safely."""


def pack_metadata_paths(directory: Path | None = None) -> list[Path]:
    """Return supported pack metadata files in deterministic order."""
    directory = PACKS_DIRECTORY if directory is None else directory
    return sorted({*directory.glob("*.yml"), *directory.glob("*.yaml")})


def load_pack_requirements(
    paths: Iterable[Path] | None = None,
) -> dict[str, tuple[CapabilityRequirement, ...]]:
    """Load conformance capability requirements from validated pack YAML."""
    selected_paths = list(paths) if paths is not None else pack_metadata_paths()
    if not selected_paths:
        raise PackCatalogError("no pack metadata .yml or .yaml files were found")

    requirements: dict[str, tuple[CapabilityRequirement, ...]] = {}
    core_pack_found = False
    for path in selected_paths:
        try:
            metadata = load_pack_metadata_yaml(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise PackCatalogError(f"could not load pack metadata {path}: {exc}") from exc

        if not isinstance(metadata, CorePackMetadata):
            continue

        core_pack_found = True
        for check_name, check_metadata in metadata.checks.items():
            if check_name in requirements:
                raise PackCatalogError(
                    f"check {check_name!r} is declared by more than one core pack metadata file"
                )
            requirements[check_name] = tuple(check_metadata.requires)

    if not core_pack_found:
        raise PackCatalogError("pack metadata catalog does not contain a core pack")
    return requirements
