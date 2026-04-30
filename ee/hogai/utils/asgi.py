"""FOSS stub for ee.hogai.utils.asgi.SyncIterableToAsync.

Used in products/mcp_store/backend/proxy.py to bridge a sync iterator into
an async streaming response. Provide a real implementation since this is
on the production request path for MCP store proxying (not strictly an
AI-only feature; it's general async streaming infra).
"""

import asyncio
from typing import AsyncIterator, Iterable, TypeVar

T = TypeVar("T")


class SyncIterableToAsync(AsyncIterator[T]):
    """Wrap a sync iterable so it can be consumed via `async for`.

    Yields items one at a time, deferring the .next() calls to a thread
    pool so we don't block the event loop.
    """

    def __init__(self, sync_iterable: Iterable[T]):
        self._iter = iter(sync_iterable)

    def __aiter__(self) -> "SyncIterableToAsync[T]":
        return self

    async def __anext__(self) -> T:
        try:
            return await asyncio.to_thread(next, self._iter)
        except StopIteration:
            raise StopAsyncIteration
