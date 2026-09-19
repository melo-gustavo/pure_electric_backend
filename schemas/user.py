import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    phone: str = Field(examples=["+5511999999999"])
    email: EmailStr | None = Field(default=None, examples=["user@example.com"])
    password: str | None = Field(default=None, min_length=6, max_length=72)
    full_name: str = Field(min_length=2, examples=["Maria da Silva"])
    cpf: str | None = Field(
        default=None, examples=["12345678900"], min_length=11, max_length=11
    )
    cnpj: str | None = Field(
        default=None, examples=["12345678000199"], min_length=14, max_length=14
    )


class UserUpdate(BaseModel):
    phone: str | None = Field(default=None, examples=["+5511999999999"])
    email: EmailStr | None = Field(default=None, examples=["user@example.com"])
    password: str | None = Field(default=None, min_length=6, max_length=72)
    full_name: str | None = Field(default=None, min_length=2)
    cpf: str | None = Field(
        default=None, examples=["12345678900"], min_length=11, max_length=11
    )
    cnpj: str | None = Field(
        default=None, examples=["12345678000199"], min_length=14, max_length=14
    )


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str
    email: EmailStr | None
    full_name: str
    cpf: str | None
    cnpj: str | None
