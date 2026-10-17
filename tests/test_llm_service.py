from google.genai import errors

from services import llm_service
from services.llm_service import LLMService, UserMeta, build_contents
from tests.conftest import VALID_LEAD, call_response, fake_client, text_response

META = UserMeta(telegram_id=42, first_name="Анна", username="anna")


async def test_plain_reply():
    client, models = fake_client([text_response("Здравствуйте! Чем помочь?")])
    result = await LLMService(client, "m").chat([("user", "Привет")], META)

    assert result.text == "Здравствуйте! Чем помочь?"
    assert result.lead is None
    config = models.calls[0]["config"]
    assert config.automatic_function_calling.disable is True
    assert config.tools[0].function_declarations[0].name == "submit_lead"
    assert "@anna" in config.system_instruction


async def test_valid_submit_returns_lead():
    client, _ = fake_client([call_response("submit_lead", VALID_LEAD)])
    result = await LLMService(client, "m").chat([("user", "Да, отправляйте")], META)

    assert result.lead is not None
    assert result.lead.lead_priority == "High"
    assert result.lead.client_name == "Анна"


async def test_invalid_submit_is_sent_back_to_model():
    bad = VALID_LEAD | {"client_name": "  ", "lead_priority": "Urgent"}
    client, models = fake_client(
        [call_response("submit_lead", bad), text_response("Подскажите, как вас зовут?")]
    )
    result = await LLMService(client, "m").chat([("user", "Хочу бота")], META)

    assert result.lead is None
    assert result.text == "Подскажите, как вас зовут?"
    second = models.calls[1]["contents"]
    fr = second[-1].parts[0].function_response
    assert fr.name == "submit_lead"
    assert fr.response["status"] == "error"
    assert "client_name" in fr.response["detail"]


async def test_completed_session_has_no_tool():
    client, models = fake_client([text_response("Заявка уже у менеджера.")])
    await LLMService(client, "m").chat([("user", "Спасибо!")], META, allow_submit=False)

    config = models.calls[0]["config"]
    assert not config.tools
    assert "уже оформлена" in config.system_instruction


async def test_retry_on_server_error(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(llm_service.asyncio, "sleep", no_sleep)
    client, models = fake_client(
        [errors.ServerError(503, {"error": {"message": "busy"}}), text_response("Ок")]
    )
    result = await LLMService(client, "m", max_retries=3).chat([("user", "Привет")], META)

    assert result.text == "Ок"
    assert len(models.calls) == 2


async def test_transcribe_sends_audio():
    client, models = fake_client([text_response(" Нужен бот для салона ")])
    text = await LLMService(client, "m").transcribe(b"OggS...", "audio/ogg")

    assert text == "Нужен бот для салона"
    part = models.calls[0]["contents"][0].parts[0]
    assert part.inline_data.mime_type == "audio/ogg"
    assert part.inline_data.data == b"OggS..."


def test_build_contents_merges_same_role():
    contents = build_contents([("user", "a"), ("user", "b"), ("model", "c")])
    assert [c.role for c in contents] == ["user", "model"]
    assert contents[0].parts[0].text == "a\n\nb"
