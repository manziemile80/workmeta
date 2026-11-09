"""Seed the first platform owner.

Usage (from the repository root):

    apps/api/.venv/Scripts/python tools/scripts/seed_owner.py
    apps/api/.venv/Scripts/python tools/scripts/seed_owner.py --force

Reads SEED_OWNER_EMAIL / SEED_OWNER_PASSWORD from the repository `.env`. The
command is idempotent: running it again does not duplicate the owner. ``--force``
resets the owner's password.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.rls import set_rls_context  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.models.enums import PLATFORM_OWNER_ROLE, UserStatus  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import audit  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="Override SEED_OWNER_EMAIL")
    parser.add_argument("--password", help="Override SEED_OWNER_PASSWORD")
    parser.add_argument("--name", default="Platform Owner")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reset the password of an existing platform owner",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    email = (args.email or settings.seed_owner_email).strip().lower()
    password = args.password or settings.seed_owner_password

    if not email:
        print("SEED_OWNER_EMAIL is not set (or pass --email).")
        return 2
    if not password:
        print(
            "SEED_OWNER_PASSWORD is empty. Run tools/scripts/generate_env.py "
            "or pass --password with at least 12 characters."
        )
        return 2
    if len(password) < 12:
        print("The owner password must be at least 12 characters.")
        return 2

    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        created = False

        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(password),
                full_name=args.name,
                is_platform_admin=True,
                status=UserStatus.active.value,
            )
            session.add(user)
            await session.flush()
            created = True
        elif not user.is_platform_admin:
            print(f"{email} already exists but is not a platform admin.")
            return 1
        elif args.force:
            user.password_hash = hash_password(password)
            user.status = UserStatus.active.value
            await session.flush()
        else:
            print(f"Platform owner {email} already exists (use --force to reset).")
            return 0

        await set_rls_context(session, user_id=user.id)
        await audit.record(
            session,
            action="platform.owner_seeded",
            actor_user_id=user.id,
            actor_role=PLATFORM_OWNER_ROLE,
            resource_type="user",
            resource_id=str(user.id),
            metadata={"created": created, "at": datetime.now(UTC).isoformat()},
        )
        await session.commit()

    print(
        f"{'Created' if created else 'Updated'} platform owner {email}. "
        "The password is stored only where you provided it."
    )
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
