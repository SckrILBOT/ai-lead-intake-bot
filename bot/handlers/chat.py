from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone, tzinfo
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.keyboards import new_lead_keyboard
from config import Settings
from database.db import Database
from schemas.lead import Lead
from services.lead_dispatcher import LeadDispatcher
from services.llm_service import LLMService, UserMeta
from services.speechkit_service import SpeechKitService

logger = logging.getLogger(__name__)
router = Router(name="chat")

TG_LIMIT = 4096
ERROR_REPLY = (
    "😔 Сервис ответа сейчас перегружен. Попробуйте отправить сообщение ещё раз "
    "через минуту."
)

# One lock per user: prevents double processing (and double leads) on fast messages.
_locks: "WeakValueDictionary[int, asyncio.Lock]" = WeakValueDictionary()


def _user_lock(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


def _tz(name: str) -> tzinfo:
    if name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        logger.warning("Unknown timezone %r, falling back to UTC", name)
        return timezone.utc


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def confirmation_text(lead: Lead) -> str:
    return (
        f"✅ Спасибо, {lead.client_name}! Заявка оформлена и передана менеджеру.\n\n"
        f"Задача: {lead.project_description}\n"
        f"Бюджет/сроки: {lead.budget_or_timeline}\n"
        f"Контакт: {lead.contact_info}\n\n"
        "Менеджер свяжется с вами в ближайшее время. А пока я на связи — "
        "можно задать вопросы по услугам. Чтобы оформить ещё одну заявку, "
        "нажмите кнопку ниже."
    )


async def process_user_text(
    message: Message,
    text: str,
    *,
    db: Database,
    llm: LLMService,
    dispatcher: LeadDispatcher,
    settings: Settings,
) -> None:
    user = message.from_user
    async with _user_lock(user.id):
        status = await db.get_status(user.id)
        completed = status == "completed"
        await db.add_message(user.id, "user", text)
        history = await db.get_history(user.id)
        meta = UserMeta(telegram_id=user.id, first_name=user.first_name, username=user.username)

        try:
            async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
                result = await llm.chat(history, meta, allow_submit=not completed)
        except Exception:  # noqa: BLE001
            logger.exception("LLM call failed for user %s", user.id)
            await message.answer(ERROR_REPLY)
            return

        if result.lead is None:
            await db.add_message(user.id, "model", result.text)
            for chunk in split_text(result.text):
                await message.answer(chunk)
            return

        lead = Lead(
            **result.lead.model_dump(),
            created_at=datetime.now(_tz(settings.timezone)),
            telegram_id=user.id,
            telegram_username=user.username,
        )
        dispatch = await dispatcher.dispatch(lead)
        if not dispatch.saved:
            await message.answer(
                "😔 Не удалось сохранить заявку из-за технической ошибки. "
                "Попробуйте ещё раз чуть позже."
            )
            return

        await db.set_status(user.id, "completed")
        reply = confirmation_text(lead)
        await db.add_message(user.id, "model", reply)
        await message.answer(reply, reply_markup=new_lead_keyboard())
        logger.info(
            "Lead from %s saved: priority=%s delivered=%s backup=%s",
            user.id,
            lead.lead_priority,
            dispatch.delivered,
            dispatch.backed_up,
        )


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(
    message: Message,
    db: Database,
    llm: LLMService,
    dispatcher: LeadDispatcher,
    settings: Settings,
) -> None:
    await process_user_text(
        message, message.text, db=db, llm=llm, dispatcher=dispatcher, settings=settings
    )


@router.message(F.voice)
async def on_voice(
    message: Message,
    bot: Bot,
    db: Database,
    llm: LLMService,
    stt: SpeechKitService,
    dispatcher: LeadDispatcher,
    settings: Settings,
) -> None:
    voice = message.voice
    if voice.duration > settings.voice_max_seconds:
        await message.answer(
            f"🎤 Голосовое слишком длинное. Пожалуйста, уложитесь в "
            f"{settings.voice_max_seconds} сек. или напишите текстом."
        )
        return

    try:
        async with ChatActionSender.record_voice(bot=bot, chat_id=message.chat.id):
            buffer = io.BytesIO()
            await bot.download(voice, destination=buffer)
            transcript = await stt.transcribe(buffer.getvalue())
    except Exception:  # noqa: BLE001
        logger.exception("Voice processing failed for user %s", message.from_user.id)
        await message.answer(ERROR_REPLY)
        return

    if not transcript:
        await message.answer("🎤 Не удалось разобрать голосовое. Попробуйте ещё раз или напишите текстом.")
        return

    await message.answer(f"🎤 Распознал: «{transcript[:500]}{'…' if len(transcript) > 500 else ''}»")
    await process_user_text(
        message, transcript, db=db, llm=llm, dispatcher=dispatcher, settings=settings
    )


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Я понимаю текст и голосовые сообщения 🙂 Напишите или запишите, что вам нужно.")
