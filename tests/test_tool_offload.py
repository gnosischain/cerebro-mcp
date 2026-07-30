"""Global tool offload — the guard against event-loop starvation.

FastMCP runs sync tool bodies INLINE on the single asyncio event loop, so one
slow sync tool stalls every concurrent call. This was observed twice in
production as "every tool times out, including a bare SELECT 1" — the giveaway
being that `execute_query` was ALREADY offloaded, so the request had never been
dispatched at all.

`test_slow_sync_tool_does_not_block_a_concurrent_call` is the regression test
for that. `test_every_registered_tool_is_async_after_install` is what keeps it
fixed: it fails the moment someone adds a sync tool after the installer runs, or
reorders `install_tool_offload` above a `register_*` call.
"""

import time

import anyio
import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.runtime.offload import install_tool_offload


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _build() -> FastMCP:
    mcp = FastMCP("offload-regression")

    @mcp.tool()
    def slow_sync(seconds: float = 1.0) -> str:
        """Blocking sync tool."""
        time.sleep(seconds)
        return "slow-done"

    @mcp.tool()
    def fast_sync() -> str:
        """Trivial sync tool — the canary."""
        return "fast-done"

    return mcp


@pytest.mark.anyio
async def test_slow_sync_tools_run_concurrently_not_serially(monkeypatch):
    """Concurrent slow sync tools must overlap, not queue on the event loop.

    Wall-clock is the discriminator, deliberately. Two weaker formulations were
    tried and rejected because they pass even against the unfixed code:

    * Timing a "fast" call started after ``anyio.sleep`` — the block delays when
      the clock STARTS, so the measured interval is ~0 either way.
    * Counting ticks of a concurrent ticker — a blocked loop does not DROP
      ticks, it delays them, so the count is 20/20 both ways.

    Measured against the pinned SDK: 4 x 0.3s calls take ~1.22s inline versus
    ~0.31s offloaded, so the 0.9s threshold sits well clear of both.
    """
    import cerebro_mcp.runtime.offload as offload_mod

    monkeypatch.setattr(offload_mod.settings, "TOOL_OFFLOAD_ENABLED", True)

    mcp = _build()
    assert install_tool_offload(mcp) == 2

    n, secs = 4, 0.3

    async def call_slow():
        await mcp._tool_manager.call_tool("slow_sync", {"seconds": secs})

    t0 = time.perf_counter()
    async with anyio.create_task_group() as tg:
        for _ in range(n):
            tg.start_soon(call_slow)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.9, (
        f"{n} concurrent {secs}s tools took {elapsed:.2f}s — they serialized on "
        f"the event loop instead of overlapping (serial would be ~{n * secs:.1f}s). "
        "This is the production wedge."
    )


def test_install_tool_offload_preserves_the_tool_contract(monkeypatch):
    """Wrapping must not perturb the schema the client already negotiated."""
    import cerebro_mcp.runtime.offload as offload_mod

    monkeypatch.setattr(offload_mod.settings, "TOOL_OFFLOAD_ENABLED", True)

    mcp = _build()
    before = {
        name: (tool.parameters, tool.description, tool.fn_metadata)
        for name, tool in mcp._tool_manager._tools.items()
    }

    install_tool_offload(mcp)

    for name, tool in mcp._tool_manager._tools.items():
        assert tool.parameters == before[name][0], f"{name} schema changed"
        assert tool.description == before[name][1]
        assert tool.fn_metadata is before[name][2]
        assert tool.is_async is True


def test_install_tool_offload_skips_async_and_is_idempotent(monkeypatch):
    import cerebro_mcp.runtime.offload as offload_mod

    monkeypatch.setattr(offload_mod.settings, "TOOL_OFFLOAD_ENABLED", True)

    mcp = FastMCP("async-only")

    @mcp.tool()
    async def already_async() -> str:
        """Async tool — needs the loop, must not be wrapped."""
        return "ok"

    original = mcp._tool_manager._tools["already_async"].fn
    assert install_tool_offload(mcp) == 0
    assert mcp._tool_manager._tools["already_async"].fn is original

    # Second call is a no-op (marker attribute).
    assert install_tool_offload(mcp) == 0


def test_kill_switch_disables_wrapping(monkeypatch):
    import cerebro_mcp.runtime.offload as offload_mod

    monkeypatch.setattr(offload_mod.settings, "TOOL_OFFLOAD_ENABLED", False)
    mcp = _build()
    assert install_tool_offload(mcp) == 0
    assert mcp._tool_manager._tools["slow_sync"].is_async is False


def test_every_registered_tool_is_async_after_install():
    """The invariant that keeps this fixed.

    Fails if a sync tool is registered after `install_tool_offload` runs in
    server.py, or if the installer is moved above a `register_*` call.
    """
    from cerebro_mcp import server

    blocking = [
        name
        for name, tool in server.mcp._tool_manager._tools.items()
        if not tool.is_async
    ]
    assert blocking == [], (
        "these tools would run inline on the event loop and can wedge the "
        f"whole server: {blocking}"
    )


@pytest.mark.anyio
async def test_limiter_queues_excess_without_starving_the_loop(monkeypatch):
    """Over-subscribing the limiter must queue, not stall the loop."""
    import cerebro_mcp.runtime.offload as offload_mod

    monkeypatch.setattr(offload_mod.settings, "TOOL_OFFLOAD_ENABLED", True)

    limiter = anyio.CapacityLimiter(2)
    monkeypatch.setattr(offload_mod, "_TOOL_LIMITER", limiter)

    mcp = _build()
    install_tool_offload(mcp)

    async def worker():
        await mcp._tool_manager.call_tool("slow_sync", {"seconds": 0.15})

    t0 = time.perf_counter()
    async with anyio.create_task_group() as tg:
        for _ in range(6):  # 6 jobs through 2 tokens
            tg.start_soon(worker)
    elapsed = time.perf_counter() - t0

    # 6 jobs / 2 tokens = 3 waves of 0.15s. Must queue (not run all at once,
    # not serialize one-per-loop-turn) and must still complete.
    assert 0.35 < elapsed < 0.9, (
        f"6 jobs through a 2-token limiter took {elapsed:.2f}s; expected ~3 "
        "waves of 0.15s"
    )


# --- Phase 2: bounds on the previously-unbounded tools -------------------
#
# Offload turns a server-wide wedge into one slow call holding one thread
# token. These assert the calls are ALSO bounded, so N of them cannot exhaust
# the limiter and recreate the wedge.


def test_rpc_sweep_rejects_a_genesis_scale_window():
    """A genesis-to-head sweep must be refused up front, not attempted."""
    from cerebro_mcp.tools.semantic.graph_explorer import transactions as tx

    calls = []

    class _FakeClient:
        def request(self, method, params):
            calls.append(method)
            if method == "eth_blockNumber":
                return hex(40_000_000)  # realistic Gnosis head
            raise AssertionError("must not issue eth_getLogs for a rejected sweep")

    class _FakeRouter:
        standard = _FakeClient()

    with pytest.raises(tx.RpcSweepTooLarge) as exc:
        tx._discover_address_transactions_rpc(
            "0x" + "ab" * 20,
            after_block=0,
            router=_FakeRouter(),
        )

    assert "after_block" in str(exc.value), "error must say how to narrow it"
    assert calls == ["eth_blockNumber"], "no log fetches should be attempted"


def test_rpc_sweep_allows_a_narrow_incremental_window():
    """The cap must not break the normal post-cursor case."""
    from cerebro_mcp.tools.semantic.graph_explorer import transactions as tx

    head = 40_000_000

    class _FakeClient:
        def request(self, method, params):
            if method == "eth_blockNumber":
                return hex(head)
            return []

    class _FakeRouter:
        standard = _FakeClient()

    rows, got_head = tx._discover_address_transactions_rpc(
        "0x" + "ab" * 20,
        after_block=head - 1000,
        router=_FakeRouter(),
    )
    assert got_head == head
    assert rows == []


def test_get_performance_stats_clamps_last_n(monkeypatch):
    """last_n<=0 must not read every trace in the retention window."""
    import cerebro_mcp.tools.governance.reasoning as R

    seen = {}

    def _spy(last_n):
        seen["last_n"] = last_n
        return []

    monkeypatch.setattr(R, "_list_session_files", _spy)

    mcp = FastMCP("stats")
    R.register_reasoning_tools(mcp)
    fn = mcp._tool_manager._tools["get_performance_stats"].fn

    for requested in (0, -1, 10_000):
        # Sync here: registered on a bare FastMCP, so the global installer
        # has not wrapped it.
        fn(requested)
        assert 1 <= seen["last_n"] <= R.MAX_PERFORMANCE_STATS_SESSIONS, (
            f"last_n={requested} was not clamped (got {seen['last_n']})"
        )


def test_verify_numbers_rejects_an_oversized_claim_batch():
    """Check queries run serially, so the batch must be capped."""
    import json as _json

    from cerebro_mcp.tools.governance import cross_check

    mcp = FastMCP("verify")
    cross_check.register_cross_check_tools(mcp, None)
    fn = mcp._tool_manager._tools["verify_numbers"].fn

    claims = [
        {"label": f"c{i}", "value": 1.0, "formula": "a", "components": {"a": 1.0}}
        for i in range(cross_check.MAX_CLAIMS_PER_CALL + 1)
    ]
    out = fn(_json.dumps(claims))
    assert "exceeds" in out and str(cross_check.MAX_CLAIMS_PER_CALL) in out
