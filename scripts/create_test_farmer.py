import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import session_scope
from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.models.user import FarmerProfile, User


async def main(name: str, phone: str, password: str) -> None:
    async with session_scope() as db:
        user = User(
            name=name,
            phone=phone,
            password_hash=hash_password(password),
            role=UserRole.FARMER,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()
        db.add(FarmerProfile(user_id=user.id, acres_of_land=Decimal("5.0"), primary_crop="Cotton"))
        print(f"Created active farmer {user.id} ({phone})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Ramesh Kumar")
    parser.add_argument("--phone", default="9876543210")
    parser.add_argument("--password", default="FarmerTestPass123")
    args = parser.parse_args()
    asyncio.run(main(args.name, args.phone, args.password))
