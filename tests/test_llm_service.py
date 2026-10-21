import json

import httpx
import openai
import pytest

from services import llm_service
from services.llm_service import LLMService, UserMeta, build_messages, model_uri
from tests.conftest import VALID_LEAD, Reply, call_response, fake_client, text_response

META = UserMeta(telegram_id=42, first_name="Анна", username="anna")
REQUEST = httpx.Request("POST", "https://example.test")


def system_of(call: dict) -> str:
    return call["instructions"] if "instructions" in call else call["messages"][0]["content"]


def tool_names(call: dict) -> list[str]:
    tools = call.get("tools") or []
    return [t["name"] if "name" in t else t["function"]["name"] for t in tools]


def last_item(call: dict) -> dict:
    return call["input"][-1] if "input" in call else call["messages"][-1]


async def test_plain_reply(api):
    client, endpoint = fake_client([text_response("Здравствуйте! Чем помочь?")], api)
    result = await LLMService(client, "m", api=api).chat([("user", "Привет")], META)

    assert result.text == "Здравствуйте! Чем помочь?"
    assert result.lead is None
    call = endpoint.calls[0]
    assert call["model"] == "m"
    assert tool_names(call) == ["submit_lead"]
    assert "@anna" in system_of(call)


async def test_valid_submit_returns_lead(api):
    client, _ = fake_client([call_response("submit_lead", VALID_LEAD)], api)
    result = await LLMService(client, "m", api=api).chat([("user", "Да, отправляйте")], META)

    assert result.lead is not None
    assert result.lead.lead_priority == "High"
    assert result.lead.client_name == "Анна"


async def test_invalid_fields_are_sent_back_to_model(api):
    bad = call_response("submit_lead", VALID_LEAD | {"client_name": "  ", "lead_priority": "Urgent"})
    client, endpoint = fake_client([bad, text_response("Подскажите, как вас зовут?")], api)
    result = await LLMService(client, "m", api=api).chat([("user", "Хочу бота")], META)

    assert result.lead is None
    assert result.text == "Подскажите, как вас зовут?"
    feedback = last_item(endpoint.calls[1])
    if api == "responses":
        assert feedback["type"] == "function_call_output"
        payload = feedback["output"]
    else:
        assert feedback["role"] == "tool"
        payload = feedback["content"]
    assert json.loads(payload)["status"] == "error"
    assert "client_name" in payload


async def test_broken_json_arguments_are_sent_back_to_model(api):
    client, endpoint = fake_client(
        [Reply(call=("submit_lead", "not json")), text_response("Уточните, пожалуйста.")], api
    )
    result = await LLMService(client, "m", api=api).chat([("user", "Хочу бота")], META)

    assert result.lead is None
    assert result.text == "Уточните, пожалуйста."
    assert len(endpoint.calls) == 2


async def test_completed_session_has_no_tool(api):
    client, endpoint = fake_client([text_response("Заявка уже у менеджера.")], api)
    await LLMService(client, "m", api=api).chat([("user", "Спасибо!")], META, allow_submit=False)

    call = endpoint.calls[0]
    assert not call.get("tools")
    assert "уже оформлена" in system_of(call)


async def test_tool_call_ignored_when_submit_not_allowed(api):
    client, _ = fake_client([call_response("submit_lead", VALID_LEAD)], api)
    result = await LLMService(client, "m", api=api).chat(
        [("user", "Спасибо!")], META, allow_submit=False
    )

    assert result.lead is None
    assert result.text == llm_service.FALLBACK_REPLY


async def test_retry_on_server_error_and_rate_limit(api, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(llm_service.asyncio, "sleep", no_sleep)
    busy = openai.InternalServerError(
        "busy", response=httpx.Response(503, request=REQUEST), body=None
    )
    limited = openai.RateLimitError(
        "limit", response=httpx.Response(429, request=REQUEST), body=None
    )
    client, endpoint = fake_client([busy, limited, text_response("Ок")], api)
    result = await LLMService(client, "m", max_retries=3, api=api).chat(
        [("user", "Привет")], META
    )

    assert result.text == "Ок"
    assert len(endpoint.calls) == 3


async def test_client_error_is_not_retried(api):
    bad = openai.BadRequestError("bad", response=httpx.Response(400, request=REQUEST), body=None)
    client, endpoint = fake_client([bad, text_response("не дойдёт")], api)

    with pytest.raises(openai.BadRequestError):
        await LLMService(client, "m", max_retries=3, api=api).chat([("user", "Привет")], META)
    assert len(endpoint.calls) == 1


def test_unknown_api_mode_rejected():
    with pytest.raises(ValueError):
        LLMService(object(), "m", api="grpc")


def test_model_uri():
    assert model_uri("b1g", "deepseek-v4.1-flash") == "gpt://b1g/deepseek-v4.1-flash"
    assert model_uri("b1g", "gpt://other/model") == "gpt://other/model"


def test_build_messages_merges_same_role_and_maps_roles():
    messages = build_messages([("user", "a"), ("user", "b"), ("model", "c")])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "a\n\nb"
