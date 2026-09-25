from app.core.security import create_access_token
from app.models.user import User


def auth_headers(user: User) -> dict:
    token = create_access_token(user_id=user.id, role=user.role.value, status=user.status.value)
    return {"Authorization": f"Bearer {token}"}
