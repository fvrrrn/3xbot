import asyncio
import logging
from datetime import datetime

from aiogram import Bot

import xui
from config import settings
from keyboards import extend_button

logger = logging.getLogger(__name__)


async def _check_expiry(bot: Bot) -> None:
    try:
        clients = await xui.get_all_clients()
    except Exception as e:
        logger.warning("could not list clients: %s", e)
        return

    now_ms = int(datetime.now().timestamp() * 1000)
    threshold_ms = settings.expiry_notify_h * 3600 * 1000

    for client in clients:
        if client.tg_id == 0:
            continue
        if client.expiry_ms <= now_ms:
            continue
        if (client.expiry_ms - now_ms) > threshold_ms:
            continue

        expiry = datetime.fromtimestamp(client.expiry_ms / 1000).strftime(
            "%d.%m.%Y %H:%M"
        )
        try:
            await bot.send_message(
                client.tg_id,
                f"⚠️ <b>Подписка истекает {expiry}</b>",
                reply_markup=extend_button(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("notify user %d failed: %s", client.tg_id, e)


async def run_scheduler(bot: Bot) -> None:
    while True:
        try:
            await _check_expiry(bot)
        except Exception as e:
            logger.error("scheduler tick failed: %s", e)
        await asyncio.sleep(settings.scheduler_time_h * 60 * 60)
