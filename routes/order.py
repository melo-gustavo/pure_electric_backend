import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from databases.postgres import get_session
from repositories.order import OrderRepository
from schemas.order import OrderCreate, OrderOut

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("", response_model=list[OrderOut])
async def get_orders(db: AsyncSession = Depends(get_session)):
    """Return every stored order."""
    return await OrderRepository.get_orders(db)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Return a single order by ID or 404 when not found."""
    return await OrderRepository.get_order_by_id(db, order_id)


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def receive_order(
    payload: OrderCreate,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Receive an order idempotently, queue processing, and return the order."""
    return await OrderRepository.create_and_publish(db, payload)
