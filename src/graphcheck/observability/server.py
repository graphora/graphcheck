from __future__ import annotations

import prometheus_client

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9100


def start_metrics_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Expose the existing Prometheus registry on the supplied host and port."""

    prometheus_client.start_http_server(port, addr=host)


__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "start_metrics_server"]
