import uuid

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from enums.user import DocumentType

from .base_model import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    phone: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    password: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    cpf: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True, index=True
    )
    cnpj: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True, index=True
    )
    document_type: Mapped[DocumentType | None] = mapped_column(
        Enum(DocumentType, name="document_type"), nullable=True
    )
