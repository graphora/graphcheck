from __future__ import annotations

import unicodedata


def cypher_identifier(identifier: str) -> str:
    """Return one validated identifier escaped for a Cypher grammar position."""
    if not isinstance(identifier, str) or not identifier or not identifier.strip():
        raise ValueError("a Cypher identifier must be a non-blank string")
    if any(unicodedata.category(character) == "Cc" for character in identifier):
        raise ValueError("a Cypher identifier cannot contain control characters")
    return f"`{identifier.replace('`', '``')}`"


def node_pattern(variable: str, label: str | None = None) -> str:
    return f"({variable}{f':{cypher_identifier(label)}' if label is not None else ''})"


def relationship_pattern(variable: str, relationship_type: str | None = None) -> str:
    token = f":{cypher_identifier(relationship_type)}" if relationship_type is not None else ""
    return f"[{variable}{token}]"


def property_access(variable: str, property_name: str) -> str:
    return f"{variable}.{cypher_identifier(property_name)}"
