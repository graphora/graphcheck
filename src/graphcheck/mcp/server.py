from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from graphcheck.mcp import adapter

mcp = FastMCP("GraphCheck")


@mcp.tool()
def list_checks() -> dict:
    """Return the available GraphCheck checks."""
    return adapter.list_checks()


@mcp.tool()
def run_suite(
    suite: str,
    profile: str | None = None,
):
    """Run a GraphCheck suite."""
    return adapter.run_suite(
        suite=suite,
        profile=profile,
    )


@mcp.tool()
def get_results(run_id: str) -> Any:
    """Load a GraphCheck results.json file."""
    return adapter.get_results(run_id)


def run() -> None:
    """Start the MCP server."""
    mcp.run()
