"""Async helper utilities: pub/sub event bus, retry-with-backoff, and UI-update throttling.

These three primitives are what let the engines stay completely ignorant of
whether anyone is listening (TUI, --no-tui console, tests): they publish
events and the caller decides how, and how often, to react to them.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, DefaultDict, Hashable, List, Optional, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
EventCallback = Callable[[Any], Awaitable[None]]


class EventBus:
    """Lightweight asyncio-native publish/subscribe bus.

    Decouples engine progress/log producers from UI consumers so neither
    side needs a direct reference to the other. Any number of subscribers
    may listen to the same topic (e.g. multiple screens both watching
    "progress" during a batch).
    """

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[EventCallback]] = defaultdict(list)

    def subscribe(self, topic: str, callback: EventCallback) -> Callable[[], None]:
        """Subscribe `callback` to `topic`. Returns an unsubscribe function."""
        self._subscribers[topic].append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers[topic].remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    async def publish(self, topic: str, payload: Any) -> None:
        """Publish `payload` to every subscriber of `topic`.

        A misbehaving subscriber is logged and isolated — it must never be
        able to take down the producer (an engine mid-download).
        """
        for callback in list(self._subscribers.get(topic, ())):
            try:
                await callback(payload)
            except Exception:
                logger.exception("EventBus subscriber for topic %r raised", topic)


async def retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    on_retry: Optional[Callable[[int, BaseException], Awaitable[None]]] = None,
) -> T:
    """Call `func()`, retrying up to `max_retries` times with exponential backoff.

    Implements the "Smart Retry" requirement: up to 3 attempts by default,
    doubling delay each time, capped at `max_delay`. Re-raises the final
    exception once retries are exhausted so the caller can mark the task
    permanently failed.
    """
    attempt = 0
    while True:
        try:
            return await func()
        except retry_on as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if on_retry is not None:
                await on_retry(attempt, exc)
            await asyncio.sleep(delay)


class Throttler:
    """Rate-limits how often a keyed event may fire.

    Used to keep the TUI responsive when hundreds of tasks are emitting
    progress simultaneously: each task's updates are coalesced to at most
    one UI refresh per `interval` seconds. Callers should always apply the
    *latest* value when `should_fire` returns True — this class only
    tracks timing, not payloads.
    """

    def __init__(self, interval: float = 0.15) -> None:
        self.interval = interval
        self._last_fired: dict[Hashable, float] = {}

    def should_fire(self, key: Hashable) -> bool:
        now = time.monotonic()
        last = self._last_fired.get(key, 0.0)
        if now - last >= self.interval:
            self._last_fired[key] = now
            return True
        return False

    def reset(self, key: Hashable) -> None:
        self._last_fired.pop(key, None)
