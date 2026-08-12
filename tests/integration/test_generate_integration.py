from __future__ import annotations

import json
from pathlib import Path

import yaml

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.profile import (
    BaselineProfile,
    GraphSchema,
    ProfileStatistics,
    profile_fingerprint,
)
from graphcheck.contracts.results import Capabilities, RunTarget, SkipReason, Verdict
from graphcheck.engine import Engine
from graphcheck.generation.proposals import ProposalRequest, RawProposal, RawProposalBatch
from graphcheck.generation.service import GenerationService
from graphcheck.project import write_default_project

FIXTURE = Path(__file__).parents[1] / "contracts" / "fixtures" / "baseline.json"
TARGET = RunTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="integration-target",
    capabilities=Capabilities(apoc=False, count_store=True),
)


class FakeClient:
    def __init__(self, candidates: list[RawProposal]) -> None:
        self.candidates = candidates
        self.requests: list[ProposalRequest] = []

    def propose(self, request: ProposalRequest) -> RawProposalBatch:
        self.requests.append(request)
        return RawProposalBatch(candidates=self.candidates)


class NoReadConnector:
    def _fail(self, *args, **kwargs):
        raise AssertionError("generated suite attempted a connector read")

    run_read_result = run_read_result_bounded = run_read = _fail


def _project(root: Path, baseline: BaselineProfile) -> None:
    write_default_project(root)
    config = yaml.safe_load((root / "graphcheck.yml").read_text(encoding="utf-8"))
    config["generate"] = {
        "provider": "ollama",
        "model": "qwen3:8b",
        "base_url": "http://localhost:11434/v1",
    }
    (root / "graphcheck.yml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    baselines = root / ".graphcheck" / "baselines"
    baselines.mkdir(parents=True)
    (baselines / "20260724T120000.000000.json").write_text(
        baseline.model_dump_json(by_alias=True),
        encoding="utf-8",
    )


def test_real_pipeline_writes_loadable_suite_that_never_reads_graph(tmp_path: Path) -> None:
    baseline = BaselineProfile.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    _project(tmp_path, baseline)
    client = FakeClient(
        [
            RawProposal(
                kind="conformance",
                spec={
                    "id": "customer-id-complete",
                    "check": "completeness",
                    "with": {"label": "Customer", "property": "id"},
                },
            ),
            RawProposal(
                kind="competency",
                spec={
                    "id": "customer-count-shape",
                    "question": "Can customers be counted?",
                    "query": "MATCH (c:Customer) RETURN count(c) AS count",
                    "expect": {"rows": {"exactly": 1}, "columns": ["count"]},
                },
            ),
        ]
    )
    service = GenerationService(client_factory=lambda config, key: client)

    result = service.generate(
        project_root=tmp_path,
        baseline_from=None,
        document_paths=None,
        requested_count=2,
        disclosure_sink=lambda event: None,
    )

    generated = tmp_path / result.path
    text = generated.read_text(encoding="utf-8")
    suite = load_suite(text, source=str(generated))
    assert len(suite.checks) == 2
    assert all(check.generated for check in suite.checks)

    results = Engine(NoReadConnector()).run_yaml(text, target=TARGET)
    assert all(check.verdict is Verdict.SKIPPED for check in results.checks)
    assert all(check.skip_reason is SkipReason.GENERATED for check in results.checks)


def test_external_supply_chain_schema_crosses_only_allow_list(tmp_path: Path) -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["schema"] = {
        "labels": [
            {
                "name": "Shipment",
                "count": 40,
                "properties": [
                    {"name": "tracking_id", "type": "STRING"},
                ],
                "degree_distribution": {
                    "median": 1.0,
                    "p95": 2.0,
                    "p99": 3.0,
                    "maximum": 3,
                },
            },
            {
                "name": "Supplier",
                "count": 5,
                "properties": [{"name": "supplier_id", "type": "STRING"}],
                "degree_distribution": {
                    "median": 2.0,
                    "p95": 4.0,
                    "p99": 5.0,
                    "maximum": 5,
                },
            },
        ],
        "relationship_types": [{"name": "SENT_BY", "count": 40}],
        "constraints": [
            {
                "name": "shipment_tracking_unique",
                "type": "UNIQUENESS",
                "labels_or_types": ["Shipment"],
                "properties": ["tracking_id"],
            }
        ],
        "indexes": [
            {
                "name": "supplier_id_index",
                "type": "RANGE",
                "labels_or_types": ["Supplier"],
                "properties": ["supplier_id"],
            }
        ],
    }
    raw["statistics"] = {
        "node_count": 45,
        "relationship_count": 40,
        "property_coverage": [
            {
                "owner": "node",
                "owner_name": "Shipment",
                "property": "tracking_id",
                "coverage": 100.0,
            },
            {
                "owner": "node",
                "owner_name": "Supplier",
                "property": "supplier_id",
                "coverage": 100.0,
            },
        ],
    }
    schema = GraphSchema.model_validate(raw["schema"])
    statistics = ProfileStatistics.model_validate(raw["statistics"])
    raw["fingerprint"] = profile_fingerprint(schema, statistics)
    external = BaselineProfile.model_validate_json(json.dumps(raw))
    _project(tmp_path, external)
    client = FakeClient(
        [
            RawProposal(
                kind="conformance",
                spec={
                    "id": "shipment-tracking-complete",
                    "check": "completeness",
                    "with": {"label": "Shipment", "property": "tracking_id"},
                },
            )
        ]
    )
    service = GenerationService(client_factory=lambda config, key: client)

    service.generate(
        project_root=tmp_path,
        baseline_from=None,
        document_paths=None,
        requested_count=1,
        disclosure_sink=lambda event: None,
    )

    prompt = client.requests[0].user_prompt
    assert "Shipment" in prompt and "Supplier" in prompt and "SENT_BY" in prompt
    assert "shipment_tracking_unique" not in prompt
    assert "supplier_id_index" not in prompt
    assert raw["target"]["database"] not in prompt
