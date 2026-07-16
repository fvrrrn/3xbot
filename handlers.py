import logging
import uuid
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiosqlite import Connection

import db
import xui
from config import settings
from keyboards import (
    admin_hosts_menu,
    admin_menu,
    admin_plans_menu,
    back_to_menu,
    hosts_menu,
    key_detail_menu,
    main_menu,
    my_keys_menu,
    payment_menu,
    plans_menu,
)
from models import Host, Plan, Transaction
from states import AdminAddHost, AdminAddPlan, AdminBroadcast, Buy, Extend

logger = logging.getLogger(__name__)
router = Router()


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return (
            message.from_user is not None
            and message.from_user.id in settings.telegram_admin_ids
        )


# ── /start ────────────────────────────────────────────────────────────────────


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Добро пожаловать!\n\nВыберите действие:", reply_markup=main_menu()
    )


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery) -> None:

    # TODO:
    # Diagnostics:
    # 1. Cannot access attribute "edit_text" for class "InaccessibleMessage"
    #      Attribute "edit_text" is unknown [reportAttributeAccessIssue]
    # 2. "edit_text" is not a known attribute of "None" [reportOptionalMemberAccess]
    await call.message.edit_text("Выберите действие:", reply_markup=main_menu())


# ── my keys ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "my_keys")
async def cb_my_keys(call: CallbackQuery, connection: Connection) -> None:
    try:
        hosts = await db.get_hosts(connection)
    except Exception:
        await call.message.edit_text(
            "У вас нет активных ключей.\n\nКупите подписку в разделе 🛒 Купить.",
            reply_markup=back_to_menu(),
        )
        return

    user_id = call.from_user.id
    now_ms = int(datetime.now().timestamp() * 1000)
    keys: list[tuple[str, int]] = []

    for host in hosts:
        try:
            client = await xui.get_client(host, f"{user_id}@{host.host_name}")
            if client.enable and client.expiry_ms > now_ms:
                keys.append((host.host_name, client.expiry_ms))
        except Exception:
            continue

    if not keys:
        await call.message.edit_text(
            "У вас нет активных ключей.\n\nКупите подписку в разделе 🛒 Купить.",
            reply_markup=back_to_menu(),
        )
        return

    await call.message.edit_text(
        "🔑 <b>Ваши ключи:</b>", reply_markup=my_keys_menu(keys), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("key:"))
async def cb_key_detail(call: CallbackQuery, connection: Connection) -> None:
    # TODO: "split" is not a known attribute of "None" [reportOptionalMemberAccess]
    host_name = call.data.split(":", 1)[1]
    try:
        host = await db.get_host(host_name, connection)
    except Exception:
        await call.answer("Сервер не найден", show_alert=True)
        return

    email = f"{call.from_user.id}@{host_name}"
    try:
        client = await xui.get_client(host, email)
    except Exception:
        await call.answer("Ключ не найден", show_alert=True)
        return

    expiry = datetime.fromtimestamp(client.expiry_ms / 1000).strftime("%d.%m.%Y %H:%M")
    await call.message.edit_text(
        f"🔑 <b>Ключ — {host_name}</b>\n\n"
        f"⏳ Действует до: {expiry}\n\n"
        f"<code>{client.sub_id}</code>",
        reply_markup=key_detail_menu(host_name),
        parse_mode="HTML",
    )


# ── buy flow ──────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "buy")
async def cb_buy(
    call: CallbackQuery, state: FSMContext, connection: Connection
) -> None:
    try:
        hosts = await db.get_hosts(connection)
    except Exception:
        await call.answer("Нет доступных серверов", show_alert=True)
        return
    await state.set_state(Buy.host)
    await call.message.edit_text("🖥 Выберите сервер:", reply_markup=hosts_menu(hosts))


@router.callback_query(Buy.host, F.data.startswith("host:"))
async def cb_buy_host(
    call: CallbackQuery, state: FSMContext, connection: Connection
) -> None:
    host_name = call.data.split(":")[1]
    try:
        plans = await db.get_plans(connection, host_name)
    except Exception:
        await call.answer("Нет тарифов для этого сервера", show_alert=True)
        return
    await state.update_data(host_name=host_name)
    await state.set_state(Buy.plan)
    await call.message.edit_text("📦 Выберите тариф:", reply_markup=plans_menu(plans))


@router.callback_query(Buy.plan, F.data.startswith("plan:"))
async def cb_buy_plan(
    call: CallbackQuery, state: FSMContext, connection: Connection
) -> None:
    plan_id = int(call.data.split(":")[1])
    try:
        plan = await db.get_plan(plan_id, connection)
    except Exception:
        await call.answer("Тариф не найден", show_alert=True)
        return

    tx = Transaction(
        id=str(uuid.uuid4()),
        tg_id=call.from_user.id,
        plan_id=plan_id,
        amount=plan.price,
        status="pending",
        created_at=datetime.now(),
    )
    try:
        await db.create_transaction(tx, connection)
    except Exception:
        await call.answer("Ошибка создания платежа", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text(
        f"💳 <b>Оплата</b>\n\n"
        f"Тариф: {plan.plan_name}\n"
        f"Сумма: <b>{plan.price:.0f}₽</b>\n\n"
        f"Нажмите кнопку ниже для оплаты. После успешной оплаты ключ придёт автоматически.",
        reply_markup=payment_menu(
            call.from_user.id, plan.price, settings.yoomoney_receiver
        ),
        parse_mode="HTML",
    )


# ── extend flow ───────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("extend:"))
async def cb_extend(
    call: CallbackQuery, state: FSMContext, connection: Connection
) -> None:
    host_name = call.data.split(":", 1)[1]
    try:
        plans = await db.get_plans(connection, host_name)
    except Exception:
        await call.answer("Нет тарифов для продления", show_alert=True)
        return
    await state.update_data(host_name=host_name)
    await state.set_state(Extend.confirm)
    await call.message.edit_text(
        "📦 Выберите тариф для продления:", reply_markup=plans_menu(plans)
    )


@router.callback_query(Extend.confirm, F.data.startswith("plan:"))
async def cb_extend_plan(
    call: CallbackQuery, state: FSMContext, connection: Connection
) -> None:
    plan_id = int(call.data.split(":")[1])
    try:
        plan = await db.get_plan(plan_id, connection)
    except Exception:
        await call.answer("Тариф не найден", show_alert=True)
        return

    tx = Transaction(
        id=str(uuid.uuid4()),
        tg_id=call.from_user.id,
        plan_id=plan_id,
        amount=plan.price,
        status="pending",
        created_at=datetime.now(),
    )
    try:
        await db.create_transaction(tx, connection)
    except Exception:
        await call.answer("Ошибка создания платежа", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text(
        f"💳 <b>Продление подписки</b>\n\n"
        f"Тариф: {plan.plan_name}\n"
        f"Сумма: <b>{plan.price:.0f}₽</b>\n\n"
        f"После оплаты подписка будет продлена автоматически.",
        reply_markup=payment_menu(
            call.from_user.id, plan.price, settings.yoomoney_receiver
        ),
        parse_mode="HTML",
    )


# ── support ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "support_info")
async def cb_support_info(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "💬 <b>Поддержка</b>\n\n"
        "Напишите /support чтобы открыть тикет. Ваше сообщение будет передано в поддержку.\n"
        "Обычное время ответа — до 24 часов.",
        reply_markup=back_to_menu(),
        parse_mode="HTML",
    )


@router.message(Command("support"))
async def cmd_support(message: Message, bot: Bot, connection: Connection) -> None:
    user_id = message.from_user.id

    try:
        thread_id = await db.get_support_thread(user_id, connection)
    except Exception:
        try:
            topic = await bot.create_forum_topic(
                settings.support_group_id,
                name=f"{message.from_user.full_name} ({user_id})",
            )
            thread_id = topic.message_thread_id
        except Exception as e:
            logger.error("create_forum_topic failed: %s", e)
            await message.answer("Ошибка создания тикета. Попробуйте позже.")
            return
        await db.save_support_thread(user_id, thread_id, connection)

    await message.answer("✅ Тикет открыт. Мы ответим в ближайшее время.")
    try:
        await bot.forward_message(
            settings.support_group_id,
            message.chat.id,
            message.message_id,
            message_thread_id=thread_id,
        )
    except Exception as e:
        logger.error("forward to support failed: %s", e)


@router.message(F.chat.type == "private", ~F.text.startswith("/"), StateFilter(None))
async def forward_to_support(
    message: Message, bot: Bot, connection: Connection
) -> None:
    try:
        thread_id = await db.get_support_thread(message.from_user.id, connection)
    except Exception:
        return
    try:
        await bot.forward_message(
            settings.support_group_id,
            message.chat.id,
            message.message_id,
            message_thread_id=thread_id,
        )
    except Exception as e:
        logger.error("forward to support failed: %s", e)


@router.message(F.chat.id == settings.support_group_id, F.reply_to_message.is_not(None))
async def support_reply(message: Message, bot: Bot, connection: Connection) -> None:
    if message.message_thread_id is None:
        return
    try:
        user_id = await db.get_user_by_thread(message.message_thread_id, connection)
    except Exception:
        return
    try:
        await bot.copy_message(user_id, message.chat.id, message.message_id)
    except Exception as e:
        logger.error("copy_message to user failed: %s", e)


# ── admin panel ───────────────────────────────────────────────────────────────


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "🔧 <b>Панель администратора</b>", reply_markup=admin_menu(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:menu", IsAdmin())
async def cb_admin_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "🔧 <b>Панель администратора</b>", reply_markup=admin_menu(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:stats", IsAdmin())
async def cb_admin_stats(call: CallbackQuery, connection: Connection) -> None:
    try:
        s = await db.get_stats(connection)
    except Exception:
        await call.answer("Ошибка загрузки статистики", show_alert=True)
        return
    await call.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"💳 Транзакций: {s['transactions']}\n"
        f"💰 Выручка: {s['revenue']:.0f}₽",
        reply_markup=admin_menu(),
        parse_mode="HTML",
    )


# ── admin: hosts ──────────────────────────────────────────────────────────────


@router.callback_query(F.data == "admin:hosts", IsAdmin())
async def cb_admin_hosts(call: CallbackQuery, connection: Connection) -> None:
    try:
        hosts = await db.get_hosts(connection)
    except Exception:
        hosts = []
    await call.message.edit_text(
        "🖥 <b>Хосты</b>", reply_markup=admin_hosts_menu(hosts), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("admin:delhost:"), IsAdmin())
async def cb_admin_delhost(call: CallbackQuery, connection: Connection) -> None:
    host_name = call.data.split(":", 2)[2]
    try:
        await db.delete_host(host_name, connection)
    except Exception:
        await call.answer("Ошибка удаления", show_alert=True)
        return
    await call.answer(f"Хост {host_name} удалён")
    try:
        hosts = await db.get_hosts(connection)
    except Exception:
        hosts = []
    await call.message.edit_text(
        "🖥 <b>Хосты</b>", reply_markup=admin_hosts_menu(hosts), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:addhost", IsAdmin())
async def cb_admin_addhost(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminAddHost.host_name)
    await call.message.edit_text(
        "Введите <b>имя хоста</b> (уникальное):", parse_mode="HTML"
    )


@router.message(AdminAddHost.host_name, IsAdmin())
async def fsm_host_name(message: Message, state: FSMContext) -> None:
    await state.update_data(host_name=message.text.strip())
    await state.set_state(AdminAddHost.host_url)
    await message.answer(
        "Введите <b>URL панели</b> (напр. https://1.2.3.4:2053):", parse_mode="HTML"
    )


@router.message(AdminAddHost.host_url, IsAdmin())
async def fsm_host_url(message: Message, state: FSMContext) -> None:
    await state.update_data(host_url=message.text.strip())
    await state.set_state(AdminAddHost.api_token)
    await message.answer(
        "Введите <b>API токен</b> (Settings → Security → API Token):", parse_mode="HTML"
    )


@router.message(AdminAddHost.api_token, IsAdmin())
async def fsm_host_api_token(message: Message, state: FSMContext) -> None:
    await state.update_data(api_token=message.text.strip())
    await state.set_state(AdminAddHost.inbound_id)
    await message.answer("Введите <b>ID inbound</b> (число):", parse_mode="HTML")


@router.message(AdminAddHost.inbound_id, IsAdmin())
async def fsm_host_inbound(message: Message, state: FSMContext) -> None:
    try:
        inbound_id = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число")
        return
    await state.update_data(inbound_id=inbound_id)
    await state.set_state(AdminAddHost.public_hostname)
    await message.answer(
        "Введите <b>публичный hostname</b> (или '-' чтобы пропустить):",
        parse_mode="HTML",
    )


@router.message(AdminAddHost.public_hostname, IsAdmin())
async def fsm_host_pub_hostname(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    await state.update_data(public_hostname=None if val == "-" else val)
    await state.set_state(AdminAddHost.public_url)
    await message.answer(
        "Введите <b>публичный URL подписки</b> (или '-'):", parse_mode="HTML"
    )


@router.message(AdminAddHost.public_url, IsAdmin())
async def fsm_host_pub_url(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    await state.update_data(public_url=None if val == "-" else val)
    await state.set_state(AdminAddHost.additional_inbound_ids)
    await message.answer(
        "Дополнительные inbound ID через запятую (или '-'):", parse_mode="HTML"
    )


@router.message(AdminAddHost.additional_inbound_ids, IsAdmin())
async def fsm_host_extra_inbounds(
    message: Message, state: FSMContext, connection: Connection
) -> None:
    val = message.text.strip()
    extra: list[int] = []
    if val != "-":
        try:
            extra = [int(x.strip()) for x in val.split(",") if x.strip()]
        except ValueError:
            await message.answer("Введите числа через запятую или '-'")
            return

    data = await state.get_data()
    await state.clear()

    host = Host(
        host_name=data["host_name"],
        host_url=data["host_url"],
        api_token=data["api_token"],
        inbound_id=data["inbound_id"],
        public_hostname=data.get("public_hostname"),
        public_url=data.get("public_url"),
        additional_inbound_ids=extra,
    )
    try:
        await db.add_host(host, connection)
    except Exception:
        await message.answer("Ошибка добавления хоста")
        return
    await message.answer(
        f"✅ Хост <b>{host.host_name}</b> добавлен.",
        reply_markup=admin_menu(),
        parse_mode="HTML",
    )


# ── admin: plans ──────────────────────────────────────────────────────────────


@router.callback_query(F.data == "admin:plans", IsAdmin())
async def cb_admin_plans(call: CallbackQuery, connection: Connection) -> None:
    try:
        plans = await db.get_plans(connection)
    except Exception:
        plans = []
    await call.message.edit_text(
        "📦 <b>Тарифы</b>", reply_markup=admin_plans_menu(plans), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("admin:delplan:"), IsAdmin())
async def cb_admin_delplan(call: CallbackQuery, connection: Connection) -> None:
    plan_id = int(call.data.split(":")[2])
    try:
        await db.delete_plan(plan_id, connection)
    except Exception:
        await call.answer("Ошибка удаления", show_alert=True)
        return
    await call.answer("Тариф удалён")
    try:
        plans = await db.get_plans(connection)
    except Exception:
        plans = []
    await call.message.edit_text(
        "📦 <b>Тарифы</b>", reply_markup=admin_plans_menu(plans), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:addplan", IsAdmin())
async def cb_admin_addplan(
    call: CallbackQuery, state: FSMContext, connection: Connection
) -> None:
    try:
        hosts = await db.get_hosts(connection)
    except Exception:
        await call.answer("Сначала добавьте хост", show_alert=True)
        return
    names = ", ".join(h.host_name for h in hosts)
    await state.set_state(AdminAddPlan.host_name)
    await call.message.edit_text(
        f"Введите <b>имя хоста</b> из списка:\n{names}", parse_mode="HTML"
    )


@router.message(AdminAddPlan.host_name, IsAdmin())
async def fsm_plan_host(
    message: Message, state: FSMContext, connection: Connection
) -> None:
    try:
        await db.get_host(message.text.strip(), connection)
    except Exception:
        await message.answer("Хост не найден. Введите точное имя.")
        return
    await state.update_data(host_name=message.text.strip())
    await state.set_state(AdminAddPlan.plan_name)
    await message.answer("Введите <b>название тарифа</b>:", parse_mode="HTML")


@router.message(AdminAddPlan.plan_name, IsAdmin())
async def fsm_plan_name(message: Message, state: FSMContext) -> None:
    await state.update_data(plan_name=message.text.strip())
    await state.set_state(AdminAddPlan.months)
    await message.answer("Введите <b>количество месяцев</b>:", parse_mode="HTML")


@router.message(AdminAddPlan.months, IsAdmin())
async def fsm_plan_months(message: Message, state: FSMContext) -> None:
    try:
        months = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число")
        return
    await state.update_data(months=months)
    await state.set_state(AdminAddPlan.price)
    await message.answer("Введите <b>цену</b> (₽):", parse_mode="HTML")


@router.message(AdminAddPlan.price, IsAdmin())
async def fsm_plan_price(
    message: Message, state: FSMContext, connection: Connection
) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Введите число")
        return

    data = await state.get_data()
    await state.clear()

    plan = Plan(
        id=0,
        host_name=data["host_name"],
        plan_name=data["plan_name"],
        months=data["months"],
        price=price,
    )
    try:
        await db.add_plan(plan, connection)
    except Exception:
        await message.answer("Ошибка добавления тарифа")
        return
    await message.answer(
        f"✅ Тариф <b>{plan.plan_name}</b> добавлен.",
        reply_markup=admin_menu(),
        parse_mode="HTML",
    )


# ── admin: broadcast ──────────────────────────────────────────────────────────


@router.callback_query(F.data == "admin:broadcast", IsAdmin())
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminBroadcast.message)
    await call.message.edit_text("Введите текст для рассылки (HTML):")


@router.message(AdminBroadcast.message, IsAdmin())
async def fsm_broadcast(
    message: Message, state: FSMContext, bot: Bot, connection: Connection
) -> None:
    await state.clear()
    try:
        hosts = await db.get_hosts(connection)
    except Exception:
        await message.answer("Нет хостов для рассылки")
        return

    tg_ids: set[int] = set()
    for host in hosts:
        try:
            clients = await xui.get_all_clients(host)
            for c in clients:
                if c.tg_id:
                    tg_ids.add(c.tg_id)
                    tg_ids.add(tg_id)
        except Exception:
            continue

    sent, failed = 0, 0
    for tg_id in tg_ids:
        try:
            await bot.send_message(tg_id, message.text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"📢 Рассылка завершена\n✅ Отправлено: {sent}\n❌ Ошибок: {failed}",
        reply_markup=admin_menu(),
    )
