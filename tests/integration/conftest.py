from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from graphcheck.connection_profiles import write_default_profiles
from graphcheck.project import write_default_project

_FIXTURES = Path(__file__).parents[1] / "unit" / "contracts" / "fixtures"


def stdio_params(project: Path):
    """Parameters that launch `graphcheck mcp serve` in the given project directory."""
    from mcp.client.stdio import StdioServerParameters

    return StdioServerParameters(command="graphcheck", args=["mcp", "serve"], cwd=str(project))


@pytest.fixture
def mcp_client() -> Callable[[Path], object]:
    """Factory for a modern MCP client (2026-07-28 discovery mode, no legacy initialize).

    Usage: ``async with mcp_client(project) as client: ...``
    """
    from mcp import Client
    from mcp.client.stdio import stdio_client

    def _connect(project: Path) -> object:
        return Client(stdio_client(stdio_params(project)), mode="auto")

    return _connect


@pytest.fixture
def mcp_project(tmp_path: Path) -> Path:
    """A minimal GraphCheck project for driving `graphcheck mcp serve` over stdio.

    It carries the default `example` suite so `list_checks` returns real suites, and a
    pre-seeded `latest` run so `get_results` can be exercised without a database.
    """
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    checks = tmp_path / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "smoke.yml").write_text(
        """\
suite: smoke
competency:
  - id: smoke
    question: Does the graph return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
""",
        encoding="utf-8",
    )
    latest = tmp_path / ".graphcheck" / "runs" / "latest"
    latest.mkdir(parents=True)
    shutil.copyfile(_FIXTURES / "results.complete.json", latest / "results.json")
    return tmp_path
