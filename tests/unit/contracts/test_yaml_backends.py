import runpy
from pathlib import Path

import pytest
import yaml

from graphcheck import yaml_loader


@pytest.fixture(params=[yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)])
def backend(request, monkeypatch):
    class StrictLoader(request.param):
        pass

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_loader._construct_mapping
    )
    monkeypatch.setattr(yaml_loader, "_NoDuplicatesLoader", StrictLoader)


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2",
        "a: {b: 1, b: 2}",
        "a: !!python/object:builtins.object {}",
        "[a, b]",
        "a: [",
        "a: 1\n---\nb: 2",
        "? [a,b]\n: 1",
        "a: &a {b: 1}\nc: {<<: *a}",
    ],
)
def test_both_safe_backends_reject_invalid_input(backend, text):
    with pytest.raises((ValueError, TypeError, yaml.YAMLError)):
        yaml_loader.load_yaml_mapping(text, description="test")


def test_both_backends_preserve_unicode_scalars_and_aliases(backend):
    assert yaml_loader.load_yaml_mapping(
        'name: 雪\nvalues: &v [true, false, 12, 1.5, null, "yes"]\ncopy: *v', description="test"
    ) == {
        "name": "雪",
        "values": [True, False, 12, 1.5, None, "yes"],
        "copy": [True, False, 12, 1.5, None, "yes"],
    }


def test_pure_python_fallback(monkeypatch):
    original_error = yaml_loader.DuplicateKeyError
    with monkeypatch.context() as patch:
        patch.delattr(yaml, "CSafeLoader", raising=False)
        isolated = runpy.run_path(yaml_loader.__file__)
        assert issubclass(isolated["_NoDuplicatesLoader"], yaml.SafeLoader)
        assert isolated["load_yaml_mapping"]("a: 1", description="test") == {"a": 1}
    assert yaml_loader.DuplicateKeyError is original_error


def test_repository_yaml_corpus_has_matching_backend_semantics(monkeypatch):
    root = Path(__file__).parents[3]
    paths = [
        path
        for directory in ("checks", "examples", "src/graphcheck/packs", "tests/fixtures")
        for path in (root / directory).rglob("*.yml")
        if "external" not in path.parts
    ]
    for path in paths:
        outcomes = []
        for base in (yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)):

            class StrictLoader(base):
                pass

            StrictLoader.add_constructor(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_loader._construct_mapping
            )
            monkeypatch.setattr(yaml_loader, "_NoDuplicatesLoader", StrictLoader)
            try:
                outcomes.append(
                    yaml_loader.load_yaml_mapping(
                        path.read_text(encoding="utf-8"), description="test"
                    )
                )
            except (ValueError, TypeError, yaml.YAMLError):
                outcomes.append("rejected")
        assert outcomes[0] == outcomes[1], path
