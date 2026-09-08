"""Tests for the `_run` thread-offloading/timeout wrapper in server.py.

This is the fix for the same freeze-the-whole-server bug agent-memory-mcp
had and fixed: FastMCP calls a synchronous @mcp.tool() function directly on
its single asyncio event loop with no thread-pool offloading, so a blocking
file access (WIKI_DIR can be pointed at a synced folder the same way
AGENT_MEMORY_DIR commonly is) would freeze the entire server, not just that
one call.

Unlike agent-memory-mcp, this `_run` takes no per-resource lock — see its
docstring in server.py for why write_page's full-overwrite semantics don't
need one the way agent-memory-mcp's read-modify-write kanban functions did.
"""

import asyncio
import time

from wiki_mcp import server


def test_run_returns_result_of_fast_call():
    async def go():
        return await server._run(lambda: "ok")

    assert asyncio.run(go()) == "ok"


def test_run_passes_args_positionally():
    async def go():
        return await server._run(lambda a, b: f"{a}-{b}", "x", "y")

    assert asyncio.run(go()) == "x-y"


def test_run_times_out_and_returns_informative_message(monkeypatch):
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 0.05)

    def slow():
        time.sleep(0.3)
        return "done"

    async def go():
        return await server._run(slow)

    result = asyncio.run(go())
    assert "still working" in result.lower()
    assert "background" in result.lower()


def test_run_does_not_block_concurrent_calls(monkeypatch):
    """Regression test for the hang bug: a slow blocking call must not
    freeze other concurrent _run calls."""
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)
    order = []

    def slow():
        time.sleep(0.3)
        order.append("slow")
        return "slow-done"

    def fast():
        order.append("fast")
        return "fast-done"

    async def go():
        return await asyncio.gather(server._run(slow), server._run(fast))

    results = asyncio.run(go())
    assert results == ["slow-done", "fast-done"]
    assert order == ["fast", "slow"]
