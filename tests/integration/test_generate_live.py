from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from graphcheck.contracts.check import load_suite
from graphcheck.generation.service import GenerationService
from graphcheck.project import write_default_project

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_LLM_INTEGRATION") != "1",
    reason="set GRAPHCHECK_LLM_INTEGRATION=1 to run live generation smoke tests",
)

FIXTURE = Path(__file__).parents[1] / "unit" / "contracts" / "fixtures" / "baseline.json"


def test_live_generation_smoke(tmp_path: Path) -> None:
    provider = os.environ["GRAPHCHECK_LLM_PROVIDER"]
    model = os.environ["GRAPHCHECK_LLM_MODEL"]
    api_key_env = os.environ.get("GRAPHCHECK_LLM_API_KEY_ENV")
    base_url = os.environ.get("GRAPHCHECK_LLM_BASE_URL")
    write_default_project(tmp_path)
    config = yaml.safe_load((tmp_path / "graphcheck.yml").read_text(encoding="utf-8"))
    config["generate"] = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "base_url": base_url,
        "temperature": 0,
    }
    (tmp_path / "graphcheck.yml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    baselines = tmp_path / ".graphcheck" / "baselines"
    baselines.mkdir(parents=True)
    (baselines / "20260724T120000.000000.json").write_bytes(FIXTURE.read_bytes())
    disclosures: list[object] = []

    result = GenerationService().generate(
        project_root=tmp_path,
        baseline_from=None,
        document_paths=None,
        requested_count=1,
        disclosure_sink=disclosures.append,
    )

    assert disclosures
    generated = tmp_path / result.path
    loaded = load_suite(generated.read_text(encoding="utf-8"), source=str(generated))
    assert len(loaded.checks) == 1
    assert loaded.checks[0].generated is True
