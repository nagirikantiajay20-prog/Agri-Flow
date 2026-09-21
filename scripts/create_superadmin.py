"""
Bootstrap the first super_admin account — there's no public endpoint for
this by design (Master Plan Module 1/11: privileged accounts are never
self-registered). Run once against a fresh database.

Usage:
    python scripts/create_superadmin.py --name "Ops Admin" --phone 9999999999 --password "changeme123"
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import session_scope  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.enums import UserRole, UserStatus  # noqa: E402
from app.models.user import StaffProfile, User  # noqa: E402


async def main(name: str, phone: str, password: str) -> None:
    async with session_scope() as db:
        user = User(
            name=name,
            phone=phone,
            password_hash=hash_password(password),
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()
        db.add(StaffProfile(user_id=user.id, department="Operations"))
        print(f"Created super_admin {user.id} ({phone})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.name, args.phone, args.password))
