from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class ThrottlingMiddleware(BaseMiddleware):
    """Drops messages sent faster than `rate` seconds apart (protects the LLM quota)."""

    def __init__(self, rate: float = 0.7, max_users: int = 10_000) -> None:
        self._rate = rate
        self._max_users = max_users
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        now = time.monotonic()
        user_id = event.from_user.id
        if now - self._last.get(user_id, 0.0) < self._rate:
            return None
        if len(self._last) >= self._max_users:
            self._last.clear()
        self._last[user_id] = now
        return await handler(event, data)
