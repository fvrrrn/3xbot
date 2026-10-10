import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from aiogram import Bot
from aiohttp import ClientSession
from expression import effect

import xui
from config import Settings
from keyboards import extend_button

logger = logging.getLogger(__name__)


@effect.async_result[None, Exception]()
async def _check_expiry(
    bot: Bot, session: ClientSession, settings: Settings
) -> AsyncGenerator[None, None]:
    maybe_clients = await xui.get_all_clients(session, panel_url=settings.xui_url)
    clients = await maybe_clients

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    threshold_ms = settings.expiry_notify_h * 3600 * 1000

    for client in clients:
        if client.tg_id == 0:
            continue
        if client.expiry_ms <= now_ms:
            continue
        if (client.expiry_ms - now_ms) > threshold_ms:
            continue

        expiry = datetime.fromtimestamp(client.expiry_ms / 1000, tz=UTC).strftime(
            "%d.%m.%Y %H:%M UTC"
        )
        if (
            result := await xui.either_from_awaitable(
                bot.send_message(
                    client.tg_id,
                    f"⚠️ <b>Подписка истекает {expiry}</b>",
                    reply_markup=extend_button(),
                    parse_mode="HTML",
                ),
                exception_class=Exception,
            )
        ).is_error():
            logger.warning("notify user %d failed: %s", client.tg_id, result.error)

    yield None


async def run_scheduler(bot: Bot, session: ClientSession, settings: Settings) -> None:
    while True:
        if (result := await _check_expiry(bot, session, settings)).is_error():
            logger.error("scheduler tick failed: %s", result.error)
        await asyncio.sleep(settings.scheduler_time_h * 60 * 60)
