"""Stage 14: remove the submodel platform.

Revision ID: 20261115_0014
Revises: 20261113_0013
Create Date: 2026-11-15

The submodel platform (Stage 12) is removed from the codebase: the router,
models, schemas, services, console tab and the agent's submodel calls are
all gone. Nothing reads or writes these tables any more, so they go.

Five tables are dropped and the plan loses its submodel limit:

* ``submodels``
* ``submodel_materials``
* ``submodel_procedures``
* ``submodel_tests``
* ``submodel_action_plans``

The DDL is not repeated here: it is taken from the migration that created
the tables, so the two can never drift apart. That migration stays in the
history — it has already run on live databases — and this one calls it
directly.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path

revision: str = "20261115_0014"
down_revision: str | None = "20261113_0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _legacy_migration():
    """The migration that created the submodel tables.

    It is loaded from its file rather than imported, because a revision id
    is not a valid Python module name.
    """
    path = Path(__file__).with_name("20261112_0012_submodels.py")
    spec = importlib.util.spec_from_file_location("wm_legacy_submodels", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load the submodel migration at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    """Drop the tables and the plan's submodel limit."""
    _legacy_migration().downgrade()


def downgrade() -> None:
    """Put the submodel tables back.

    The rows are gone — the migration cannot invent them — but the schema is
    restored, so the platform can still be rolled back to the previous
    release.
    """
    _legacy_migration().upgrade()
