import logging
import random
import string
from datetime import datetime, timedelta

import aiohttp

from models import Client, Host, ProvisionResult

logger = logging.getLogger(__name__)


def _gen_sub_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def _headers(host: Host) -> dict:
    return {"Authorization": f"Bearer {host.api_token}"}


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))


async def provision_key(
    host: Host, email: str, days: int, tg_id: int
) -> ProvisionResult:
    async with _session() as session:
        async with session.get(
            f"{host.host_url}/panel/api/clients/get/{email}",
            headers=_headers(host),
        ) as resp:
            data = await resp.json()

        existing = data.get("obj") if data.get("success") else None
        now_ms = int(datetime.now().timestamp() * 1000)
        expiry_ms = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)

        if existing:
            current = existing.get("expiryTime") or 0
            if current > now_ms:
                expiry_ms = int(
                    (
                        datetime.fromtimestamp(current / 1000) + timedelta(days=days)
                    ).timestamp()
                    * 1000
                )
            sub_id = existing.get("subId") or _gen_sub_id()
            existing["expiryTime"] = expiry_ms
            existing["enable"] = True
            existing["subId"] = sub_id
            async with session.post(
                f"{host.host_url}/panel/api/clients/update/{email}",
                headers=_headers(host),
                json=existing,
            ) as resp:
                result = await resp.json()
            if not result.get("success"):
                raise RuntimeError(
                    f"Failed to update client {email!r}: {result.get('msg')}"
                )
        else:
            sub_id = _gen_sub_id()
            payload = {
                "client": {
                    "email": email,
                    "tgId": tg_id,
                    "subId": sub_id,
                    "expiryTime": expiry_ms,
                    "enable": True,
                    "totalGB": 0,
                    "limitIp": 0,
                },
                "inboundIds": [host.inbound_id] + host.additional_inbound_ids,
            }
            async with session.post(
                f"{host.host_url}/panel/api/clients/add",
                headers=_headers(host),
                json=payload,
            ) as resp:
                result = await resp.json()
            if not result.get("success"):
                raise RuntimeError(
                    f"Failed to create client {email!r}: {result.get('msg')}"
                )

        if host.public_url:
            connection_string = f"{host.public_url.rstrip('/')}/{sub_id}"
        else:
            async with session.get(
                f"{host.host_url}/panel/api/clients/links/{email}",
                headers=_headers(host),
            ) as resp:
                links_data = await resp.json()
            links = links_data.get("obj") or []
            if not links:
                raise ValueError(f"No connection links for {email!r}")
            connection_string = links[0]

        return ProvisionResult(
            email=email,
            expiry_ms=expiry_ms,
            connection_string=connection_string,
            sub_id=sub_id,
        )


async def get_client(host: Host, email: str) -> Client:
    async with _session() as session:
        async with session.get(
            f"{host.host_url}/panel/api/clients/get/{email}",
            headers=_headers(host),
        ) as resp:
            data = await resp.json()
    obj = data.get("obj") if data.get("success") else None
    if not obj:
        raise ValueError(f"Client {email!r} not found on {host.host_name!r}")
    return Client.model_validate(obj)


async def get_all_clients(host: Host) -> list[Client]:
    async with _session() as session:
        async with session.get(
            f"{host.host_url}/panel/api/clients/list",
            headers=_headers(host),
        ) as resp:
            data = await resp.json()
    clients = data.get("obj") if data.get("success") else None
    if not clients:
        raise ValueError(f"No clients on {host.host_name!r}")
    return [Client.model_validate(c) for c in clients]


async def delete_key(host: Host, email: str) -> int:
    async with _session() as session:
        async with session.post(
            f"{host.host_url}/panel/api/clients/del/{email}",
            headers=_headers(host),
        ) as resp:
            data = await resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Failed to delete {email!r}: {data.get('msg')}")
    return 1
