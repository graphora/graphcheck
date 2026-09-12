"""Configurable GraphRAG model and flat check payloads."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from graphcheck.contracts.scalars import PositiveJsonSchemaInteger
from graphcheck.packs import Identifier, _WithBase, register

GRAPHRAG_CHECK_NAMES = (
    "orphan_chunks",
    "entity_without_provenance",
    "dangling_extraction_relationships",
    "near_duplicate_entities",
)


class GraphRAGModel(_WithBase):
    document_label: Identifier
    chunk_label: Identifier
    entity_label: Identifier
    document_chunk_rel: Identifier
    chunk_entity_rel: Identifier
    embedding_property: Identifier
    name_property: Identifier = "name"
    document_chunk_direction: Literal["out", "in", "any"] = "out"
    chunk_entity_direction: Literal["out", "in", "any"] = "out"
    extraction_rel_types: list[Identifier] | None = Field(default=None, min_length=1)

    @field_validator("extraction_rel_types")
    @classmethod
    def types_are_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("extraction_rel_types must be unique")
        return value


class GraphRAGWith(GraphRAGModel):
    # An unbound check is valid: the project supplies the model, or it is not evaluated.
    document_label: Identifier | None = None
    chunk_label: Identifier | None = None
    entity_label: Identifier | None = None
    document_chunk_rel: Identifier | None = None
    chunk_entity_rel: Identifier | None = None
    embedding_property: Identifier | None = None


class NearDuplicateOptions(_WithBase):
    threshold: float = Field(default=0.9, gt=0, le=1, allow_inf_nan=False)
    sample_size: Annotated[PositiveJsonSchemaInteger, Field(le=2000)] = 1000


@register("orphan_chunks")
class OrphanChunksWith(GraphRAGWith):
    """Missing document/chunk links in either population; multiple parents are allowed."""


@register("entity_without_provenance")
class EntityWithoutProvenanceWith(GraphRAGWith):
    """Entities lacking the configured chunk-to-entity path."""


@register("dangling_extraction_relationships")
class DanglingExtractionRelationshipsWith(GraphRAGWith):
    """Extraction relationships with an endpoint lacking chunk provenance."""


@register("near_duplicate_entities")
class NearDuplicateEntitiesWith(GraphRAGWith, NearDuplicateOptions):
    """Bounded name-based duplicate candidates within one entity label."""
