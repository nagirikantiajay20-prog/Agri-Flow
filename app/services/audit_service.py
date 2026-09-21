"""
Single insertion point for audit_logs (Master Plan §11 / §31 / Module 11).

Every mutating service call below imports and calls `record` instead of
inserting into `audit_logs` directly — this is deliberate, replacing the
scattered `supabase.from('audit_logs').insert(...)` call sites found
across the legacy frontend/Edge Functions.

Because every write in the system already funnels through here, this is
also the one place that invalidates the dashboard cache and announces a
change to connected dashboards, so neither concern has to be remembered
at ~40 individual call sites.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.models.ledger import AuditLog
from app.services import event_service

PUBLIC_STAT_ACTIONS = (
    "farmer.",
    "user.register",
    "crop.register",
    "seed.",
    "warehouse.create",
    "market_rate.",
)


async def record(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    details: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
    )
    db.add(entry)
    await db.flush()

    await cache.invalidate(cache.DASHBOARD_NAMESPACE)
    if action.startswith(PUBLIC_STAT_ACTIONS):
        await cache.invalidate(cache.PUBLIC_NAMESPACE)
    await event_service.publish_dashboard_changed(action)

    return entry
