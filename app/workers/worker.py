"""
Background jobs (Master Plan Module 13). Net-new capability — nothing in
the legacy system does this asynchronously today (Edge Functions are
synchronous, request-scoped). Can be deferred behind Modules 1-12 if
timeline is tight; included here as a minimal working scaffold.
"""
from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.database import session_scope
from app.core.logging import get_logger

logger = get_logger(__name__)


async def send_visit_reminders(ctx) -> None:
    """Notify farmers of farm visits scheduled for tomorrow. Runs daily
    (see WorkerSettings.cron_jobs below)."""
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.models.crop import FarmVisit
    from app.models.enums import VisitStatus
    from app.services import notification_service

    tomorrow = date.today() + timedelta(days=1)
    async with session_scope() as db:
        result = await db.execute(
            select(FarmVisit).where(FarmVisit.scheduled_date == tomorrow, FarmVisit.status == VisitStatus.SCHEDULED)
        )
        visits = result.scalars().all()
        for visit in visits:
            await notification_service.notify_user(
                db,
                user_id=visit.farmer_id,
                title="Farm visit tomorrow",
                message="A farm visit is scheduled for tomorrow.",
                reference_type="farm_visit",
                reference_id=visit.id,
            )
        logger.info("visit_reminders_sent", count=len(visits))


async def generate_monthly_report_job(ctx, month: str) -> None:
    """Pre-generate a monthly report so admins don't wait on the
    aggregation query synchronously (Master Plan Module 13)."""
    from app.services import admin_service

    async with session_scope() as db:
        report = await admin_service.get_monthly_report(db, month=month)
        logger.info("monthly_report_generated", month=month, report=report)
        # Persisting the generated report to a cache/table for instant
        # retrieval is a natural next step here, left as a TODO — the
        # admin.get_monthly_report endpoint currently always computes
        # live, which is fine at current data volume.


class WorkerSettings:
    functions = [send_visit_reminders, generate_monthly_report_job]
    cron_jobs = [
        cron(send_visit_reminders, hour=6, minute=0),  # daily at 06:00
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
