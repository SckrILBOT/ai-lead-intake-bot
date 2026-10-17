from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.handlers import chat, start
from bot.middlewares.throttling import ThrottlingMiddleware
from config import get_settings
from database.db import Database
from services.email_service import EmailService
from services.lead_dispatcher import BackupStorage, LeadDispatcher
from services.llm_service import LLMService
from services.sheets_service import SheetsService

logger = logging.getLogger("lead_bot")


def build_dispatcher_service(settings) -> LeadDispatcher:
    sheets = (
        SheetsService(
            settings.google_service_account_json,
            settings.spreadsheet_id,
            settings.worksheet_name,
        )
        if settings.sheets_enabled
        else None
    )
    email = None
    if settings.email_enabled and settings.email_recipient:
        email = EmailService(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            sender=settings.smtp_from or settings.smtp_user,
            recipient=settings.email_recipient,
        )
    logger.info(
        "Channels: Google Sheets=%s, Email=%s, backup=%s",
        "ON" if sheets else "OFF (mock)",
        "ON" if email else "OFF (mock)",
        settings.backup_path,
    )
    return LeadDispatcher(BackupStorage(settings.backup_path), sheets=sheets, email=email)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    settings = get_settings()

    db = Database(settings.db_path, history_limit=settings.history_limit)
    await db.connect()

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher(
        db=db,
        llm=LLMService.from_api_key(
            settings.gemini_api_key, settings.gemini_model, settings.llm_max_retries
        ),
        dispatcher=build_dispatcher_service(settings),
        settings=settings,
    )
    dp.message.middleware(ThrottlingMiddleware(rate=settings.throttle_seconds))
    dp.include_routers(start.router, chat.router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать диалог"),
            BotCommand(command="reset", description="Очистить контекст"),
        ]
    )
    logger.info("Bot started, model: %s", settings.gemini_model)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
