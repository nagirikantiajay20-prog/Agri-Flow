import bcrypt
import pytest

from app.core.security import hash_password, is_bcrypt_hash, needs_rehash, verify_password
from app.services import auth_service


def test_argon2_hash_round_trips():
    h = hash_password("mypassword123")
    assert not is_bcrypt_hash(h)
    assert verify_password("mypassword123", h)
    assert not verify_password("wrongpassword", h)
    assert not needs_rehash(h)


def test_bcrypt_hash_verifies_correctly():
    bcrypt_hash = bcrypt.hashpw(b"legacypassword123", bcrypt.gensalt()).decode()
    assert is_bcrypt_hash(bcrypt_hash)
    assert verify_password("legacypassword123", bcrypt_hash)
    assert not verify_password("wrongpassword", bcrypt_hash)
    assert needs_rehash(bcrypt_hash)


@pytest.mark.asyncio
async def test_login_with_migrated_bcrypt_hash_lazily_rehashes(db_session):
    from app.models.enums import UserRole, UserStatus
    from app.models.user import User

    bcrypt_hash = bcrypt.hashpw(b"legacypassword123", bcrypt.gensalt()).decode()
    user = User(
        name="Migrated Farmer", phone="9888877766", password_hash=bcrypt_hash,
        role=UserRole.FARMER, status=UserStatus.ACTIVE, legacy_app_user_id=4242,
    )
    db_session.add(user)
    await db_session.commit()

    assert is_bcrypt_hash(user.password_hash)

    authenticated = await auth_service.authenticate(db_session, phone="9888877766", password="legacypassword123")
    await db_session.commit()

    assert authenticated.id == user.id
    assert not is_bcrypt_hash(authenticated.password_hash), "Must be rehashed to Argon2id after successful login"

    # Second login still works, now against the Argon2 hash.
    again = await auth_service.authenticate(db_session, phone="9888877766", password="legacypassword123")
    assert again.id == user.id
