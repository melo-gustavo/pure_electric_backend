import uuid

from sqlalchemy.orm import Mapped, mapped_column

from .base_model import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid] = mapped_column(primary_key=True, default=uuid.uuid7)
