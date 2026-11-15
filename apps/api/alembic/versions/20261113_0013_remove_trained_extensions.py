"""Stage 13: remove the trained extensions.

Revision ID: 20261113_0013
Revises: 20261112_0012
Create Date: 2026-11-13

The trained extensions were replaced by **WorkMeta AI Submodels**. A submodel
is trained from the same recordings, but it carries its own workflows, so it
can be exported, installed on another computer and used there without
training again. The extension tables are no longer written to or read from,
so they go.

Five tables are dropped and the plan loses its extension limit:

* ``extensions``
* ``training_materials``
* ``extension_procedures``
* ``extension_tests``
* ``extension_action_plans``

The DDL is not repeated here: it is taken from the migration that created
the tables, so the two can never drift apart. That migration stays in the
history — it has already run on live databases — and this one calls it
directly.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "20261113_0013"
down_revision: str | None = "20261112_0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _legacy_migration():
    """The migration that created the extension tables.

    It is loaded from its file rather than imported, because a revision id
    is not a valid Python module name.
    """
    path = Path(__file__).with_name("20261114_0010_trained_extensions.py")
    spec = importlib.util.spec_from_file_location("wm_legacy_extensions", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load the extension migration at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    """Carry the limit over, then drop the tables."""
    # A client that had room for several extensions now has the same room for
    # submodels. Plans that never had a limit keep having none.
    op.execute(
        "UPDATE public.subscription_plans SET max_submodels = max_extensions "
        "WHERE max_submodels IS NULL AND max_extensions IS NOT NULL"
    )
    _legacy_migration().downgrade()


def downgrade() -> None:
    """Put the extension tables back.

    The rows are gone — the migration cannot invent them — but the schema is
    restored, so the platform can still be rolled back to the previous
    release.
    """
    _legacy_migration().upgrade()
