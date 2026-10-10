import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from aiosqlite import Connection

import db
from config import Settings
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


class SessionMiddleware(BaseMiddleware):
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["session"] = self.session
        return await handler(event, data)


def build_app(settings: Settings) -> web.Application:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage(), settings=settings)
    dispatcher.include_router(router)

    async def on_startup(app: web.Application) -> None:
        connection = await db.get_connection(settings.db_path)
        app["connection"] = connection
        session = ClientSession(
            connector=TCPConnector(ssl=False),
            timeout=ClientTimeout(total=15),
            headers={"Authorization": f"Bearer {settings.xui_token}"},
        )
        app["session"] = session
        dispatcher.update.middleware(ConnectionMiddleware(connection))
        dispatcher.update.middleware(SessionMiddleware(session))
        await bot.set_webhook(str(settings.telegram_webhook_url))
        commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="support", description="Написать в поддержку"),
        ]
        await bot.set_my_commands(commands)
        await bot.set_my_commands(
            [*commands, BotCommand(command="broadcast", description="Рассылка")],
            scope=BotCommandScopeChat(chat_id=settings.telegram_admin_id),
        )
        await bot.set_my_description("Description")
        await bot.set_my_short_description("Short description")
        app["bot"] = bot
        app["scheduler"] = asyncio.create_task(run_scheduler(bot, session, settings))
        logger.info("started")

    async def on_cleanup(app: web.Application) -> None:
        if task := app.get("scheduler"):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        try:
            await bot.delete_webhook()
        finally:
            try:
                if session := app.get("session"):
                    await session.close()
            finally:
                if connection := app.get("connection"):
                    await connection.close()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    SimpleRequestHandler(dispatcher=dispatcher, bot=bot).register(
        app, path=settings.telegram_webhook_url.path
    )
    setup_application(app, dispatcher, bot=bot)
    return app


if __name__ == "__main__":
    settings = Settings()
    web.run_app(
        build_app(settings), host="0.0.0.0", port=settings.telegram_webhook_url.port
    )
