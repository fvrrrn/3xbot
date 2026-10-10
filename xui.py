import json
import logging
import random
import string
from asyncio import TimeoutError
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import yarl
from expression import Error, Ok, Result, effect
from pydantic import ValidationError

from models import (
    Client,
    ClientInfo,
    InboundOption,
    PanelResponse,
    PanelSettings,
)

logger = logging.getLogger(__name__)


async def either_from_awaitable[L: Exception, R](
    awaitable: Awaitable[R], exception_class: type[L] | tuple[type[L], ...]
) -> Result[R, L]:
    try:
        return Ok(await awaitable)
    except exception_class as exception:
        return Error(exception)


def either_from_throwable[L: Exception, R](
    function: Callable[[], R],
    exception_class: type[L] | tuple[type[L], ...],
) -> Result[R, L]:
    try:
        return Ok(function())
    except exception_class as exception:
        return Error(exception)


class PanelException(Exception):
    """Panel Exception"""


class ClientNotFound(PanelException):
    """No client with this email exists on the panel."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"client {email!r} not found")


class ClientAlreadyExists(PanelException):
    """A client with this email already exists on the panel."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"client {email!r} already exists")


def _generate_subscription_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


@effect.async_result[
    PanelSettings,
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def get_panel_settings(
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[PanelSettings, None]:
    maybe_response = await either_from_awaitable(
        session.request(
            method="POST",
            url=panel_url / "panel/api/setting/all",
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[PanelSettings].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        await Error(PanelException(info.msg))
        return

    if info.obj is None:
        await Error(PanelException("Panel response has no object"))
        return

    yield info.obj


@effect.async_result[
    list[InboundOption],
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def get_inbound_options(
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[list[InboundOption], None]:
    maybe_response = await either_from_awaitable(
        session.request(
            method="GET",
            url=panel_url / "panel/api/inbounds/options",
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[list[InboundOption]].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        await Error(PanelException(info.msg))
        return

    if info.obj is None:
        await Error(PanelException("Panel response has no object"))
        return

    yield info.obj


@effect.async_result[
    yarl.URL,
    ValueError
    | aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def get_subscription_base_url(
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[yarl.URL, None]:
    maybe_settings = await get_panel_settings(session=session, panel_url=panel_url)
    panel_settings = await maybe_settings
    if not panel_settings.sub_enable:
        await Error(
            PanelException(
                f"Subscription is not enabled. panel_settings.sub_enable={panel_settings.sub_enable}"
            )
        )
        return
    if not panel_settings.sub_cert_file:
        await Error(
            PanelException(
                f"No cert file found for subscription. panel_settings.sub_cert_file={panel_settings.sub_cert_file}"
            )
        )
        return
    if not panel_settings.sub_path:
        await Error(
            PanelException(
                f"No path found for subscription. panel_settings.sub_path={panel_settings.sub_path}"
            )
        )
        return
    maybe_url = either_from_throwable(
        lambda: (
            yarl.URL(panel_settings.sub_uri)
            if panel_settings.sub_uri
            else yarl.URL.build(
                scheme="https",
                host=panel_settings.sub_domain,
                port=panel_settings.sub_port,
                path=panel_settings.sub_path,
            )
        ),
        exception_class=ValueError,
    )
    url = await maybe_url
    yield url


@effect.async_result[
    PanelResponse[None],
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException
    | ClientAlreadyExists,
]()
async def create_client(
    email: str,
    expiry_days: int,
    tg_id: int,
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[PanelResponse[None], None]:
    sub_id = _generate_subscription_id()
    maybe_options = await get_inbound_options(session=session, panel_url=panel_url)
    options = await maybe_options
    payload = {
        "client": {
            "email": email,
            "tgId": tg_id,
            "subId": sub_id,
            "expiryTime": int(
                (datetime.now(UTC) + timedelta(days=expiry_days)).timestamp() * 1000
            ),
            "enable": True,
            "totalGB": 0,
            "limitIp": 0,
            # xtls-rprx-vision is the only non-empty VLESS flow xray defines;
            # the panel says per inbound whether it can take one.
            "flow": "xtls-rprx-vision"
            if any(o.tls_flow_capable for o in options)
            else "",
        },
        "inboundIds": [o.id for o in options],
    }
    maybe_response = await either_from_awaitable(
        session.request(
            method="POST",
            url=panel_url / "panel/api/clients/add",
            json=payload,
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[None].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        message = (
            info.msg.strip()
            .removeprefix("Something went wrong (")
            .removesuffix(")")
            .strip()
        )
        if message == f"email already in use: {email}":
            await Error(ClientAlreadyExists(email))
            return
        await Error(PanelException(info.msg))
        return

    yield info


@effect.async_result[
    Client,
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def extend_subscription(
    client: Client,
    days: int,
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[Client, None]:
    """Extend an existing client; never create one or replace its subscription ID."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    expiry_ms = max(client.expiry_ms, now_ms) + days * 86400 * 1000
    extended = client.model_copy(update={"expiry_ms": expiry_ms, "enable": True})
    payload = extended.model_dump(mode="json", by_alias=True)
    # Unified client records have a numeric id and a separate credential uuid;
    # the update endpoint expects that credential in its legacy id field.
    payload["id"] = str(payload.get("uuid") or client.id)
    maybe_response = await either_from_awaitable(
        session.request(
            method="POST",
            url=panel_url / "panel/api/clients/update" / client.email,
            json=payload,
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[dict[str, Any]].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        await Error(PanelException(info.msg))
        return

    yield extended


@effect.async_result[
    Client,
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException
    | ClientNotFound,
]()
async def get_client(
    email: str,
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[Client, None]:
    maybe_response = await either_from_awaitable(
        session.request(
            method="GET",
            url=panel_url / "panel/api/clients/get" / email,
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[ClientInfo].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        if info.msg.strip() in ("record not found", "(record not found)"):
            await Error(ClientNotFound(email))
            return
        await Error(PanelException(info.msg))
        return

    if info.obj is None:
        await Error(ClientNotFound(email))
        return

    yield info.obj.client


@effect.async_result[
    Client,
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def get_or_create_client(
    email: str,
    days: int,
    tg_id: int,
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[Client, None]:
    match await create_client(
        email=email,
        expiry_days=days,
        tg_id=tg_id,
        session=session,
        panel_url=panel_url,
    ):
        case Result(tag="ok") | Result(tag="error", error=ClientAlreadyExists()):
            maybe_client = await get_client(
                email=email, session=session, panel_url=panel_url
            )
            yield await maybe_client
        case other_error:
            await other_error


@effect.async_result[
    yarl.URL,
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def get_or_create_subscription(
    email: str,
    days: int,
    tg_id: int,
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[yarl.URL, None]:
    match await get_or_create_client(
        email=email,
        days=days,
        tg_id=tg_id,
        session=session,
        panel_url=panel_url,
    ):
        case Result(tag="ok", ok=client):
            maybe_base_url = await get_subscription_base_url(
                session=session, panel_url=panel_url
            )
            base_url = await maybe_base_url
            yield base_url / client.sub_id
        case error:
            await error


@effect.async_result[
    list[Client],
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def get_all_clients(
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[list[Client], None]:
    maybe_response = await either_from_awaitable(
        session.request(
            method="GET",
            url=panel_url / "panel/api/clients/list",
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[list[Client]].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        await Error(PanelException(info.msg))
        return

    if info.obj is None:
        await Error(PanelException("Panel response has no object"))
        return

    yield info.obj


@effect.async_result[
    int,
    aiohttp.ClientError
    | TimeoutError
    | json.JSONDecodeError
    | UnicodeDecodeError
    | ValidationError
    | PanelException,
]()
async def delete_key(
    email: str,
    session: aiohttp.ClientSession,
    *,
    panel_url: yarl.URL,
) -> AsyncGenerator[int, None]:
    maybe_response = await either_from_awaitable(
        session.request(
            method="POST",
            url=panel_url / "panel/api/clients/del" / email,
        ),
        exception_class=(aiohttp.ClientError, TimeoutError),
    )
    response = await maybe_response

    maybe_json = await either_from_awaitable(
        response.json(content_type=None),
        exception_class=(
            aiohttp.ClientError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    )
    data = await maybe_json

    maybe_info = either_from_throwable(
        lambda: PanelResponse[dict[str, Any]].model_validate(data),
        exception_class=ValidationError,
    )
    info = await maybe_info

    if not info.success:
        await Error(PanelException(info.msg))
        return

    yield 1
