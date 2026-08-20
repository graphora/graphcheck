# Prometheus Configuration

This directory contains the reference Prometheus configuration for the GraphCheck observability stack.

The configuration:

- scrapes the GraphCheck metrics endpoint;
- is intended for local development and reference deployments; and
- is used by the Docker Compose stack in the parent `monitoring` directory.

The default scrape target is:

```
host.docker.internal:9100
```

Start the monitoring stack from the `monitoring` directory:

```bash
docker compose up -d
```