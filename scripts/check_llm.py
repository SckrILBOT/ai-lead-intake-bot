"""Check that the Yandex model from .env answers and supports function calling.

Run:  python scripts/check_llm.py   (from the project folder, inside the venv)

Tries both API modes ("responses" and "chat") and prints which one works, so you
know what to put into YANDEX_API_MODE.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings  # noqa: E402
from services.llm_service import LLMService, UserMeta  # noqa: E402

META = UserMeta(telegram_id=1, first_name="Тест", username="test_user")

CONVERSATION = [
    ("user", "Здравствуйте! Меня зовут Анна, телефон +7 900 123-45-67."),
    ("model", "Приятно познакомиться, Анна! Расскажите, какая у вас задача?"),
    ("user", "Нужен Telegram-бот для записи клиентов в салон красоты."),
    ("model", "Понял. Какой у вас примерный бюджет и сроки?"),
    ("user", "Бюджет около 100 000 рублей, запустить хотим за 2 недели. Оформляйте заявку."),
]


async def check(mode: str) -> bool:
    s = get_settings()
    llm = LLMService.from_settings(
        s.yandex_api_key, s.yandex_folder_id, s.yandex_model, max_retries=1, api=mode
    )
    print(f"\n=== API mode: {mode} ===")
    try:
        plain = await llm.chat([("user", "Привет! Чем вы занимаетесь?")], META, allow_submit=False)
        print("Обычный ответ: OK ->", plain.text[:120].replace("\n", " "))
    except Exception as exc:  # noqa: BLE001
        print("Обычный ответ: ОШИБКА ->", str(exc)[:300])
        return False
    try:
        result = await llm.chat(CONVERSATION, META)
    except Exception as exc:  # noqa: BLE001
        print("Вызов функции: ОШИБКА ->", str(exc)[:300])
        return False
    if result.lead is None:
        print("Вызов функции: модель не вызвала submit_lead. Ответ:", result.text[:200])
        return False
    print("Вызов функции: OK ->", result.lead.model_dump())
    return True


async def main() -> None:
    s = get_settings()
    print(f"Модель: {s.yandex_model}, текущий режим в .env: {s.yandex_api_mode}")
    ok = {mode: await check(mode) for mode in ("responses", "chat")}
    print("\nИтог:", ", ".join(f"{m}={'работает' if v else 'не работает'}" for m, v in ok.items()))
    if ok.get(s.yandex_api_mode):
        print(f"Текущий режим ({s.yandex_api_mode}) подходит, менять ничего не нужно.")
    elif any(ok.values()):
        best = next(m for m, v in ok.items() if v)
        print(f"Поставьте в .env: YANDEX_API_MODE={best}")
    else:
        print("Ни один режим не сработал: проверьте ключ, ID каталога, роли и название модели.")


asyncio.run(main())
