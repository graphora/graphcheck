from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.generation.proposals import RawProposal, RawProposalBatch
from graphcheck.generation.service import GenerationService
from graphcheck.project import write_default_project

FIXTURE = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
runner = CliRunner()


class FakeClient:
    def propose(self, request):
        return RawProposalBatch(
            candidates=[
                RawProposal(
                    kind="conformance",
                    spec={
                        "id": "customer-id-complete",
                        "check": "completeness",
                        "with": {"label": "Customer", "property": "id"},
                    },
                )
            ]
        )


def test_generate_without_extra_prints_install_command(monkeypatch) -> None:
    real_import = __import__("importlib").import_module
    monkeypatch.setattr(
        "graphcheck.cli.import_module",
        lambda name: (
            (_ for _ in ()).throw(ModuleNotFoundError(name="instructor"))
            if name == "graphcheck.generation.service"
            else real_import(name)
        ),
    )

    result = runner.invoke(app, ["generate"])

    assert result.exit_code == 2
    assert "graphcheck generate" in result.output
    assert 'pip install "graphcheck[generate]"' in result.output


def setup_project(tmp_path: Path, monkeypatch) -> None:
    write_default_project(tmp_path)
    config = yaml.safe_load((tmp_path / "graphcheck.yml").read_text(encoding="utf-8"))
    config["generate"] = {
        "provider": "ollama",
        "model": "qwen",
        "base_url": "http://localhost:11434/v1",
    }
    (tmp_path / "graphcheck.yml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    baselines = tmp_path / ".graphcheck" / "baselines"
    baselines.mkdir(parents=True)
    (baselines / "20260724T120000.000000.json").write_bytes(FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    service = GenerationService(client_factory=lambda config, key: FakeClient())
    monkeypatch.setattr("graphcheck.cli.generation_service_factory", lambda: service)


def test_generate_cli_json_is_machine_readable(tmp_path: Path, monkeypatch) -> None:
    setup_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["generate", "--count", "1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "generated"
    assert payload["written"] == 1
    disclosure = json.loads(result.stderr)
    assert disclosure["event"] == "generate.disclosure"
    assert disclosure["destination"] == "http://localhost:11434/v1"


def test_generate_cli_count_bounds_are_usage_errors(tmp_path: Path, monkeypatch) -> None:
    setup_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["generate", "--count", "0"])

    assert result.exit_code == 2
    assert "generate disclosure" not in result.stderr


def test_generate_cli_missing_config_is_fix_bearing(tmp_path: Path, monkeypatch) -> None:
    write_default_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["generate", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "generate.config_missing"
    assert payload["error"]["fix"]
