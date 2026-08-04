from __future__ import annotations

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.anyio
async def test_mcp_agent_surface():
    server = StdioServerParameters(
        command="uv",
        args=["run", "graphcheck", "mcp", "serve"],
    )

    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()

        tools = await session.list_tools()

        names = {tool.name for tool in tools.tools}

        assert "list_checks" in names
        assert "run_suite" in names
        assert "get_results" in names

        result = await session.call_tool(
            "list_checks",
            {},
        )

        assert result is not None
