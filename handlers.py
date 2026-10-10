import asyncio
import html
import logging
import uuid
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message, User
from aiohttp import ClientSession
from aiosqlite import Connection
from expression import Result

import db
import xui
from config import Settings
from keyboards import (
    confirm_button,
    paid_button,
)
from models import Transaction, TransactionCallback
from states import AdminBroadcast

logger = logging.getLogger(__name__)
router = Router()


class IsAdmin(BaseFilter):
    async def __call__(
        self, message: Message | CallbackQuery, settings: Settings
    ) -> bool:
        return (
            message.from_user is not None
            and message.from_user.id == settings.telegram_admin_id
        )


class IsSupportGroup(BaseFilter):
    async def __call__(self, message: Message, settings: Settings) -> bool:
        return message.chat.id == settings.support_group_id


@router.message(Command("start"))
async def cmd_start(
    message: Message, session: ClientSession, settings: Settings
) -> None:
    if message.from_user is None:
        logger.warning("start without sender in chat=%d", message.chat.id)
        await message.answer("Не удалось определить пользователя.")
        return
    tg_id = message.from_user.id
    email = str(tg_id)

    match await xui.get_or_create_subscription(
        email=email,
        days=settings.trial_days,
        tg_id=tg_id,
        session=session,
        panel_url=settings.xui_url,
    ):
        case Result(tag="ok", ok=subscription_url):
            maybe_sent_message = await xui.either_from_awaitable(
                message.answer(
                    str(subscription_url),
                    parse_mode="HTML",
                ),
                exception_class=Exception,
            )
            if maybe_sent_message.is_error():
                logger.error(
                    "Could not send key for user=%d: %s",
                    tg_id,
                    maybe_sent_message.error,
                )
                return

        case Result(tag="error", error=error):
            logger.error("Could not get client for user=%d: %s", tg_id, error)
            await message.answer("Не удалось получить ключ. Попробуйте позже.")


@router.callback_query(F.data.startswith("extend"))
async def cb_extend(
    call: CallbackQuery, connection: Connection, settings: Settings
) -> None:
    if call.message is None or isinstance(call.message, InaccessibleMessage):
        await call.answer("Сообщение недоступно. Откройте /start", show_alert=True)
        return
    tg_id = call.from_user.id
    tx = Transaction(
        id=str(uuid.uuid4()),
        tg_id=tg_id,
        months=settings.plan_months,
        amount=settings.plan_price_rub,
        status="pending",
        created_at=datetime.now(UTC),
    )
    try:
        await db.create_transaction(tx, connection)
    except Exception:
        logger.exception("create_transaction failed for user=%d", tg_id)
        await call.answer("Ошибка создания платежа", show_alert=True)
    else:
        await call.message.edit_text(
            f"💳 <b>Продление на {settings.plan_months} мес.</b>\n\n"
            f"Сумма: <b>{settings.plan_price_rub:.0f}₽</b>\n"
            f"Перевод: <b>{settings.payment_details}</b>\n\n"
            f"После перевода нажмите «Я оплатил» — ключ продлится "
            f"после проверки.",
            reply_markup=paid_button(tx.id),
            parse_mode="HTML",
        )
        await call.answer()


async def _prompt_confirm(
    bot: Bot, user: User, transaction_id: str, settings: Settings
) -> None:
    """Offer the admin a one-tap confirmation of a payment the user claims.

    Raises if no admin could be reached. With no payment provider behind this, an
    unnoticed claim is a user waiting forever, so the failure has to surface on
    their screen rather than only in the log.
    """
    handle = f"@{user.username}" if user.username else f"id{user.id}"
    try:
        await bot.send_message(
            chat_id=settings.telegram_admin_id,
            text=f"💰 <b>Ожидает оплаты</b>\n\n"
            f"{html.escape(user.full_name)} ({html.escape(handle)})\n"
            f"{settings.plan_price_rub:.0f}₽ за {settings.plan_months} мес.",
            reply_markup=confirm_button(transaction_id),
            parse_mode="HTML",
        )
    except Exception as e:
        raise RuntimeError(
            f"No admin reachable for transaction {transaction_id!r}"
        ) from e


@router.callback_query(TransactionCallback.filter(F.action == "paid"))
async def cb_paid(
    call: CallbackQuery,
    bot: Bot,
    connection: Connection,
    callback_data: TransactionCallback,
    settings: Settings,
) -> None:
    if call.message is None or isinstance(call.message, InaccessibleMessage):
        await call.answer("Сообщение недоступно. Откройте /start", show_alert=True)
        return
    transaction_id = str(callback_data.transaction_id)
    try:
        tx = await db.get_pending_transaction(transaction_id, connection)
    except db.PendingTransactionNotFound as e:
        logger.warning("no pending transaction %s: %s", transaction_id, e)
        await call.answer("Платёж уже обработан", show_alert=True)
        return
    except Exception:
        logger.exception("could not load transaction %s", transaction_id)
        await call.answer(
            "Не удалось проверить платёж. Попробуйте позже.", show_alert=True
        )
        return
    if tx.tg_id != call.from_user.id:
        await call.answer(
            "Этот платёж принадлежит другому пользователю", show_alert=True
        )
        return

    try:
        await _prompt_confirm(bot, call.from_user, transaction_id, settings)
    except Exception:
        logger.exception("could not reach any admin for tx=%s", transaction_id)
        await call.answer(
            "Не удалось отправить заявку. Напишите в /support", show_alert=True
        )
        return

    await call.message.edit_text(
        "⏳ <b>Платёж проверяется</b>\n\n"
        "Ключ продлится, как только перевод будет подтверждён.",
        parse_mode="HTML",
    )
    await call.answer()


# The only path to a paid extension: an admin who sees the transfer land in their
# bank app taps this. get_pending_transaction filters on status, so a second tap
# buys no second block of days.
@router.callback_query(TransactionCallback.filter(F.action == "confirm"), IsAdmin())
async def cb_confirm(
    call: CallbackQuery,
    connection: Connection,
    session: ClientSession,
    callback_data: TransactionCallback,
    settings: Settings,
) -> None:
    if call.message is None or isinstance(call.message, InaccessibleMessage):
        await call.answer("Сообщение недоступно", show_alert=True)
        return
    transaction_id = str(callback_data.transaction_id)
    try:
        tx = await db.get_pending_transaction(transaction_id, connection)
    except db.PendingTransactionNotFound as e:
        logger.warning("no pending transaction %s: %s", transaction_id, e)
        await call.answer("Платёж уже подтверждён или отменён", show_alert=True)
        return
    except Exception:
        logger.exception("could not load transaction %s", transaction_id)
        await call.answer(
            "Не удалось проверить платёж. Попробуйте позже.", show_alert=True
        )
        return

    try:
        match await xui.get_client(
            str(tx.tg_id), session=session, panel_url=settings.xui_url
        ):
            case Result(tag="ok", ok=client):
                maybe_result = await xui.extend_subscription(
                    client, tx.months * 30, session=session, panel_url=settings.xui_url
                )
            case Result(tag="error", error=error):
                logger.error("client lookup failed for user=%d: %s", tx.tg_id, error)
                await call.answer(
                    "Не удалось получить клиента. Попробуйте позже.", show_alert=True
                )
                return
    except Exception:
        logger.exception("extension failed for user=%d", tx.tg_id)
        await call.answer("Панель недоступна, попробуйте ещё раз", show_alert=True)
        return
    match maybe_result:
        case Result(tag="error", error=error):
            logger.error("extension failed for user=%d: %s", tx.tg_id, error)
            await call.answer("Панель недоступна, попробуйте ещё раз", show_alert=True)
            return

    try:
        await db.complete_transaction(tx.id, connection)
    except Exception:
        # The days are already credited. Dropping the button anyway is the safer
        # failure: a retry would extend the key a second time.
        logger.exception(
            "complete_transaction failed for user=%d, tx=%s: subscription was extended, "
            "reconcile manually",
            tx.tg_id,
            tx.id,
        )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer(f"Ключ продлён на {tx.months * 30} дней.")


@router.message(Command("support"))
async def cmd_support(
    message: Message, bot: Bot, connection: Connection, settings: Settings
) -> None:
    if message.from_user is None:
        logger.warning("support without sender in chat=%d", message.chat.id)
        await message.answer("Не удалось определить пользователя.")
        return
    user_id = message.from_user.id

    try:
        thread_id = await db.get_support_thread(user_id, connection)
    except db.SupportThreadNotFound:
        try:
            topic = await bot.create_forum_topic(
                settings.support_group_id,
                name=f"{message.from_user.full_name or user_id} ({user_id})"[:128],
            )
            thread_id = topic.message_thread_id
        except Exception as e:
            logger.error("create_forum_topic failed: %s", e)
            await message.answer("Ошибка создания тикета. Попробуйте позже.")
            return
        try:
            await db.save_support_thread(user_id, thread_id, connection)
        except Exception:
            logger.exception(
                "could not save support topic=%d for user=%d", thread_id, user_id
            )
            await message.answer("Ошибка сохранения тикета. Попробуйте позже.")
            return
    except Exception:
        logger.exception("could not look up support thread for user=%d", user_id)
        await message.answer("Поддержка временно недоступна. Попробуйте позже.")
        return

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


# Only forward outside an FSM so support does not consume broadcast input.
@router.message(F.chat.type == "private", ~F.text.startswith("/"), StateFilter(None))
async def forward_to_support(
    message: Message, bot: Bot, connection: Connection, settings: Settings
) -> None:
    if message.from_user is None:
        logger.warning("support message without sender in chat=%d", message.chat.id)
        return
    try:
        thread_id = await db.get_support_thread(message.from_user.id, connection)
    except db.SupportThreadNotFound:
        await message.answer("Чтобы написать в поддержку, откройте тикет: /support")
        return
    except Exception:
        logger.exception("support lookup failed for user=%d", message.from_user.id)
        await message.answer("Поддержка временно недоступна. Попробуйте позже.")
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
        await message.answer(
            "Не удалось отправить сообщение в поддержку. Попробуйте позже."
        )


@router.message(IsSupportGroup(), F.reply_to_message.is_not(None))
async def support_reply(message: Message, bot: Bot, connection: Connection) -> None:
    if message.message_thread_id is None:
        logger.debug("ignoring support reply outside a topic")
        return
    try:
        user_id = await db.get_user_by_thread(message.message_thread_id, connection)
    except db.SupportThreadNotFound:
        logger.debug("no user mapped to support topic=%d", message.message_thread_id)
        return
    except Exception:
        logger.exception(
            "support user lookup failed for topic=%d", message.message_thread_id
        )
        await message.answer("Не удалось найти пользователя. Попробуйте позже.")
        return
    try:
        # Copy hides the support group's forwarding attribution from the user.
        await bot.copy_message(user_id, message.chat.id, message.message_id)
    except Exception as e:
        logger.error("copy_message to user failed: %s", e)
        await message.answer("Не удалось доставить ответ пользователю.")


@router.message(Command("broadcast"), IsAdmin())
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminBroadcast.message)
    await message.answer("Отправьте сообщение для рассылки...")


@router.message(AdminBroadcast.message, IsAdmin())
async def fsm_broadcast(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: ClientSession,
    settings: Settings,
) -> None:
    await state.clear()
    try:
        maybe_clients = await xui.get_all_clients(
            session=session, panel_url=settings.xui_url
        )
    except Exception:
        logger.exception("broadcast could not list clients")
        await message.answer("Не удалось получить список пользователей")
        return
    match maybe_clients:
        case Result(tag="ok", ok=clients):
            tg_ids = {c.tg_id for c in clients if c.tg_id}
        case Result(tag="error", error=error):
            logger.error("broadcast could not list clients: %s", error)
            await message.answer("Не удалось получить список пользователей")
            return

    sent, failed = 0, 0
    for tg_id in tg_ids:
        while True:
            try:
                await bot.copy_message(tg_id, message.chat.id, message.message_id)
            except TelegramRetryAfter as e:
                logger.warning("broadcast rate limited: wait %d seconds", e.retry_after)
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                logger.warning("broadcast to %d failed: %s", tg_id, e)
                failed += 1
                break
            else:
                sent += 1
                break
        await asyncio.sleep(0.05)
    await message.answer(
        f"📢 Рассылка завершена\n✅ Отправлено: {sent}\n❌ Ошибок: {failed}",
    )
