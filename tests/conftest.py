from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from database.db import Database


def text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)])
            )
        ]
    )


def call_response(name: str, args: dict) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
                )
            )
        ]
    )


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
class FakeModels:
    """Stands in for client.aio.models; returns queued responses in order."""

    responses: list[Any]
    calls: list[dict] = field(default_factory=list)

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fake_client(responses: list[Any]) -> tuple[Any, FakeModels]:
    models = FakeModels(responses)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), models


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", history_limit=15)
    await database.connect()
    yield database
    await database.close()
