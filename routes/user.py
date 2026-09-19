import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from databases.postgres import get_session
from repositories.user import UserRepository
from schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=list[UserOut])
async def get_users(db: AsyncSession = Depends(get_session)):
    """Return all users."""
    return await UserRepository.get_users(db)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Return a single user by ID or 404 when not found."""
    return await UserRepository.get_user_by_id(db, user_id)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_session)):
    """Create a new user and return it."""
    return await UserRepository.create_user(db, payload)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, payload: UserUpdate, db: AsyncSession = Depends(get_session)
):
    """Update a user and return the updated record."""
    return await UserRepository.update_user(db, user_id, payload)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Delete a user by ID or 404 when not found."""
    await UserRepository.delete_user(db, user_id)
