from __future__ import annotations

from mcp.server import MCPServer
from pydantic import BaseModel

from graphcheck.contracts.results import Results
from graphcheck.mcp import adapter


class CheckInfo(BaseModel):
    pack: str
    name: str
    template: str
    requires: list[str]
    sampled: bool
    evidence_elements: list[str]
    evidence_id_fields: list[str]


class CheckListResponse(BaseModel):
    checks: list[CheckInfo]


mcp = MCPServer("GraphCheck")


@mcp.tool()
def list_checks() -> CheckListResponse:
    """Return the available GraphCheck checks."""
    return CheckListResponse.model_validate(adapter.list_checks())


@mcp.tool()
def run_suite(
    suite: str,
    profile: str | None = None,
) -> Results:
    """Run a GraphCheck suite."""
    return adapter.run_suite(
        suite=suite,
        profile=profile,
    )


@mcp.tool()
def get_results(run_id: str) -> Results:
    """Load a GraphCheck results.json file."""
    return adapter.get_results(run_id)


def run() -> None:
    """Start the MCP server."""
    mcp.run()
