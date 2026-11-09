"""Platform product catalog (reference data for assistants)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.db.session import get_session
from app.models.product import Product
from app.schemas.assistant import ProductRead

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductRead], summary="List available products")
async def list_products(
    _: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Product]:
    rows = await session.scalars(
        select(Product).where(Product.is_active.is_(True)).order_by(Product.name.asc())
    )
    return list(rows)
