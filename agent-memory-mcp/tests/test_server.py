"""Tests for the `_run` thread-offloading/timeout wrapper in server.py.

This is the fix for the "reads hang behind a stuck write" bug: FastMCP calls
a synchronous tool function directly on its single asyncio event loop with no
thread-pool offloading, so a blocking file access (AGENT_MEMORY_DIR is
commonly an iCloud/Dropbox-synced folder, where a read/write can stall for
minutes) previously froze the entire server, not just that one call.
"""

import asyncio
import time

import pytest

from agent_memory_mcp import server


@pytest.fixture(autouse=True)
def clear_project_locks():
    """Each test calls asyncio.run() separately, creating a fresh event loop
    per test — but _project_locks is module-level state that would otherwise
    persist across tests. An asyncio.Lock binds to whichever event loop first
    uses it, so a lock left over from a previous test's (now-closed) loop
    breaks the next test that reuses the same project name. This is a
    test-isolation artifact only: in production, main() runs one event loop
    for the server's entire lifetime, so _project_locks is always used from
    the single loop it was created in."""
    server._project_locks.clear()
    yield
    server._project_locks.clear()


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
    """Regression test for the hang bug: a slow blocking call must not freeze
    other concurrent _run calls. Without thread offloading, a synchronous
    tool function ran directly on the event loop and froze the whole server
    for the duration of any blocking I/O — this is exactly what was observed
    as "reads fine, writes hanging, and a read blocked behind the hung
    write," even though nothing in storage.py takes a lock.
    """
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
    # The fast call must finish (and append) before the slow one — proof
    # they ran concurrently on separate threads rather than one blocking
    # the other on a shared event loop.
    assert order == ["fast", "slow"]


def test_run_serializes_concurrent_calls_for_same_project(monkeypatch):
    """Regression test for the race the thread-offloading fix introduced:
    two concurrent calls for the same project must not both read the same
    starting state and clobber each other on write — the project lock in
    _run must serialize them.
    """
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)
    board = {"cards": []}

    def add_card(title):
        current = list(board["cards"])
        time.sleep(0.1)  # widen the read-modify-write window to make a race likely if unguarded
        current.append(title)
        board["cards"] = current
        return f"added {title}"

    async def go():
        return await asyncio.gather(
            server._run(add_card, "A", project="p"),
            server._run(add_card, "B", project="p"),
        )

    asyncio.run(go())
    assert sorted(board["cards"]) == ["A", "B"]


def test_run_shares_lock_across_normalized_project_spellings(monkeypatch):
    """The lock must key on the same normalization storage.py itself uses to
    resolve a project name — otherwise 'JobSearch' and 'job-search' would get
    different locks and still race, reproducing the exact split this whole
    project-identity fix exists to prevent.
    """
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)
    board = {"cards": []}

    def add_card(title):
        current = list(board["cards"])
        time.sleep(0.1)
        current.append(title)
        board["cards"] = current
        return f"added {title}"

    async def go():
        return await asyncio.gather(
            server._run(add_card, "A", project="JobSearch"),
            server._run(add_card, "B", project="job-search"),
        )

    asyncio.run(go())
    assert sorted(board["cards"]) == ["A", "B"]


def test_run_different_projects_still_run_concurrently(monkeypatch):
    """The per-project lock must not regress the original fix: unrelated
    projects should still run fully in parallel, not get serialized behind
    each other's locks."""
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)

    def slow():
        time.sleep(0.2)
        return "done"

    async def go():
        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            server._run(slow, project="alpha"),
            server._run(slow, project="beta"),
        )
        return asyncio.get_event_loop().time() - start

    elapsed = asyncio.run(go())
    assert elapsed < 0.35  # ~0.2s if parallel, ~0.4s if wrongly serialized


def test_run_timeout_does_not_release_lock_early(monkeypatch):
    """A timed-out call must keep holding its project's lock until the
    abandoned background task actually finishes — releasing it early (e.g. a
    bare anyio.move_on_after cancelling the call) would let a subsequent call
    for the same project start a new read-modify-write cycle while the first
    write is still in flight, reintroducing the same lost-update race across
    a timeout-then-retry sequence.
    """
    board = {"cards": []}

    def add_card(title):
        current = list(board["cards"])
        time.sleep(0.2)
        current.append(title)
        board["cards"] = current
        return f"added {title}"

    async def go():
        first = await server._run(add_card, "A", project="p")
        # Give the second call enough time to wait out the first's lock hold.
        monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)
        second = await server._run(add_card, "B", project="p")
        return first, second

    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 0.05)
    first_result, second_result = asyncio.run(go())
    assert "still working" in first_result.lower()
    assert board["cards"] == ["A", "B"]


def test_run_locks_multiple_projects_and_serializes_shared_target(monkeypatch):
    """Regression test for the kanban_rename destination race: a call that
    touches two projects (its source and destination) must serialize against
    another call touching either of those same projects — including at a
    destination that neither call's source-only lock would otherwise cover.
    Reproduced directly before this fix: two concurrent kanban_rename calls
    into the same new_name both passed the "does it exist" check and the
    second clobbered the first.
    """
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)
    board = {"cards": []}

    def claim_destination(source, title):
        current = list(board["cards"])
        time.sleep(0.1)
        current.append(f"{source}:{title}")
        board["cards"] = current
        return f"claimed by {source}"

    async def go():
        return await asyncio.gather(
            server._run(claim_destination, "A", "x", project=("A", "Shared")),
            server._run(claim_destination, "B", "y", project=("B", "Shared")),
        )

    asyncio.run(go())
    assert sorted(board["cards"]) == ["A:x", "B:y"]


def test_run_multi_lock_ordering_does_not_deadlock(monkeypatch):
    """_locks_for sorts its slugs so two calls requesting the same two
    projects in opposite argument order still acquire them in the same
    global order. Without that sort, this test would deadlock instead of
    completing (each call holding the lock the other one wants next).
    """
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)

    def noop():
        time.sleep(0.05)
        return "ok"

    async def go():
        return await asyncio.gather(
            server._run(noop, project=("alpha", "beta")),
            server._run(noop, project=("beta", "alpha")),
        )

    assert asyncio.run(go()) == ["ok", "ok"]


def test_run_dedupes_locks_for_same_normalized_project(monkeypatch):
    """A call whose 'projects' all normalize to the same slug (e.g. a pure
    recasing rename, project='JobSearch' + new_name='jobsearch') must not
    try to acquire the same asyncio.Lock twice — an asyncio.Lock isn't
    reentrant, so that would deadlock a single call against itself.
    """
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)

    async def go():
        return await server._run(lambda: "ok", project=("JobSearch", "jobsearch"))

    assert asyncio.run(go()) == "ok"


def test_run_without_project_does_not_wait_on_any_lock(monkeypatch):
    """Regression test for 'a wedged write blocks reads for that project
    forever': a call made with no `project=` kwarg (as every read-only tool
    wrapper in server.py now calls _run) must never wait behind a
    concurrent locked call for the same project's name, however long that
    other call takes to finish.
    """
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 5.0)

    def slow_write():
        time.sleep(0.3)
        return "write-done"

    def fast_unlocked_read():
        return "read-done"

    async def go():
        write_task = asyncio.create_task(server._run(slow_write, project="p"))
        await asyncio.sleep(0.05)  # let the write actually acquire its lock first
        t0 = asyncio.get_event_loop().time()
        read_result = await server._run(fast_unlocked_read)  # no project= at all
        elapsed = asyncio.get_event_loop().time() - t0
        write_result = await write_task
        return read_result, elapsed, write_result

    read_result, elapsed, write_result = asyncio.run(go())
    assert read_result == "read-done"
    assert elapsed < 0.2  # nowhere near the slow write's 0.3s hold, since it never waited
    assert write_result == "write-done"


def test_run_timeout_logs_late_completion(monkeypatch):
    """A call that eventually finishes after its caller already timed out
    must not vanish silently (previously: an untouched asyncio Task whose
    exception, if any, is never retrieved). _run attaches a done-callback in
    the timeout branch specifically so this is observable."""
    monkeypatch.setattr(server, "_SLOW_OPERATION_TIMEOUT_SECONDS", 0.05)
    logged = []
    monkeypatch.setattr(server.log, "error", lambda event, **kw: logged.append((event, kw)))
    monkeypatch.setattr(server.log, "info", lambda event, **kw: logged.append((event, kw)))

    def eventually_fails():
        time.sleep(0.2)
        raise ValueError("simulated late failure")

    async def go():
        result = await server._run(eventually_fails)
        # give the abandoned task time to actually finish and fire its
        # done-callback after we've already stopped waiting on it
        await asyncio.sleep(0.3)
        return result

    result = asyncio.run(go())
    assert "still working" in result.lower()
    assert ("delayed_operation_failed", {"error": "simulated late failure"}) in logged
