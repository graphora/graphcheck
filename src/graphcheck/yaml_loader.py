from __future__ import annotations

import yaml


class DuplicateKeyError(ValueError):
    """A mapping key appeared more than once in a YAML document."""


class _NoDuplicatesLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(f"duplicate key {key!r} at {key_node.start_mark}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicatesLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_yaml_mapping(text: str, *, description: str) -> dict:
    """Safely load one YAML mapping while rejecting duplicate keys at every depth."""
    # SAFETY: _NoDuplicatesLoader subclasses SafeLoader, so it never constructs
    # arbitrary Python objects. The custom constructor only adds duplicate-key checks.
    data = yaml.load(text, Loader=_NoDuplicatesLoader)  # noqa: S506 (SafeLoader subclass)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{description} must be a mapping at the top level")
    return data
