"""Stage 8: re-apply the least-privilege grants that a blanket GRANT undid.

Revision ID: 20261111_0008
Revises: 20261112_0007
Create Date: 2026-11-11

Migration ``0001`` revoked ``ALL`` on ``alembic_version`` and ``UPDATE``/
``DELETE`` on ``audit_logs`` for the runtime role. Migration ``0002`` then ran
``GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public``, which silently
re-granted those privileges on every table that existed by then - including
the migration ledger, which carries no row-level security.

This revision re-applies the targeted revokes so they hold regardless of the
order in which blanket grants are executed. It changes privileges only: no
table, column or row is touched, so it is safe to run on a live database.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261111_0008"
down_revision: str | None = "20261112_0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    role = _app_role()
    # The runtime must never rewrite migration state.
    op.execute(f"REVOKE ALL ON TABLE public.alembic_version FROM {role}")
    # The audit trail is append-only for the application.
    op.execute(f"REVOKE UPDATE, DELETE ON TABLE public.audit_logs FROM {role}")
    # No application code hard-deletes; keep that true for every table.
    op.execute(f"REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM {role}")


def downgrade() -> None:
    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role}")
    op.execute(f"GRANT UPDATE ON TABLE public.audit_logs TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.alembic_version TO {role}")
