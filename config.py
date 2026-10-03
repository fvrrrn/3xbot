from typing import Annotated

from pydantic import AfterValidator, HttpUrl
from pydantic_settings import BaseSettings


class NonEmptyUrl(HttpUrl):
    @property
    def path(self) -> str:
        assert (path := super().path) is not None
        return path

    @property
    def port(self) -> int:
        assert (port := super().port) is not None
        return port


def _with_path(url: NonEmptyUrl) -> NonEmptyUrl:
    if url.path == "/":
        raise ValueError("must carry a path, e.g. https://host/telegram-webhook")
    return url


def _with_telegram_port(url: NonEmptyUrl) -> NonEmptyUrl:
    if url.port not in (443, 80, 88, 8443):
        raise ValueError("Telegram delivers webhooks only to port 443, 80, 88 or 8443")
    return url


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_admin_ids: list[int]
    support_group_id: int
    payment_details: str
    xui_url: HttpUrl
    xui_token: str
    trial_days: int
    plan_months: int
    plan_price_rub: float
    db_path: str = "db.sqlite3"
    expiry_notify_h: int = 24
    scheduler_time_h: int = 24
    telegram_webhook_url: Annotated[
        NonEmptyUrl, AfterValidator(_with_path), AfterValidator(_with_telegram_port)
    ]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
