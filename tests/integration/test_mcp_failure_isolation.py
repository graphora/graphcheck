from __future__ import annotations

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.anyio
async def test_mcp_failure_isolation():
    server = StdioServerParameters(
        command="graphcheck",
        args=["mcp", "serve"],
    )

    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()

        # First call succeeds
        result1 = await session.call_tool("list_checks", {})
        assert result1 is not None

        # Call a tool that does not exist
        result = await session.call_tool(
            "tool_that_does_not_exist",
            {},
        )

        assert result.is_error is True
        assert "Unknown tool" in result.content[0].text

        # Server should still work afterwards
        result2 = await session.call_tool("list_checks", {})
        assert result2 is not None
