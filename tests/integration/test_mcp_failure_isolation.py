from __future__ import annotations

import json
import select
import subprocess
import time

import pytest
from mcp.client.stdio import StdioServerParameters

from graphcheck.application.run import RunRequest, execute_run
from graphcheck.contracts.results import Capabilities, ResultsTarget
from graphcheck.neo4j_adapter import Counts, QueryResult

_TARGET = ResultsTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:isolation",
    capabilities=Capabilities(apoc=False, count_store=True),
    labels=[],
    relationship_types=[],
)


class _StubClient:
    """A read-only client double that returns one row for any query."""

    def __init__(self) -> None:
        self.closed = False

    def probe(self, *, timeout_s=None):
        return _TARGET, object(), Counts(nodes=1, relationships=0)

    def run_read_result(self, query, params, *, timeout_s=None):
        return QueryResult([{"value": 1}], ("value",), ())

    def close(self) -> None:
        self.closed = True


def _rpc_readline(process: subprocess.Popen, timeout: float = 20.0) -> dict:
    """Read one newline-delimited JSON-RPC message from the server, with a timeout."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("no MCP response from the server")
        ready, _, _ = select.select([process.stdout], [], [], remaining)
        if not ready:
            raise TimeoutError("no MCP response from the server")
        line = process.stdout.readline()
        if line.strip():
            return json.loads(line)


@pytest.mark.anyio
async def test_mcp_tool_failures_are_isolated_from_the_session(mcp_project, mcp_client):
    async with mcp_client(mcp_project) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {"list_checks", "run_suite", "get_results"}

        # First call succeeds.
        result1 = await client.call_tool("list_checks", {})
        assert result1.is_error is False
        assert result1.structured_content is not None
        assert "suites" in result1.structured_content

        # A traversal run id is rejected with a sanitized error that leaks nothing.
        traversal = await client.call_tool("get_results", {"run_id": "../../outside"})
        assert traversal.is_error is True
        assert traversal.content
        assert "The supplied run ID is invalid." in traversal.content[0].text
        assert "outside" not in traversal.content[0].text

        # A syntactically valid but missing run id returns a sanitized, path-free error.
        missing = await client.call_tool("get_results", {"run_id": "does-not-exist"})
        assert missing.is_error is True
        assert missing.content
        missing_text = missing.content[0].text
        assert "No GraphCheck results exist for the supplied run ID." in missing_text
        assert "results.json" not in missing_text
        assert ".graphcheck" not in missing_text

        # The server still serves other tools after the failures.
        result2 = await client.call_tool("list_checks", {})
        assert result2.is_error is False
        assert result2.structured_content is not None
        assert "suites" in result2.structured_content


def test_killing_an_active_mcp_session_does_not_break_independent_engine_runs(
    mcp_project, monkeypatch
):
    # Spawn a victim server we own so we can kill it *after* proving it is serving.
    params = StdioServerParameters(
        command="graphcheck", args=["mcp", "serve"], cwd=str(mcp_project)
    )
    victim = subprocess.Popen(
        [params.command, *params.args],
        cwd=params.cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        # Establish a working session: initialize, then a real tool call that succeeds. This
        # proves the victim reached the serving state before it is killed.
        victim.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "isolation-test", "version": "1"},
                    },
                }
            )
            + "\n"
        )
        victim.stdin.flush()
        init = _rpc_readline(victim)
        assert init.get("id") == 1
        assert "result" in init

        victim.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        victim.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_checks", "arguments": {}},
                }
            )
            + "\n"
        )
        victim.stdin.flush()
        called = _rpc_readline(victim)
        assert called.get("id") == 2
        assert called["result"]["isError"] is False
    finally:
        # Terminate the active server abruptly, without a graceful shutdown.
        victim.kill()
        victim.wait(timeout=10)
        for stream in (victim.stdin, victim.stdout, victim.stderr):
            if stream is not None:
                stream.close()

    assert victim.returncode is not None

    # After the active MCP server is killed, an independent in-process engine run on the same
    # project completes and publishes its artifacts. This proves the killed session left no
    # corrupt `latest` and no stuck publication lock (the OS releases the file lock on
    # process death).
    monkeypatch.chdir(mcp_project)
    client = _StubClient()
    outcome = execute_run(
        RunRequest(profile=None, suite_ids=["smoke"], tags=[], fail_fast=False),
        client_factory=lambda profile, max_concurrency: client,
    )

    assert outcome.results.run.error is None
    assert outcome.results.run.status.value != "failed"
    assert outcome.results_path is not None
    assert outcome.results_path.exists()
    assert client.closed is True
