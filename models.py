from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProvisionResult(BaseModel):
    email: str
    expiry_ms: int
    connection_string: str
    sub_id: str


class Transaction(BaseModel):
    id: str
    tg_id: int
    months: int
    amount: float
    message_id: int | None = None
    status: str
    created_at: datetime


class Client(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    email: str = ""
    tg_id: int = Field(alias="tgId", default=0)
    expiry_ms: int = Field(alias="expiryTime", default=0)
    sub_id: str = Field(alias="subId", default="")
    enable: bool = False


class PanelResponse[T](BaseModel):
    success: bool
    msg: str = ""
    obj: T | None = None


class PanelSettings(BaseModel):
    """obj of POST /panel/api/setting/all - the sub server's configuration."""

    model_config = ConfigDict(populate_by_name=True)
    sub_enable: bool = Field(alias="subEnable", default=False)
    sub_uri: str = Field(alias="subURI", default="")
    sub_domain: str = Field(alias="subDomain", default="")
    sub_port: int = Field(alias="subPort", default=0)
    sub_path: str = Field(alias="subPath", default="/")
    sub_cert_file: str = Field(alias="subCertFile", default="")


class InboundOption(BaseModel):
    """An entry of GET /panel/api/inbounds/options."""

    model_config = ConfigDict(populate_by_name=True)
    id: int
    remark: str = ""
    protocol: str = ""
    port: int = 0
    # Only a VLESS inbound over a flow-capable transport accepts xtls-rprx-vision.
    tls_flow_capable: bool = Field(alias="tlsFlowCapable", default=False)


class ClientInfo(BaseModel):
    """obj of GET /panel/api/clients/get/{email}."""

    model_config = ConfigDict(populate_by_name=True)
    client: Client
    inbound_ids: list[int] = Field(alias="inboundIds", default_factory=list)
    used_traffic: int = Field(alias="usedTraffic", default=0)
