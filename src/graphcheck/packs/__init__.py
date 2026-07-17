from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

PACK_VERSION = "0.1.0"
REGISTRY: dict[str, type[BaseModel]] = {}
CapabilityRequirement = Literal["read", "show_procedures", "apoc", "count_store"]
PACK_REQUIREMENTS: dict[str, tuple[CapabilityRequirement, ...]] = {}


def register(name: str, *, requires: tuple[CapabilityRequirement, ...] = ("read",)):
    def decorator(cls: type[BaseModel]) -> type[BaseModel]:
        REGISTRY[name] = cls
        PACK_REQUIREMENTS[name] = requires
        return cls

    return decorator


class _WithBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("completeness")
class CompletenessWith(_WithBase):
    label: str
    property: str
    threshold: float = 1.0
