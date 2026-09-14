import json
import re
import runpy
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.results import Results
from graphcheck.contracts.schemas import SCHEMAS_DIR
from graphcheck.reporting.html import render_html_report
from graphcheck.reporting.writer import load_results, results_json

FIXTURES = Path(__file__).parent / "fixtures" / "historical"
VERSIONS = ("1.0", "1.1", "1.2", "2.0")
INVENTORY = {"labels": ["Account", "Customer"], "relationship_types": ["CONTROLS", "OWNS"]}
CURRENT_SCHEMA = json.loads((SCHEMAS_DIR / "results.schema.json").read_text(encoding="utf-8"))


def _warning(version):
    if version == "2.0":
        return ""
    return (
        f"results.schema_deprecated: Reading results schema {version} is deprecated; "
        "removal is planned for GraphCheck 0.5.0, postponed while required by the "
        "current/previous-schema guarantee. Use the transformer in "
        "docs/reference/artifact-compatibility.md to migrate to 2.0.\n"
    )


def _normalized(raw):
    expected = deepcopy(raw)
    expected["schema_version"] = "2.0"
    run = expected["run"]
    if "status" in run:
        run["run_status"] = run.pop("status")
    for field in ("previous_run_id", "baseline_ref", "config_hash"):
        run.setdefault(field, None)
    if run["target"] is not None:
        for field in ("nodes", "relationships", "labels", "relationship_types"):
            run["target"].setdefault(field, None)
    return expected


@pytest.mark.parametrize("version", VERSIONS)
def test_historical_artifacts_load_as_normalized_2_0(version, capsys):
    source = FIXTURES / version / "results.json"
    original = source.read_bytes()
    raw = json.loads(original)
    schema_name = "results.schema.json" if version == "2.0" else f"results-{version}.schema.json"
    schema = json.loads((SCHEMAS_DIR / schema_name).read_text(encoding="utf-8"))
    assert raw["schema_version"] == version
    jsonschema.validate(raw, schema)

    model = load_results(source)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _warning(version)
    normalized = model.model_dump(mode="json", by_alias=True)
    assert normalized == _normalized(raw)
    jsonschema.validate(normalized, CURRENT_SCHEMA)
    assert model._historical_schema_version == (None if version == "2.0" else version)
    assert source.read_bytes() == original

    # Writers/renderers revalidate the model without duplicating the original read warning.
    assert load_results(model) == model
    exported = json.loads(results_json(model))
    assert exported["schema_version"] == version
    jsonschema.validate(exported, schema)
    assert "<!doctype html>" in render_html_report(model)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("input_kind", ["dict", "json", "path"])
def test_warning_is_once_per_artifact_read_for_all_input_types(version, input_kind, capsys):
    source = FIXTURES / version / "results.json"
    raw_json = source.read_text(encoding="utf-8")
    data = {"dict": json.loads(raw_json), "json": raw_json, "path": source}[input_kind]
    original = deepcopy(data)
    for _ in range(2):
        load_results(data)
        assert capsys.readouterr() == ("", _warning(version))
    assert data == original


@pytest.fixture
def transformer_path(tmp_path):
    policy = (SCHEMAS_DIR.parent / "reference" / "artifact-compatibility.md").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(r"```python\n(.*?)\n```", policy, flags=re.DOTALL)
    assert len(blocks) == 1, "Keep the public transformer directly executable and tested"
    script = tmp_path / "migrate_results.py"
    script.write_text(blocks[0], encoding="utf-8")
    return script


@pytest.mark.parametrize(
    "counts", [{"nodes": 0, "relationships": 0}, {"nodes": 1250, "relationships": 3480}]
)
def test_later_1_1_artifacts_preserve_recorded_counts(counts):
    raw = json.loads((FIXTURES / "1.1" / "results.json").read_text(encoding="utf-8"))
    raw["run"]["target"].update(counts)
    schema = json.loads((SCHEMAS_DIR / "results-1.1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(raw, schema)
    exported = json.loads(results_json(load_results(raw)))
    jsonschema.validate(exported, schema)
    assert {field: exported["run"]["target"][field] for field in counts} == counts


@pytest.mark.parametrize("version", VERSIONS)
def test_documented_transformer_migrates_historical_fixtures(version, transformer_path, capsys):
    source = FIXTURES / version / "results.json"
    original = source.read_bytes()
    destination = transformer_path.parent / "results-2.0.json"
    args = [sys.executable, str(transformer_path), str(source), str(destination)]
    if version in {"1.0", "1.1"}:
        inventory = transformer_path.parent / "inventory.json"
        inventory.write_text(json.dumps(INVENTORY), encoding="utf-8")
        args.append(str(inventory))

    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=False)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == _warning(version)
    migrated = json.loads(destination.read_text(encoding="utf-8"))
    expected = _normalized(json.loads(original))
    if version in {"1.0", "1.1"}:
        expected["run"]["target"].update(INVENTORY)
    assert migrated == expected
    jsonschema.validate(migrated, CURRENT_SCHEMA)
    assert Results.model_validate(migrated) == load_results(destination)
    assert capsys.readouterr() == ("", "")
    assert source.read_bytes() == original

    second = transformer_path.parent / "results-second-copy.json"
    runpy.run_path(str(transformer_path))["migrate"](destination, second)
    assert second.read_bytes() == destination.read_bytes()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "inventory", [None, {}, {"labels": []}, {**INVENTORY, "labels": ["Z", "A"]}]
)
def test_transformer_requires_complete_canonical_historical_inventory(inventory, transformer_path):
    migrate = runpy.run_path(str(transformer_path))["migrate"]
    destination = transformer_path.parent / "invalid.json"
    with pytest.raises(ValueError):
        migrate(FIXTURES / "1.0" / "results.json", destination, inventory)
    assert not destination.exists()


def test_transformer_preserves_aggregate_evidence(transformer_path):
    raw = json.loads((FIXTURES / "1.1" / "results.json").read_text(encoding="utf-8"))
    check = raw["checks"][0]
    check.update(pattern="drift", measured={"nodes": 80}, expected={"nodes": 100})
    check["evidence"].update(
        message="Node count declined",
        elements=[{"kind": "aggregate", "id": "node_count:label=Customer"}],
        truncated=False,
        total_count=1,
    )
    source = transformer_path.parent / "aggregate.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    destination = transformer_path.parent / "migrated.json"
    runpy.run_path(str(transformer_path))["migrate"](source, destination, INVENTORY)
    migrated = load_results(destination)
    assert migrated.checks[0].evidence.elements[0].kind == "aggregate"
    assert migrated.checks[0].evidence.elements[0].id == "node_count:label=Customer"


@pytest.mark.parametrize("version", VERSIONS)
def test_transformer_migrates_failed_runs_without_inventory(version, transformer_path):
    raw = json.loads((FIXTURES.parent / "results.failed.json").read_text(encoding="utf-8"))
    raw["schema_version"] = version
    if version != "2.0":
        raw["run"]["status"] = raw["run"].pop("run_status")
    source = transformer_path.parent / "failed.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    destination = transformer_path.parent / "migrated.json"
    runpy.run_path(str(transformer_path))["migrate"](source, destination)
    assert load_results(destination).model_dump(mode="json", by_alias=True) == _normalized(raw)


@pytest.mark.parametrize("version", [None, "0.9", "1.3", "3.0"])
def test_loader_rejects_unknown_schema_versions(version, capsys):
    raw = json.loads((FIXTURES / "2.0" / "results.json").read_text(encoding="utf-8"))
    if version is None:
        raw.pop("schema_version")
    else:
        raw["schema_version"] = version
    with pytest.raises(ValidationError, match="schema_version"):
        load_results(raw)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "2.0", "9.0"])
def test_transformer_rejects_invalid_artifacts_before_writing(version, transformer_path, capsys):
    raw = json.loads((FIXTURES / "2.0" / "results.json").read_text(encoding="utf-8"))
    raw["schema_version"] = version
    if version != "2.0":
        raw["run"]["status"] = raw["run"].pop("run_status")
    raw["totals"]["checks"] = 999
    source = transformer_path.parent / "invalid.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    destination = transformer_path.parent / "migrated.json"
    with pytest.raises(ValidationError):
        runpy.run_path(str(transformer_path))["migrate"](source, destination, INVENTORY)
    assert not destination.exists()
    assert json.loads(source.read_text(encoding="utf-8")) == raw
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("same_path", [False, True])
def test_transformer_refuses_to_overwrite_files(same_path, transformer_path):
    source = transformer_path.parent / "source.json"
    original = (FIXTURES / "2.0" / "results.json").read_bytes()
    source.write_bytes(original)
    destination = source if same_path else transformer_path.parent / "existing.json"
    destination.write_bytes(original)
    with pytest.raises(FileExistsError):
        runpy.run_path(str(transformer_path))["migrate"](source, destination)
    assert source.read_bytes() == destination.read_bytes() == original
