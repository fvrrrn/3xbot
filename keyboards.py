from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def extend_button() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Продлить", callback_data="extend")
    return kb.as_markup()


def paid_button(transaction_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Я оплатил", callback_data=f"paid:{transaction_id}")
    kb.button(text="Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def back_button() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data="menu")
    return kb.as_markup()


def confirm_button(transaction_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить оплату", callback_data=f"confirm:{transaction_id}")
    return kb.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Статистика", callback_data="admin:stats")
    kb.button(text="Рассылка", callback_data="admin:broadcast")
    kb.adjust(2)
    return kb.as_markup()
