from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_admin_ids: list[int]
    support_group_id: int
    yoomoney_secret: str
    yoomoney_receiver: str
    db_path: str = "db.sqlite3"
    expiry_notify_h: int = 24
    scheduler_time_h: int = 24
    webhook_url: str
    webhook_port: int
    telegram_webhook_path: str
    yoomoney_webhook_path: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
