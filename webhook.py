import hashlib
import hmac
import logging
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import quote

from aiogram import Bot
from aiohttp import web
from aiosqlite import Connection

import db
import xui
from models import ProvisionResult, YooMoneyRequest

logger = logging.getLogger(__name__)


def _is_signature_verified(form_data: Mapping[str, str], secret: str) -> bool:
    params = {k: v for k, v in form_data.items() if k != "sign"}
    sorted_str = "&".join(
        f"{k}={quote(str(v), safe='')}" for k, v in sorted(params.items())
    )
    expected = hmac.new(
        secret.encode(), sorted_str.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, form_data.get("sign", ""))


def _key_text(result: ProvisionResult, plan_name: str) -> str:
    expiry = datetime.fromtimestamp(result.expiry_ms / 1000).strftime("%d.%m.%Y %H:%M")
    return (
        f"✅ <b>Ключ готов!</b>\n\n"
        f"📦 Тариф: {plan_name}\n"
        f"⏳ Действует до: {expiry}\n\n"
        f"<code>{result.connection_string}</code>"
    )


async def handle_youmoney_callback(request: web.Request) -> web.Response:
    form_data = await request.post()
    logger.info("yoomoney incoming: %s", dict(form_data))

    try:
        yoo_money_request = YooMoneyRequest.model_validate(form_data)
    except Exception as e:
        logger.warning("webhook parse failed: %s", e)
        return web.Response(status=422)

    tg_id = yoo_money_request.tg_id
    connection: Connection = request.app["connection"]
    bot: Bot = request.app["bot"]
    secret: str = request.app["yoomoney_secret"]

    if not _is_signature_verified(form_data, secret):
        logger.warning("webhook sig failed for user=%d", tg_id)
        return web.Response(status=403)

    try:
        tx = await db.find_pending_transaction(
            tg_id, yoo_money_request.paid_amount, connection
        )
    except Exception as e:
        logger.warning("no pending transaction for user=%d: %s", tg_id, e)
        return web.Response(status=402)

    try:
        plan = await db.get_plan(tx.plan_id, connection)
    except Exception as e:
        logger.warning("plan %d not found: %s", tx.plan_id, e)
        return web.Response(status=422)

    try:
        host = await db.get_host(plan.host_name, connection)
    except Exception as e:
        logger.warning("host %s not found: %s", plan.host_name, e)
        return web.Response(status=422)

    email = f"{tg_id}@{host.host_name}"
    try:
        result = await xui.provision_key(host, email, plan.months * 30, tg_id)
    except Exception as e:
        logger.error("provision failed for user=%d: %s", tg_id, e)
        return web.Response()

    try:
        await db.complete_transaction(tx.id, connection)
    except Exception as e:
        logger.error("complete_transaction failed for user=%d: %s", tg_id, e)
        return web.Response(status=500)

    try:
        await bot.send_message(
            tg_id, _key_text(result, plan.plan_name), parse_mode="HTML"
        )
    except Exception as e:
        logger.error("notify failed for user=%d: %s", tg_id, e)

    return web.Response(status=202)
