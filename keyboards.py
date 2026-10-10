from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models import TransactionCallback


def extend_button() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Продлить", callback_data="extend")
    return kb.as_markup()


def paid_button(transaction_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="Я оплатил",
        callback_data=TransactionCallback(action="paid", transaction_id=transaction_id),
    )
    kb.adjust(1)
    return kb.as_markup()


def confirm_button(transaction_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="Подтвердить оплату",
        callback_data=TransactionCallback(action="confirm", transaction_id=transaction_id),
    )
    return kb.as_markup()
