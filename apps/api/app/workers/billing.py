"""Billing maintenance worker.

Run as a separate process in production::

    python -m app.workers.billing            # poll for due subscriptions
    python -m app.workers.billing --once     # one pass and exit
    python -m app.workers.billing --drain    # one pass and exit (same as --once)

Each pass:
  1. Discovers billing subjects via ``app_billing_orgs()`` (org ids + status
     only — never document or conversation content).
  2. Advances each subscription through ``ensure_subscription`` inside that
     org's RLS context, so renewal/past-due/expired transitions and the
     resulting audit rows are per-tenant and idempotent (re-running a pass
     changes nothing when nothing is due).
  3. Prunes usage counters older than the retention window through the
     narrow ``app_prune_usage_counters`` function.

Retries: a per-org failure is logged and does not abort the pass; the next
pass retries. Timeouts: each org is processed in its own transaction. The
worker never touches document bytes, chunks, messages or provider keys.
Failed payments are handled by webhook/verification flows; this worker only
advances subscription lifecycle state.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.rls import set_rls_context
from app.db.session import SessionFactory, dispose_engine
from app.services import billing

logger = structlog.get_logger(__name__)

_RETAIN_MONTHS = 13


async def _discover(session: AsyncSession) -> list[uuid.UUID]:
    rows = (
        await session.execute(text("SELECT org_id FROM app_billing_orgs()"))
    ).all()
    return [row[0] for row in rows]


async def advance_one(
    org_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> bool:
    async with session_factory() as session:
        await set_rls_context(session, org_id=org_id)
        try:
            context = await billing.ensure_subscription(session, org_id)
            await session.commit()
            changed = context.changed
        except Exception:
            await session.rollback()
            raise
    if changed:
        logger.info("billing_subscription_advanced", org_id=str(org_id))
    return changed


async def prune_counters(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> int:
    async with session_factory() as session:
        # No tenant context needed: the function is tenant-agnostic retention.
        result = await session.execute(
            text("SELECT app_prune_usage_counters(:months)"),
            {"months": _RETAIN_MONTHS},
        )
        pruned = int(result.scalar_one() or 0)
        await session.commit()
    return pruned


async def sweep(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> dict[str, int]:
    checked = 0
    advanced = 0
    failures = 0
    async with session_factory() as session:
        org_ids = await _discover(session)
        await session.commit()
    for org_id in org_ids:
        checked += 1
        try:
            if await advance_one(org_id, session_factory):
                advanced += 1
        except Exception:  # noqa: BLE001 - one bad org must not kill the pass
            failures += 1
            logger.exception("billing_sweep_org_failed", org_id=str(org_id))
    pruned = await prune_counters(session_factory)
    logger.info(
        "billing_sweep_completed",
        checked=checked,
        advanced=advanced,
        failures=failures,
        pruned_counters=pruned,
    )
    return {"checked": checked, "advanced": advanced, "failures": failures, "pruned": pruned}


async def run(*, once: bool = False, drain: bool = False) -> None:
    settings = get_settings()
    try:
        if once or drain:
            await sweep()
            return
        while True:
            await sweep()
            await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkMeta AI billing worker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="one sweep and exit")
    group.add_argument(
        "--drain", action="store_true", help="one sweep and exit (alias)"
    )
    args = parser.parse_args()
    asyncio.run(run(once=args.once, drain=args.drain))


if __name__ == "__main__":
    main()
