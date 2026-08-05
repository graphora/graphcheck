import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
MONITORING = ROOT / "monitoring"


def test_prometheus_scrapes_graphcheck_on_the_host_gateway():
    config = yaml.safe_load(
        (MONITORING / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    )

    graphcheck_job = next(
        job for job in config["scrape_configs"] if job["job_name"] == "graphcheck"
    )

    assert graphcheck_job["static_configs"] == [{"targets": ["host.docker.internal:9100"]}]


def test_compose_maps_the_host_gateway_for_prometheus():
    config = yaml.safe_load((MONITORING / "docker-compose.yml").read_text(encoding="utf-8"))

    assert "host.docker.internal:host-gateway" in config["services"]["prometheus"]["extra_hosts"]


def test_dashboard_queries_graphcheck_metrics():
    dashboard_path = next((MONITORING / "grafana" / "dashboards").glob("*.json"))
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    expressions = [
        target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])
    ]

    expected_metrics = {
        "graphcheck_database_up",
        "graphcheck_connector_connected",
        "graphcheck_healthcheck_duration_seconds_bucket",
        "graphcheck_healthcheck_failures_total",
        "graphcheck_last_healthcheck_timestamp_seconds",
    }

    assert all(
        any(metric in expression for expression in expressions) for metric in expected_metrics
    )
    assert "up" not in expressions
    assert dashboard["time"] == {"from": "now-6h", "to": "now"}
