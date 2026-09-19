import uuid

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from schemas.user import UserCreate, UserUpdate
from utils.security import SecurityUtils


class UserRepository:
    @staticmethod
    async def get_users(db: AsyncSession) -> list[User]:
        """Return all users."""
        result = await db.execute(select(User))
        return list(result.scalars().all())

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User:
        """Return a user by ID or raise 404 when not found."""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        return user

    @staticmethod
    async def _check_unique_conflicts(
        db: AsyncSession,
        data: UserCreate | UserUpdate,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        """Raise 409 when any provided unique field already belongs to another user."""
        filters: list[ColumnElement[bool]] = []

        if data.phone:
            filters.append(User.phone == data.phone)
        if data.email:
            filters.append(User.email == data.email)
        if data.cpf:
            filters.append(User.cpf == data.cpf)
        if data.cnpj:
            filters.append(User.cnpj == data.cnpj)

        if not filters:
            return

        query = select(User.phone, User.email, User.cpf, User.cnpj).where(or_(*filters))

        if exclude_id:
            query = query.where(User.id != exclude_id)

        result = await db.execute(query)
        existing = result.first()

        if not existing:
            return

        field_map = {
            "phone": ("phone", "phone"),
            "email": ("email", "email"),
            "cpf": ("cpf", "cpf"),
            "cnpj": ("cnpj", "cnpj"),
        }

        for field, (col, label) in field_map.items():
            if getattr(data, field) and getattr(existing, col) == getattr(data, field):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A user with this {label} already exists.",
                )

    @staticmethod
    async def create_user(db: AsyncSession, data: UserCreate) -> User:
        """Create a new user with a hashed password and return it."""
        await UserRepository._check_unique_conflicts(db, data)

        user_data = data.model_dump(exclude_unset=True)
        if user_data.get("password"):
            user_data["password"] = SecurityUtils.hash_password(user_data["password"])
        else:
            user_data.pop("password", None)

        user = User(**user_data)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def update_user(
        db: AsyncSession, user_id: uuid.UUID, data: UserUpdate
    ) -> User:
        """Update provided fields, hashing the password when set, and return it."""
        user = await UserRepository.get_user_by_id(db, user_id)

        update_data = data.model_dump(exclude_unset=True)

        await UserRepository._check_unique_conflicts(db, data, exclude_id=user_id)

        if update_data.get("password"):
            update_data["password"] = SecurityUtils.hash_password(
                update_data["password"]
            )

        for field, value in update_data.items():
            setattr(user, field, value)

        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def delete_user(db: AsyncSession, user_id: uuid.UUID) -> None:
        """Delete a user by ID or raise 404 when not found."""
        user = await UserRepository.get_user_by_id(db, user_id)

        await db.delete(user)
        await db.commit()
