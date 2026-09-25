"""
Authentication is enforced via FastAPI dependencies
(app.core.dependencies.get_current_user / ActiveUser), applied per-route,
rather than as blanket ASGI middleware — this lets public endpoints
(app/api/v1/public.py) skip auth entirely while every other router
requires it, without a middleware allow-list to maintain and keep in
sync.

This module exists to satisfy the project layout in the Master Plan
(§3) and to be the obvious place a future cross-cutting auth concern
(e.g. session-fixation checks, device binding) would go.
"""
from app.core.dependencies import ActiveUser, CurrentUser, get_current_user, require_active_user  # noqa: F401
