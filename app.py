import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiosqlite import Connection

import db
import xui
from config import settings
from handlers import router
from scheduler import run_scheduler

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


class ConnectionMiddleware(BaseMiddleware):
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["connection"] = self.connection
        return await handler(event, data)


def build_app() -> web.Application:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    async def on_startup(app: web.Application) -> None:
        connection = await db.get_connection(settings.db_path)
        dispatcher.update.middleware(ConnectionMiddleware(connection))
        await bot.set_webhook(settings.telegram_webhook_url.encoded_string())
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="support", description="Написать в поддержку"),
            ]
        )
        await bot.set_my_description("Description")
        await bot.set_my_short_description("Short description")
        app["connection"] = connection
        app["bot"] = bot
        asyncio.create_task(run_scheduler(bot))
        logger.info("started")

    async def on_cleanup(app: web.Application) -> None:
        await bot.delete_webhook()
        await xui.close()
        await app["connection"].close()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    SimpleRequestHandler(dispatcher=dispatcher, bot=bot).register(
        app, path=settings.telegram_webhook_url.path
    )
    setup_application(app, dispatcher, bot=bot)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=settings.telegram_webhook_url.port)
