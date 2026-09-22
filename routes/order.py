import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from databases.postgres import get_session
from enums.order import OrderStatus
from repositories.order import OrderRepository
from schemas.order import OrderCreate, OrderOut, OrderPage
from utils.logger import get_logger

logger = get_logger("ORDER")

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("", response_model=OrderPage)
async def get_orders(
    status: list[OrderStatus] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    """Return a page of orders, newest first.

    `status` may be repeated (?status=RECEIVED&status=PROCESSING) to match any.
    """
    items, total = await OrderRepository.get_orders(db, status, limit, offset)
    return OrderPage(
        items=[OrderOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


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
        await publish_or_keep_received(order)
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK

    return order


@router.post(
    "/{order_id}/reprocess",
    response_model=OrderOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_order(order_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Requeue a FAILED (or stuck RECEIVED) order for processing; 409 otherwise."""
    order = await OrderRepository.reset_for_reprocessing(db, order_id)
    await OrderRepository._publish_order(order)
    return order


async def publish_or_keep_received(order) -> None:
    """Publish the order; if the broker is down keep it RECEIVED for reprocessing."""
    try:
        await OrderRepository._publish_order(order)
    except Exception:
        logger.exception(
            "Publish failed, order kept as RECEIVED for reprocessing",
            extra={"order_id": str(order.id), "external_id": order.external_id},
        )
