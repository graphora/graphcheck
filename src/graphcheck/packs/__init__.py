from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from graphcheck.contracts.scalars import (
    NonNegativeJsonSchemaInteger,
    PositiveJsonSchemaInteger,
)
from graphcheck.packs.metadata import PACK_VERSION as PACK_VERSION
from graphcheck.packs.metadata import CapabilityRequirement as CapabilityRequirement

REGISTRY: dict[str, type[BaseModel]] = {}
PACK_REQUIREMENTS: dict[str, tuple[CapabilityRequirement, ...]] = {}
Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
RegexPattern = Annotated[str, StringConstraints(min_length=1)]


def register(name: str, *, requires: tuple[CapabilityRequirement, ...] = ("read",)):
    def decorator(cls: type[BaseModel]) -> type[BaseModel]:
        REGISTRY[name] = cls
        PACK_REQUIREMENTS[name] = requires
        return cls

    return decorator


class _WithBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


@register("completeness")
class CompletenessWith(_WithBase):
    label: Identifier
    property: Identifier
    threshold: float = Field(default=1.0, gt=0.0, le=1.0)


@register("cardinality")
class CardinalityWith(_WithBase):
    from_label: Identifier
    rel_type: Identifier
    to_label: Identifier
    direction: Literal["out", "in", "any"] = "out"
    exactly: NonNegativeJsonSchemaInteger = 1


@register("no_orphans")
class NoOrphansWith(_WithBase):
    label: Identifier
    rel_type: Identifier | None = None
    direction: Literal["out", "in", "any"] = "any"


@register("dangling_rels")
class DanglingRelsWith(_WithBase):
    rel_type: Identifier | None = None


@register("property_type")
class PropertyTypeWith(_WithBase):
    label: Identifier
    property: Identifier
    type: Literal["string", "integer", "float", "boolean", "date", "datetime"]


@register("property_format")
class PropertyFormatWith(_WithBase):
    label: Identifier
    property: Identifier
    regex: RegexPattern = Field(json_schema_extra={"format": "regex"})

    @field_validator("regex")
    @classmethod
    def regex_must_compile(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        return value


@register("value_in_set")
class ValueInSetWith(_WithBase):
    label: Identifier
    property: Identifier
    values: list[str | int | float | bool] = Field(min_length=1)


@register("uniqueness")
class UniquenessWith(_WithBase):
    label: Identifier
    property: Identifier


@register("hub_outlier")
class HubOutlierWith(_WithBase):
    label: Identifier
    rel_type: Identifier | None = None
    direction: Literal["out", "in", "any"] = "any"
    z_threshold: float = Field(default=3.0, gt=0.0)
    sample_size: PositiveJsonSchemaInteger | None = None


@register("label_cooccurrence")
class LabelCooccurrenceWith(_WithBase):
    label_a: Identifier
    label_b: Identifier

    @model_validator(mode="after")
    def labels_must_differ(self) -> LabelCooccurrenceWith:
        if self.label_a == self.label_b:
            raise ValueError("label_cooccurrence requires two distinct labels")
        return self


@register("rel_direction")
class RelDirectionWith(_WithBase):
    from_label: Identifier
    rel_type: Identifier
    to_label: Identifier

    @model_validator(mode="after")
    def endpoint_labels_must_differ(self) -> RelDirectionWith:
        if self.from_label == self.to_label:
            raise ValueError("rel_direction requires distinct endpoint labels")
        return self


@register("temporal_sanity")
class TemporalSanityWith(_WithBase):
    label: Identifier
    start_property: Identifier
    end_property: Identifier

    @model_validator(mode="after")
    def properties_must_differ(self) -> TemporalSanityWith:
        if self.start_property == self.end_property:
            raise ValueError("temporal_sanity requires distinct start and end properties")
        return self


class _PiiWithBase(_WithBase):
    label: Identifier | None = None
    sample_size: PositiveJsonSchemaInteger | None = None


@register("pii_name_match")
class PiiNameMatchWith(_PiiWithBase):
    """Sample property occurrences and match property-key aliases from the PII pack."""

    patterns: (
        list[
            Literal[
                "ssn",
                "dob",
                "email",
                "phone",
                "nric",
                "aadhaar",
                "address",
                "passport",
                "credit_card",
                "tax_id",
                "driver_license",
                "bank_account",
                "ip_address",
                "geolocation",
                "biometric",
            ]
        ]
        | None
    ) = Field(default=None, min_length=1)

    @field_validator("patterns")
    @classmethod
    def patterns_must_be_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("PII patterns must not contain duplicate entries")
        return value


@register("pii_value_match")
class PiiValueMatchWith(_PiiWithBase):
    """Sample string property values and apply the PII regex/checksum catalog."""

    patterns: list[Literal["email", "e164_phone", "nric", "aadhaar", "credit_card"]] | None = Field(
        default=None, min_length=1
    )
    properties: list[Identifier] | None = Field(default=None, min_length=1)

    @field_validator("patterns")
    @classmethod
    def patterns_must_be_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("PII patterns must not contain duplicate entries")
        return value

    @field_validator("properties")
    @classmethod
    def properties_must_be_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("PII properties must not contain duplicate entries")
        return value
