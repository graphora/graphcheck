import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]
MONITORING = ROOT / "examples" / "monitoring"


def test_prometheus_scrapes_graphcheck_on_the_host_gateway():
    config = yaml.safe_load(
        (MONITORING / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    )

    graphcheck_job = next(
        job for job in config["scrape_configs"] if job["job_name"] == "graphcheck"
    )

    assert graphcheck_job["static_configs"] == [{"targets": ["host.docker.internal:9100"]}]


def test_compose_maps_the_host_gateway_for_prometheus():
    config = yaml.safe_load((MONITORING / "compose.yml").read_text(encoding="utf-8"))

    assert "host.docker.internal:host-gateway" in config["services"]["prometheus"]["extra_hosts"]


def test_compose_binds_monitoring_ports_to_localhost_only():
    config = yaml.safe_load((MONITORING / "compose.yml").read_text(encoding="utf-8"))

    assert config["services"]["prometheus"]["ports"] == ["127.0.0.1:9090:9090"]
    assert config["services"]["grafana"]["ports"] == ["127.0.0.1:3000:3000"]


def test_dashboards_use_the_provisioned_prometheus_datasource_uid():
    datasource_path = (
        MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus-datasource.yml"
    )
    provisioning = yaml.safe_load(datasource_path.read_text(encoding="utf-8"))
    datasource_uid = provisioning["datasources"][0].get("uid")

    assert datasource_uid

    for dashboard_path in (MONITORING / "grafana" / "dashboards").glob("*.json"):
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
        panels = list(dashboard["panels"])

        while panels:
            panel = panels.pop()
            panels.extend(panel.get("panels", []))
            if "datasource" in panel:
                assert panel["datasource"]["uid"] == datasource_uid


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


def test_last_successful_health_check_panel_handles_uninitialized_timestamp():
    dashboard_path = next((MONITORING / "grafana" / "dashboards").glob("*.json"))
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    panel = next(
        panel
        for panel in dashboard["panels"]
        if panel["title"] == "Seconds Since Last Successful Health Check"
    )

    assert panel["targets"][0]["expr"] == (
        "time() - (graphcheck_last_healthcheck_timestamp_seconds > 0)"
    )
    assert panel["fieldConfig"]["defaults"]["noValue"] == "Never"
