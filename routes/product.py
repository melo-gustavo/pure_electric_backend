import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from databases.postgres import get_session
from repositories.product import ProductRepository
from schemas.product import ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("", response_model=list[ProductOut])
async def get_products(db: AsyncSession = Depends(get_session)):
    """Return every stored product."""
    return await ProductRepository.get_products(db)


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Return a single product by ID or 404 when not found."""
    return await ProductRepository.get_product_by_id(db, product_id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Create a new product."""
    return await ProductRepository.create(db, payload)


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_session),
):
    """Update a product."""
    return await ProductRepository.update(db, product_id, payload)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
):
    """Delete a product."""
    await ProductRepository.delete(db, product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
