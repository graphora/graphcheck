from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from graphcheck.baselines import list_baselines
from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_DOCUMENT_TOTAL_BYTES = 1024 * 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GenerateProperty(_Strict):
    name: str
    type: str


class GenerateDegreeDistribution(_Strict):
    median: float
    p95: float
    p99: float
    maximum: int


class GenerateLabel(_Strict):
    name: str
    count: int
    properties: list[GenerateProperty]
    degree_distribution: GenerateDegreeDistribution | None


class GenerateRelationshipType(_Strict):
    name: str
    count: int


class GenerateConstraint(_Strict):
    type: str
    labels_or_types: list[str]
    properties: list[str]


class GenerateIndex(_Strict):
    type: str
    labels_or_types: list[str]
    properties: list[str]


class GenerateCoverage(_Strict):
    owner: Literal["node", "relationship"]
    owner_name: str
    property: str
    coverage: float


class GenerateProfileContext(_Strict):
    transmission_version: Literal["1.0"] = "1.0"
    profile_status: Literal["complete", "partial"]
    labels: list[GenerateLabel]
    relationship_types: list[GenerateRelationshipType]
    constraints: list[GenerateConstraint]
    indexes: list[GenerateIndex]
    node_count: int
    relationship_count: int
    property_coverage: list[GenerateCoverage]


class GenerateDocument(_Strict):
    ordinal: int
    name: str
    content: str


class GenerateRequest(_Strict):
    profile: GenerateProfileContext
    documents: list[GenerateDocument]
    requested_count: int


@dataclass(frozen=True)
class LoadedDocument:
    """Provider-safe document plus local-only disclosure metadata."""

    document: GenerateDocument
    local_path: Path
    display_path: str
    byte_count: int


def build_profile_context(baseline: BaselineProfile) -> GenerateProfileContext:
    """Build the versioned positive-selection egress boundary field by field."""

    labels = [
        GenerateLabel(
            name=label.name,
            count=label.count,
            properties=[
                GenerateProperty(name=prop.name, type=prop.type) for prop in label.properties
            ],
            degree_distribution=(
                None
                if label.degree_distribution is None
                else GenerateDegreeDistribution(
                    median=label.degree_distribution.median,
                    p95=label.degree_distribution.p95,
                    p99=label.degree_distribution.p99,
                    maximum=label.degree_distribution.maximum,
                )
            ),
        )
        for label in baseline.graph_schema.labels
    ]
    relationship_types = [
        GenerateRelationshipType(name=relationship.name, count=relationship.count)
        for relationship in baseline.graph_schema.relationship_types
    ]
    constraints = [
        GenerateConstraint(
            type=constraint.type,
            labels_or_types=list(constraint.labels_or_types),
            properties=list(constraint.properties),
        )
        for constraint in baseline.graph_schema.constraints
    ]
    indexes = [
        GenerateIndex(
            type=index.type,
            labels_or_types=list(index.labels_or_types),
            properties=list(index.properties),
        )
        for index in baseline.graph_schema.indexes
    ]
    property_coverage = [
        GenerateCoverage(
            owner=coverage.owner,
            owner_name=coverage.owner_name,
            property=coverage.property,
            coverage=coverage.coverage,
        )
        for coverage in baseline.statistics.property_coverage
    ]
    return GenerateProfileContext(
        profile_status=baseline.status.value,
        labels=labels,
        relationship_types=relationship_types,
        constraints=constraints,
        indexes=indexes,
        node_count=baseline.statistics.node_count,
        relationship_count=baseline.statistics.relationship_count,
        property_coverage=property_coverage,
    )


def display_path(path: Path, project_root: Path) -> str:
    """Use a project-relative path when possible and an absolute path otherwise."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def read_documents(
    paths: list[Path] | None,
    *,
    project_root: Path,
    invocation_dir: Path | None = None,
) -> list[LoadedDocument]:
    """Read only explicitly named regular UTF-8 files, without truncation."""

    base = Path.cwd() if invocation_dir is None else invocation_dir
    loaded: list[LoadedDocument] = []
    total = 0
    for ordinal, supplied in enumerate(paths or [], start=1):
        local_path = supplied if supplied.is_absolute() else base / supplied
        try:
            exists = local_path.exists()
            regular = local_path.is_file()
        except OSError as exc:
            raise GraphCheckError(
                "generate.doc_invalid",
                f"Document could not be inspected: {supplied}",
                "Pass a readable UTF-8 text file.",
            ) from exc
        if not exists:
            raise GraphCheckError(
                "generate.doc_not_found",
                f"Document was not found: {supplied}",
                "Correct or remove the named `--docs` path.",
            )
        if not regular:
            raise GraphCheckError(
                "generate.doc_invalid",
                f"Document is not a regular UTF-8 file: {supplied}",
                "Pass a readable UTF-8 text file.",
            )
        try:
            with local_path.open("rb") as document_file:
                raw = document_file.read(MAX_DOCUMENT_BYTES + 1)
        except OSError as exc:
            raise GraphCheckError(
                "generate.doc_invalid",
                f"Document could not be read: {supplied}",
                "Pass a readable UTF-8 text file.",
            ) from exc
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise GraphCheckError(
                "generate.doc_too_large",
                f"Document exceeds the 256 KiB limit: {supplied}",
                "Reduce the named file below 256 KiB and the total below 1 MiB.",
            )
        total += len(raw)
        if total > MAX_DOCUMENT_TOTAL_BYTES:
            raise GraphCheckError(
                "generate.doc_too_large",
                "The supplied documents exceed the 1 MiB aggregate limit.",
                "Reduce the named files so their total is below 1 MiB.",
            )
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GraphCheckError(
                "generate.doc_invalid",
                f"Document is not valid UTF-8: {supplied}",
                "Pass a readable UTF-8 text file.",
            ) from exc
        resolved = local_path.resolve()
        loaded.append(
            LoadedDocument(
                document=GenerateDocument(
                    ordinal=ordinal,
                    name=local_path.name,
                    content=content,
                ),
                local_path=resolved,
                display_path=display_path(resolved, project_root),
                byte_count=len(raw),
            )
        )
    return loaded


def load_generation_baseline(
    *,
    project_root: Path,
    artifacts: str | Path,
    requested: Path | None,
) -> tuple[Path, BaselineProfile]:
    """Resolve and validate an explicit baseline or the latest valid snapshot."""

    if requested is not None:
        path = requested if requested.is_absolute() else project_root / requested
        try:
            is_file = path.is_file()
        except OSError as exc:
            raise GraphCheckError(
                "generate.baseline_not_found",
                f"Baseline file could not be inspected: {path}",
                "Pass an existing baseline JSON path.",
            ) from exc
        if not is_file:
            raise GraphCheckError(
                "generate.baseline_not_found",
                f"Baseline file was not found: {path}",
                "Pass an existing baseline JSON path.",
            )
        return path, _read_baseline(path)

    try:
        paths = list_baselines(project_root, artifacts)
    except OSError as exc:
        raise GraphCheckError(
            "generate.baseline_invalid",
            "The configured baseline directory could not be read.",
            "Regenerate it with `graphcheck profile`, or pass a valid C4 baseline.",
        ) from exc
    if not paths:
        raise GraphCheckError(
            "generate.baseline_missing",
            "No timestamped baseline snapshots were found.",
            "Run `graphcheck profile`, or pass `--from <baseline.json>`.",
        )
    latest_error: GraphCheckError | None = None
    for path in reversed(paths):
        try:
            return path, _read_baseline(path)
        except GraphCheckError as exc:
            latest_error = exc
    assert latest_error is not None
    raise latest_error


def _read_baseline(path: Path) -> BaselineProfile:
    try:
        raw = path.read_bytes()
        return BaselineProfile.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        raise GraphCheckError(
            "generate.baseline_invalid",
            f"Baseline is not a valid C4 profile: {path}",
            "Regenerate it with `graphcheck profile`, or pass a valid C4 baseline.",
        ) from exc
