from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models import Host, Plan


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔑 Мои ключи", callback_data="my_keys")
    kb.button(text="🛒 Купить", callback_data="buy")
    kb.button(text="💬 Поддержка", callback_data="support_info")
    kb.adjust(2)
    return kb.as_markup()


def hosts_menu(hosts: list[Host]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for h in hosts:
        kb.button(text=h.host_name, callback_data=f"host:{h.host_name}")
    kb.button(text="◀️ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def plans_menu(plans: list[Plan]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in plans:
        kb.button(text=f"{p.plan_name} — {p.price:.0f}₽", callback_data=f"plan:{p.id}")
    kb.button(text="◀️ Назад", callback_data="buy")
    kb.adjust(1)
    return kb.as_markup()


def payment_menu(user_id: int, amount: float, receiver: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    link = f"https://yoomoney.ru/transfer/quickpay?receiver={receiver}&sum={amount:.2f}&label={user_id}&comment=vpn"
    kb.button(text="💳 Оплатить", url=link)
    kb.button(text="◀️ Назад", callback_data="buy")
    kb.adjust(1)
    return kb.as_markup()


def my_keys_menu(keys: list[tuple[str, int]]) -> InlineKeyboardMarkup:
    """keys: list of (host_name, expiry_ms)"""
    kb = InlineKeyboardBuilder()
    for host_name, expiry_ms in keys:
        from datetime import datetime
        expiry = datetime.fromtimestamp(expiry_ms / 1000).strftime("%d.%m.%Y")
        kb.button(text=f"🔑 {host_name} — до {expiry}", callback_data=f"key:{host_name}")
    kb.button(text="◀️ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def key_detail_menu(host_name: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Продлить", callback_data=f"extend:{host_name}")
    kb.button(text="◀️ Назад", callback_data="my_keys")
    kb.adjust(1)
    return kb.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ В меню", callback_data="menu")
    return kb.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="admin:stats")
    kb.button(text="🖥 Хосты", callback_data="admin:hosts")
    kb.button(text="📦 Тарифы", callback_data="admin:plans")
    kb.button(text="📢 Рассылка", callback_data="admin:broadcast")
    kb.adjust(2)
    return kb.as_markup()


def admin_hosts_menu(hosts: list[Host]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for h in hosts:
        kb.button(text=f"❌ {h.host_name}", callback_data=f"admin:delhost:{h.host_name}")
    kb.button(text="➕ Добавить", callback_data="admin:addhost")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(1)
    return kb.as_markup()


def admin_plans_menu(plans: list[Plan]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in plans:
        kb.button(
            text=f"❌ {p.plan_name} ({p.host_name}) {p.price:.0f}₽",
            callback_data=f"admin:delplan:{p.id}",
        )
    kb.button(text="➕ Добавить", callback_data="admin:addplan")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(1)
    return kb.as_markup()
