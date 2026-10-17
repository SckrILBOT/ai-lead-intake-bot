"""End-to-end dialog flow with fake Telegram message and fake Gemini."""

from types import SimpleNamespace

from bot.handlers.chat import process_user_text, split_text
from config import Settings
from services.lead_dispatcher import BackupStorage, LeadDispatcher
from services.llm_service import LLMService
from tests.conftest import VALID_LEAD, call_response, fake_client, text_response


class FakeBot:
    id = 1  # ChatActionSender logs bot.id

    async def send_chat_action(self, **kwargs):
        return True


class FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=7, first_name="Анна", username="anna")
        self.chat = SimpleNamespace(id=7)
        self.bot = FakeBot()
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


async def test_dialog_submits_once_then_consults(db, tmp_path):
    client, models = fake_client(
        [
            text_response("Расскажите о задаче?"),
            call_response("submit_lead", VALID_LEAD),
            text_response("Пожалуйста! Обращайтесь."),
        ]
    )
    settings = Settings(telegram_bot_token="t", gemini_api_key="k", data_dir=tmp_path)
    deps = dict(
        db=db,
        llm=LLMService(client, "m"),
        dispatcher=LeadDispatcher(BackupStorage(settings.backup_path)),
        settings=settings,
    )
    msg = FakeMessage()

    await process_user_text(msg, "Привет, нужен бот", **deps)
    await process_user_text(msg, "Да, оформляйте", **deps)
    await process_user_text(msg, "Спасибо!", **deps)

    assert msg.answers[0][0] == "Расскажите о задаче?"
    assert msg.answers[1][0].startswith("✅ Спасибо, Анна!")
    assert msg.answers[1][1] is not None  # «Новая заявка» button
    assert await db.get_status(7) == "completed"
    # third turn: tool is not offered -> no duplicate lead
    assert not models.calls[2]["config"].tools
    assert settings.backup_path.exists()
    assert settings.backup_path.read_text(encoding="utf-8").count('"client_name"') == 1


def test_settings_treat_empty_as_unset():
    s = Settings(
        telegram_bot_token="t",
        gemini_api_key="k",
        spreadsheet_id="",
        google_service_account_json="  ",
        smtp_host="",
        smtp_port="",
    )
    assert not s.sheets_enabled and not s.email_enabled
    assert s.smtp_port == 587
    assert s.gemini_model == "gemini-3.5-flash"


def test_split_text():
    parts = split_text("слово " * 2000, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts).replace(" ", "") == ("слово" * 2000)
