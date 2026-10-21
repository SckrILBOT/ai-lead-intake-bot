from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from database.db import Database


@dataclass
class Reply:
    """What the fake model answers: text and/or a function call."""

    text: str = ""
    call: tuple[str, str] | None = None  # (name, raw JSON arguments)


def text_response(text: str) -> Reply:
    return Reply(text=text)


def call_response(name: str, args: dict) -> Reply:
    return Reply(call=(name, json.dumps(args, ensure_ascii=False)))


def _responses_shape(reply: Reply) -> Any:
    output = []
    if reply.call:
        output.append(
            SimpleNamespace(
                type="function_call", call_id="call_1", name=reply.call[0], arguments=reply.call[1]
            )
        )
    if reply.text:
        part = SimpleNamespace(type="output_text", text=reply.text)
        output.append(SimpleNamespace(type="message", content=[part]))
    return SimpleNamespace(output=output)


def _chat_shape(reply: Reply) -> Any:
    tool_calls = None
    if reply.call:
        fn = SimpleNamespace(name=reply.call[0], arguments=reply.call[1])
        tool_calls = [SimpleNamespace(id="call_1", function=fn)]
    message = SimpleNamespace(content=reply.text or None, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


VALID_LEAD = {
    "client_name": "Анна",
    "contact_info": "Telegram @anna",
    "project_description": "Бот для записи клиентов в салон",
    "budget_or_timeline": "100 000 ₽, 2 недели",
    "ai_summary": "Салону нужен бот записи с напоминаниями.",
    "lead_priority": "High",
    "category": "Telegram Bot",
}


@dataclass
class FakeEndpoint:
    """Stands in for client.responses / client.chat.completions; pops queued replies."""

    queue: list[Any]
    shape: Any
    calls: list[dict] = field(default_factory=list)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return self.shape(item)


def fake_client(replies: list[Any], api: str = "responses") -> tuple[Any, FakeEndpoint]:
    """Returns (client, endpoint) where endpoint records the kwargs of every call."""
    if api == "responses":
        endpoint = FakeEndpoint(replies, _responses_shape)
        return SimpleNamespace(responses=endpoint), endpoint
    endpoint = FakeEndpoint(replies, _chat_shape)
    return SimpleNamespace(chat=SimpleNamespace(completions=endpoint)), endpoint


@pytest.fixture(params=["responses", "chat"])
def api(request) -> str:
    return request.param


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", history_limit=15)
    await database.connect()
    yield database
    await database.close()
