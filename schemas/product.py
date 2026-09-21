import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255, examples=["Produto Exemplo"])
    description: str | None = Field(
        default=None, max_length=1000, examples=["Descrição do produto"]
    )
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, examples=[99.90])
    image: str | None = Field(
        default=None, max_length=500, examples=["https://example.com/image.jpg"]
    )


class ProductUpdate(BaseModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=255, examples=["Produto Atualizado"]
    )
    description: str | None = Field(
        default=None, max_length=1000, examples=["Nova descrição"]
    )
    amount: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2, examples=[149.90]
    )
    image: str | None = Field(
        default=None, max_length=500, examples=["https://example.com/new-image.jpg"]
    )


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    amount: Decimal
    image: str | None
    created_at: datetime
    updated_at: datetime | None
