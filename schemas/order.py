import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from enums.order import OrderStatus


class OrderCreate(BaseModel):
    model_config = ConfigDict(validate_by_name=True)

    external_id: str = Field(
        alias="externalId", min_length=1, max_length=100, examples=["ORDER-123"]
    )
    customer: str = Field(min_length=2, max_length=255, examples=["Cliente Exemplo"])
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, examples=[150.00])


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    customer: str
    amount: Decimal
    status: OrderStatus
    failure_reason: str | None
    created_at: datetime
    processed_at: datetime | None


class OrderPage(BaseModel):
    items: list[OrderOut]
    total: int
    limit: int
    offset: int
