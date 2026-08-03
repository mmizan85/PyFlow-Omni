"""utils/: human_size, atomic_write_text, Throttler, retry_with_backoff, EventBus."""
from __future__ import annotations

import asyncio
import time

import pytest

from pyflow_omni.utils.async_utils import EventBus, Throttler, retry_with_backoff
from pyflow_omni.utils.file_utils import atomic_write_text, human_size


@pytest.mark.parametrize(
    "num_bytes,expected_prefix",
    [(0, "0 B"), (512, "512 B"), (1536, "1.5 KB"), (5 * 1024 * 1024, "5.0 MB"), (2 * 1024**3, "2.0 GB")],
)
def test_human_size(num_bytes, expected_prefix):
    assert human_size(num_bytes) == expected_prefix


def test_atomic_write_creates_parent_dirs_and_content(tmp_path):
    target = tmp_path / "nested" / "dir" / "file.txt"
    atomic_write_text(target, "hello world")
    assert target.read_text() == "hello world"


def test_atomic_write_never_leaves_a_partial_file_visible(tmp_path):
    target = tmp_path / "config.yaml"
    atomic_write_text(target, "version: 1")
    atomic_write_text(target, "version: 2")  # overwrite
    assert target.read_text() == "version: 2"
    # no leftover .tmp files
    assert list(tmp_path.glob("*.tmp")) == []


async def test_retry_with_backoff_succeeds_after_failures():
    attempts = []

    async def flaky():
        attempts.append(time.monotonic())
        if len(attempts) < 3:
            raise ValueError("not yet")
        return "ok"

    result = await retry_with_backoff(flaky, max_retries=3, base_delay=0.01, max_delay=0.05)
    assert result == "ok"
    assert len(attempts) == 3


async def test_retry_with_backoff_raises_after_exhausting_retries():
    async def always_fails():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await retry_with_backoff(always_fails, max_retries=2, base_delay=0.01, max_delay=0.02)


async def test_retry_with_backoff_calls_on_retry_hook():
    seen = []

    async def flaky():
        if len(seen) < 2:
            raise ValueError("x")
        return "done"

    async def on_retry(attempt_no, exc):
        seen.append(attempt_no)

    await retry_with_backoff(flaky, max_retries=3, base_delay=0.01, on_retry=on_retry)
    assert seen == [1, 2]


def test_throttler_allows_first_call_then_suppresses_rapid_repeats():
    throttler = Throttler(interval=0.2)
    assert throttler.should_fire("task-1") is True
    assert throttler.should_fire("task-1") is False  # too soon
    assert throttler.should_fire("task-2") is True  # different key, independent


def test_throttler_allows_again_after_interval_elapses():
    throttler = Throttler(interval=0.05)
    assert throttler.should_fire("t") is True
    time.sleep(0.06)
    assert throttler.should_fire("t") is True


async def test_event_bus_delivers_to_all_subscribers():
    received = []

    async def sub_a(payload):
        received.append(("a", payload))

    async def sub_b(payload):
        received.append(("b", payload))

    bus = EventBus()
    bus.subscribe("topic", sub_a)
    bus.subscribe("topic", sub_b)
    await bus.publish("topic", 42)

    assert ("a", 42) in received and ("b", 42) in received


async def test_event_bus_isolates_a_failing_subscriber():
    received = []

    async def bad_sub(payload):
        raise RuntimeError("boom")

    async def good_sub(payload):
        received.append(payload)

    bus = EventBus()
    bus.subscribe("topic", bad_sub)
    bus.subscribe("topic", good_sub)
    await bus.publish("topic", "value")  # must not raise, despite bad_sub

    assert received == ["value"]


async def test_event_bus_unsubscribe():
    calls = []

    async def sub(payload):
        calls.append(payload)

    bus = EventBus()
    unsubscribe = bus.subscribe("topic", sub)
    await bus.publish("topic", 1)
    unsubscribe()
    await bus.publish("topic", 2)

    assert calls == [1]
