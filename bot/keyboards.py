from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

RESET_CB = "session:reset"
NEW_LEAD_CB = "session:new"


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Начать заново / Очистить контекст", callback_data=RESET_CB)]
        ]
    )


def new_lead_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📝 Новая заявка", callback_data=NEW_LEAD_CB)]]
    )
