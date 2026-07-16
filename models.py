from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class YooMoneyRequest(BaseModel):
    notification_type: Literal["p2p-incoming", "card-incoming"]
    label: str
    amount: float
    withdraw_amount: float | None = None
    sign: str = ""
    operation_id: str = ""
    operation_label: str = ""
    bill_id: str = ""
    currency: str = ""
    sender: str = ""
    codepro: str = ""
    test_notification: str = ""
    unaccepted: str = ""
    payment_datetime: str = Field(alias="datetime", default="")
    firstname: str = ""
    lastname: str = ""
    fathersname: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    street: str = ""
    building: str = ""
    suite: str = ""
    flat: str = ""
    zip: str = ""

    @field_validator("label")
    @classmethod
    def label_must_be_int(cls, v: str) -> str:
        int(v)
        return v

    @property
    def tg_id(self) -> int:
        return int(self.label)

    @property
    def paid_amount(self) -> float:
        return self.withdraw_amount or self.amount


class Host(BaseModel):
    host_name: str
    host_url: str
    api_token: str
    inbound_id: int
    public_hostname: str | None = None
    public_url: str | None = None
    additional_inbound_ids: list[int] = []


class Plan(BaseModel):
    id: int
    host_name: str
    plan_name: str
    months: int
    price: float


class ProvisionResult(BaseModel):
    email: str
    expiry_ms: int
    connection_string: str
    sub_id: str


class Transaction(BaseModel):
    id: str
    tg_id: int
    plan_id: int
    amount: float
    status: str
    created_at: datetime


class Client(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    email: str = ""
    tg_id: int = Field(alias="tgId", default=0)
    expiry_ms: int = Field(alias="expiryTime", default=0)
    sub_id: str = Field(alias="subId", default="")
    enable: bool = False
