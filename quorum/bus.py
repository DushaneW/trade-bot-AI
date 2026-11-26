"""In-process message bus. Analysts publish, traders and the UI subscribe.

Every message is kept in a bounded history so the dashboard can explain
why a bot acted the way it did.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any
from collections.abc import Callable

from .models import now_ms

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]


class Bus:
    def __init__(self, history: int = 500) -> None:
        self._subs: dict[str, list[Handler]] = {}
        self._queues: set[asyncio.Queue] = set()
        self.history: deque[dict[str, Any]] = deque(maxlen=history)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register a handler for a topic, or "*" for every topic."""
        self._subs.setdefault(topic, []).append(handler)

    def stream(self) -> asyncio.Queue:
        """A queue that receives every message; used by websocket clients."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues.add(q)
        return q

    def unstream(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def publish(self, topic: str, payload: dict[str, Any], *, keep: bool = True) -> None:
        msg = {"topic": topic, "ts": now_ms(), "data": payload}
        if keep:
            self.history.append(msg)
        for handler in self._subs.get(topic, []) + self._subs.get("*", []):
            try:
                handler(msg)
            except Exception:  # one broken subscriber must not stall the bus
                log.exception("Subscriber failed on topic %s", topic)
        for q in list(self._queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client; it will resync from history
