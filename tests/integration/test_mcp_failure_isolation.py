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

        tools = await session.list_tools()

        list_checks_tool = next(tool for tool in tools.tools if tool.name == "list_checks")

        assert list_checks_tool.output_schema is not None

        # First call succeeds.
        result1 = await session.call_tool("list_checks", {})
        assert result1 is not None
        assert result1.is_error is False
        assert result1.structured_content is not None
        assert "checks" in result1.structured_content

        # Trigger a real GraphCheck tool failure with an invalid run ID.
        result = await session.call_tool(
            "get_results",
            {"run_id": "../../outside"},
        )

        assert result.is_error is True
        assert result.content
        assert "The supplied run ID is invalid." in result.content[0].text
        assert "outside" not in result.content[0].text

        # Server should still work afterwards.
        result2 = await session.call_tool("list_checks", {})

        assert result2 is not None
        assert result2.is_error is False
        assert result2.structured_content is not None
        assert "checks" in result2.structured_content
