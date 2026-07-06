from __future__ import annotations

from pydantic import BaseModel, ConfigDict

PACK_VERSION = "0.1.0"
REGISTRY: dict[str, type[BaseModel]] = {}


def register(name: str):
    def decorator(cls: type[BaseModel]) -> type[BaseModel]:
        REGISTRY[name] = cls
        return cls

    return decorator


class _WithBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("completeness")
class CompletenessWith(_WithBase):
    label: str
    property: str
    threshold: float = 1.0
