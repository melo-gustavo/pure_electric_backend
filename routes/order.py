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


@router.post("", response_model=OrderOut)
async def receive_order(
    payload: OrderCreate,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Receive an order idempotently, queue processing, and return the order.
    Returns 201 if newly created, 200 if order already existed.
    """
    order, created = await OrderRepository.create_idempotent(db, payload)

    if created:
        await OrderRepository._publish_order(order)
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK

    return order
