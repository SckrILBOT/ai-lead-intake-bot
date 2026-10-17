from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.keyboards import NEW_LEAD_CB, RESET_CB, start_keyboard
from database.db import Database

router = Router(name="start")

GREETING = (
    "Здравствуйте{name}! 👋\n\n"
    "Я AI-консультант студии разработки и AI-автоматизации. Помогу разобраться, "
    "что можно автоматизировать в вашем бизнесе, и оформлю заявку для менеджера.\n\n"
    "Мы делаем:\n"
    "• Telegram-ботов\n"
    "• AI-агентов на базе LLM\n"
    "• интеграции с CRM и автоматизацию процессов\n\n"
    "Пишите текстом или запишите голосовое 🎤 — расскажите, какая у вас задача."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = f", {message.from_user.first_name}" if message.from_user else ""
    await message.answer(GREETING.format(name=name), reply_markup=start_keyboard())


@router.message(Command("reset"))
async def cmd_reset(message: Message, db: Database) -> None:
    await db.reset(message.from_user.id)
    await message.answer("🧹 Контекст очищен. Начнём с чистого листа — расскажите о задаче.")


@router.callback_query(F.data.in_({RESET_CB, NEW_LEAD_CB}))
async def cb_reset(callback: CallbackQuery, db: Database) -> None:
    await db.reset(callback.from_user.id)
    text = (
        "📝 Отлично, оформим новую заявку. Расскажите, что нужно сделать?"
        if callback.data == NEW_LEAD_CB
        else "🧹 Контекст очищен. Расскажите о задаче — текстом или голосом."
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text)
