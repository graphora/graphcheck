from typing import Annotated

from pydantic import BeforeValidator, Field


def _normalize_json_schema_integer(value: object) -> object:
    """Match Draft 2020-12 integer semantics without general coercion."""
    if type(value) is float and value.is_integer():
        return int(value)
    return value


JsonSchemaInteger = Annotated[int, BeforeValidator(_normalize_json_schema_integer)]
NonNegativeJsonSchemaInteger = Annotated[
    int,
    Field(ge=0),
    BeforeValidator(_normalize_json_schema_integer),
]
PositiveJsonSchemaInteger = Annotated[
    int,
    Field(gt=0),
    BeforeValidator(_normalize_json_schema_integer),
]
