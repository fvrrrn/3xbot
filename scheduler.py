import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiosqlite import Connection

import db
import xui
from config import settings

logger = logging.getLogger(__name__)


async def _check_expiry(bot: Bot, connection: Connection) -> None:
    try:
        hosts = await db.get_hosts(connection)
    except Exception as e:
        logger.warning("no hosts found %s", e)
        return

    now_ms = int(datetime.now().timestamp() * 1000)
    threshold_ms = settings.expiry_notify_h * 3600 * 1000

    for host in hosts:
        try:
            clients = await xui.get_all_clients(host)
        except Exception:
            continue

        for client in clients:
            if client.tg_id == 0:
                continue
            if client.expiry_ms == 0 or client.expiry_ms <= now_ms:
                continue
            if client.expiry_ms - now_ms > threshold_ms:
                continue

            expiry = datetime.fromtimestamp(client.expiry_ms / 1000).strftime(
                "%d.%m.%Y %H:%M"
            )
            try:
                await bot.send_message(
                    client.tg_id,
                    f"⚠️ <b>Подписка истекает {expiry}</b>\n\n"
                    f"🖥 Сервер: {host.host_name}\n\n"
                    f"Продлите подписку в разделе 🔑 Мои ключи.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("notify user %d failed: %s", client.tg_id, e)


async def run_scheduler(bot: Bot, connection: Connection) -> None:
    while True:
        try:
            await _check_expiry(bot, connection)
        except Exception as e:
            logger.error("scheduler tick failed: %s", e)
        await asyncio.sleep(settings.scheduler_time_h * 60 * 60)
