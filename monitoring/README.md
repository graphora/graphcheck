# GraphCheck Monitoring

This directory contains the reference Prometheus and Grafana configuration for GraphCheck observability.

## Prerequisites

- Docker Desktop (or Docker Engine with Docker Compose)
- A GraphCheck project configured with a Neo4j connection profile
- `graphcheck monitor` available from the project root

## Start the monitoring stack

From this directory:

```bash
docker compose up -d
```

This starts:

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000

## Start GraphCheck monitoring

From the GraphCheck project root, run:

```bash
uv run graphcheck monitor
```

By default, GraphCheck:

- performs a lightweight health check every 15 seconds;
- exposes Prometheus metrics at `http://localhost:9100/metrics`; and
- continues monitoring until interrupted with `Ctrl+C`.

## Dashboard

The provisioned GraphCheck dashboard displays:

- Database reachability
- Connector status
- Health-check duration (95th percentile)
- Health-check failures
- Time since the last successful health check

## Stop monitoring

Stop the GraphCheck monitoring process:

- Press `Ctrl+C` in the terminal running `graphcheck monitor`.

Stop the monitoring stack:

```bash
docker compose down
```