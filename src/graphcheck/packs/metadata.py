from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from graphcheck.yaml_loader import load_yaml_mapping

PACK_VERSION = "0.1.0"

type NonWhitespaceString = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]


def _require_boolean(value: object) -> object:
    if type(value) is not bool:
        raise ValueError("value must be a boolean")
    return value


type StrictTrueLiteral = Annotated[Literal[True], BeforeValidator(_require_boolean)]

type CoreCheckName = Literal[
    "completeness",
    "cardinality",
    "no_orphans",
    "dangling_rels",
    "property_type",
    "property_format",
    "value_in_set",
    "uniqueness",
    "hub_outlier",
    "label_cooccurrence",
    "rel_direction",
    "temporal_sanity",
]
CORE_CHECK_NAMES: tuple[CoreCheckName, ...] = (
    "completeness",
    "cardinality",
    "no_orphans",
    "dangling_rels",
    "property_type",
    "property_format",
    "value_in_set",
    "uniqueness",
    "hub_outlier",
    "label_cooccurrence",
    "rel_direction",
    "temporal_sanity",
)

type CapabilityRequirement = Literal["read", "show_procedures", "apoc", "count_store"]
type EvidenceKind = Literal["node", "rel"]
type PiiCheckName = Literal["pii_name_match", "pii_value_match"]


class _StrictMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceMetadata(_StrictMetadata):
    elements: list[EvidenceKind] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    id_fields: list[NonWhitespaceString] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("elements", "id_fields")
    @classmethod
    def entries_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("metadata lists must not contain duplicate entries")
        return value


class EstimateMetadata(_StrictMetadata):
    required_when_sampled: StrictTrueLiteral


class _CoreCheckMetadataBase(_StrictMetadata):
    catches: NonWhitespaceString
    does_not_catch: NonWhitespaceString
    requires: list[CapabilityRequirement] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    evidence: EvidenceMetadata
    template: CoreCheckName

    @model_validator(mode="before")
    @classmethod
    def sampled_must_be_boolean(cls, value: object) -> object:
        if isinstance(value, Mapping) and "sampled" in value and type(value["sampled"]) is not bool:
            raise ValueError("sampled must be a boolean")
        return value

    @field_validator("requires")
    @classmethod
    def capabilities_must_be_unique(
        cls, value: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        if len(value) != len(set(value)):
            raise ValueError("requires must not contain duplicate capabilities")
        return value


class UnsampledCoreCheckMetadata(_CoreCheckMetadataBase):
    sampled: Literal[False]


class SampledCoreCheckMetadata(_CoreCheckMetadataBase):
    sampled: Literal[True]
    estimate: EstimateMetadata


type CoreCheckMetadata = Annotated[
    UnsampledCoreCheckMetadata | SampledCoreCheckMetadata,
    Field(discriminator="sampled"),
]


class CoreChecksMetadata(_StrictMetadata):
    completeness: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "completeness"}}}
    )
    cardinality: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "cardinality"}}}
    )
    no_orphans: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "no_orphans"}}}
    )
    dangling_rels: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "dangling_rels"}}}
    )
    property_type: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "property_type"}}}
    )
    property_format: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "property_format"}}}
    )
    value_in_set: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "value_in_set"}}}
    )
    uniqueness: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "uniqueness"}}}
    )
    hub_outlier: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "hub_outlier"}}}
    )
    label_cooccurrence: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "label_cooccurrence"}}}
    )
    rel_direction: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "rel_direction"}}}
    )
    temporal_sanity: CoreCheckMetadata = Field(
        json_schema_extra={"properties": {"template": {"const": "temporal_sanity"}}}
    )

    @model_validator(mode="after")
    def templates_must_match_check_names(self) -> CoreChecksMetadata:
        for name in CORE_CHECK_NAMES:
            metadata = getattr(self, name)
            if metadata.template != name:
                raise ValueError(
                    f"core check {name!r} must declare matching template {name!r}, "
                    f"not {metadata.template!r}"
                )
        return self

    def items(self):
        for name in CORE_CHECK_NAMES:
            yield name, getattr(self, name)


class CorePackMetadata(_StrictMetadata):
    pack: Literal["core"]
    version: Literal[PACK_VERSION]
    checks: CoreChecksMetadata


class _PiiCheckMetadataBase(_StrictMetadata):
    catches: NonWhitespaceString
    does_not_catch: NonWhitespaceString
    requires: list[CapabilityRequirement] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    sampled: StrictTrueLiteral
    estimate: EstimateMetadata
    evidence: EvidenceMetadata
    template: PiiCheckName

    @field_validator("requires")
    @classmethod
    def capabilities_must_be_unique(
        cls, value: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        if len(value) != len(set(value)):
            raise ValueError("requires must not contain duplicate capabilities")
        return value


class PiiChecksMetadata(_StrictMetadata):
    pii_name_match: _PiiCheckMetadataBase = Field(
        json_schema_extra={"properties": {"template": {"const": "pii_name_match"}}}
    )
    pii_value_match: _PiiCheckMetadataBase = Field(
        json_schema_extra={"properties": {"template": {"const": "pii_value_match"}}}
    )

    @model_validator(mode="after")
    def templates_must_match_check_names(self) -> PiiChecksMetadata:
        for name in ("pii_name_match", "pii_value_match"):
            metadata = getattr(self, name)
            if metadata.template != name:
                raise ValueError(
                    f"PII check {name!r} must declare matching template {name!r}, "
                    f"not {metadata.template!r}"
                )
        return self

    def items(self):
        for name in ("pii_name_match", "pii_value_match"):
            yield name, getattr(self, name)


class PiiNamePatternMetadata(_StrictMetadata):
    keys: list[NonWhitespaceString] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("keys")
    @classmethod
    def keys_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("PII name-match keys must be unique within a pattern")
        return value


class PiiNameMatchMetadata(_StrictMetadata):
    confidence: Literal["name-match"]
    patterns: dict[NonWhitespaceString, PiiNamePatternMetadata] = Field(min_length=1)


type ReportField = Literal["location", "exposure_count", "confidence"]


class PiiReportMetadata(_StrictMetadata):
    fields: list[ReportField] = Field(
        min_length=3,
        max_length=3,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("fields")
    @classmethod
    def report_fields_are_frozen(cls, value: list[ReportField]) -> list[ReportField]:
        expected = {"location", "exposure_count", "confidence"}
        if set(value) != expected:
            raise ValueError(f"PII report fields must be exactly {sorted(expected)!r}")
        return value


class PiiValuePatternMetadata(_StrictMetadata):
    regex: NonWhitespaceString = Field(json_schema_extra={"format": "regex"})
    checksum: Literal["luhn", "verhoeff"] | None = None

    @field_validator("regex")
    @classmethod
    def regex_must_compile(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid PII value-match regex: {exc}") from exc
        return value


class PiiValueMatchMetadata(_StrictMetadata):
    confidence: Literal["value-match"]
    sample_required: StrictTrueLiteral
    report: PiiReportMetadata
    patterns: dict[NonWhitespaceString, PiiValuePatternMetadata] = Field(min_length=1)


class PiiPackMetadata(_StrictMetadata):
    pack: Literal["pii"]
    version: Literal[PACK_VERSION]
    completeness_notice: NonWhitespaceString
    checks: PiiChecksMetadata
    name_match: PiiNameMatchMetadata
    value_match: PiiValueMatchMetadata


type PackMetadata = Annotated[
    CorePackMetadata | PiiPackMetadata,
    Field(discriminator="pack"),
]

_PACK_METADATA_ADAPTER = TypeAdapter(PackMetadata)


def load_pack_metadata_yaml(text: str) -> CorePackMetadata | PiiPackMetadata:
    """Parse and type-check pack YAML without silently overwriting duplicate keys."""
    raw = load_yaml_mapping(text, description="pack metadata")
    return _PACK_METADATA_ADAPTER.validate_python(raw)
