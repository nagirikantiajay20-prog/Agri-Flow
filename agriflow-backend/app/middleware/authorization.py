"""
RBAC/permission enforcement lives in app.core.dependencies
(require_permission / require_role / ROLE_PERMISSIONS) so it can be
declared per-route (`Depends(require_permission("crop.create"))`)
alongside the endpoint it protects, rather than inferred from the URL
path in a separate middleware layer. See Master Plan §5 / Module 2.
"""
from app.core.dependencies import (  # noqa: F401
    ROLE_PERMISSIONS,
    require_permission,
    require_role,
    role_has_permission,
)
