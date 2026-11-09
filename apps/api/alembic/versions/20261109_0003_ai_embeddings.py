"""Stage 3: record which embedding model produced each document's vectors.

Revision ID: 20261109_0003
Revises: 20261109_0002
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.config import get_settings

revision: str = "20261109_0003"
down_revision: str | None = "20261109_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_FAKE_EMBEDDING_MODEL = "fake-workmeta-embedding-1"


def _backfill_model() -> str:
    settings = get_settings()
    if settings.ai_provider == "fake" or not settings.ai_api_key:
        return _FAKE_EMBEDDING_MODEL
    return settings.ai_embedding_model


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_documents_embedding_pair_valid",
        "documents",
        "(embedding_model IS NULL) = (embedding_dimensions IS NULL)",
    )

    # Ready documents already have vectors. Attribute them to the active
    # embedding model so retrieval does not mix incompatible vector spaces.
    settings = get_settings()
    op.execute(
        sa.text(
            "UPDATE documents "
            "SET embedding_model = :model, embedding_dimensions = :dimensions "
            "WHERE status = 'ready'"
        ).bindparams(
            model=_backfill_model(),
            dimensions=settings.embedding_dimensions,
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_documents_embedding_pair_valid", "documents", type_="check"
    )
    op.drop_column("documents", "embedding_dimensions")
    op.drop_column("documents", "embedding_model")
