from __future__ import annotations

from mcp.server import MCPServer
from pydantic import BaseModel

from graphcheck.contracts.results import Results
from graphcheck.mcp import adapter


class SuiteCheckInfo(BaseModel):
    id: str
    kind: str
    severity: str
    tags: list[str]
    generated: bool


class SuiteInfo(BaseModel):
    suite: str
    checks: list[SuiteCheckInfo]


class CheckListResponse(BaseModel):
    suites: list[SuiteInfo]


mcp = MCPServer("GraphCheck")


@mcp.tool()
def list_checks() -> CheckListResponse:
    """Return the configured GraphCheck suites and their checks (without executing them).

    Use a suite's ``suite`` value as the ``run_suite`` argument.
    """
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
def get_results(run_id: str = "latest") -> Results:
    """Load a GraphCheck results.json file.

    Defaults to the most recent run (the ``latest`` alias) when no run id is given.
    """
    return adapter.get_results(run_id)


def run() -> None:
    """Start the MCP server."""
    mcp.run()
