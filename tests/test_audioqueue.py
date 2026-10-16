"""The live broadcaster's audio queue: rank order, shared jobs, skipped stale work, bounded threads."""

import asyncio
import threading
import time

import pytest

from earshot.live.audioqueue import AudioQueue, Skipped


def test_jobs_run_in_rank_order_once_a_slot_frees():
    order: list[str] = []
    gate = threading.Event()

    async def go():
        q = AudioQueue(workers=1)
        blocker = asyncio.create_task(q.run("block", (1, 1, 0), gate.wait))
        await asyncio.sleep(0.05)  # the one slot is taken
        jobs = [
            asyncio.create_task(q.run(name, r, order.append, name))
            for name, r in (("summary", (1, 1, -25)), ("goal", (0, 0, -100)), ("shot", (1, 0, -80)))
        ]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(blocker, *jobs)

    asyncio.run(go())
    assert order == ["goal", "shot", "summary"]  # never-drop, then preempting, then by priority


def test_identical_jobs_share_one_run():
    calls: list[int] = []

    def encode():
        calls.append(1)
        time.sleep(0.05)
        return "file.m4a"

    async def go():
        q = AudioQueue(workers=2)
        return await asyncio.gather(*(q.run("serve:L1:app_voice", (1, 1, 0), encode) for _ in range(5)))

    assert asyncio.run(go()) == ["file.m4a"] * 5
    assert calls == [1]


def test_a_stale_job_is_skipped_at_its_turn():
    async def go():
        q = AudioQueue(workers=1)
        with pytest.raises(Skipped):
            await q.run("serve:L2:one_ear", (1, 1, -15), lambda: "x", wanted=lambda: False)
        return q

    q = asyncio.run(go())
    assert (q.ran, q.skipped) == (0, 1)


def test_no_more_than_the_worker_count_run_at_once():
    live = peak = 0
    lock = threading.Lock()

    def job():
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.03)
        with lock:
            live -= 1

    async def go():
        q = AudioQueue(workers=2)
        await asyncio.gather(*(q.run(f"k{i}", (1, 1, -i), job) for i in range(8)))

    asyncio.run(go())
    assert peak == 2


def test_errors_reach_every_waiter_and_free_the_slot():
    def boom():
        raise RuntimeError("encoder missing")

    async def go():
        q = AudioQueue(workers=1)
        results = await asyncio.gather(
            q.run("k", (1, 1, 0), boom), q.run("k", (1, 1, 0), boom), return_exceptions=True
        )
        after = await q.run("other", (1, 1, 0), lambda: "ok")  # the slot came back
        return results, after

    results, after = asyncio.run(go())
    assert all(isinstance(r, RuntimeError) for r in results)
    assert after == "ok"
