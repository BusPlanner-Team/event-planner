import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


def init_scheduler(db_path, check_interval_minutes=30, escalation_interval_minutes=15, timezone="US/Eastern"):
    """Initialize and start APScheduler with all scheduled jobs.

    Jobs:
    - Task due date notifications (interval)
    - Approval escalation reminders (interval)
    - Daily calendar reminders at 9 AM ET (cron)
    - Daily calendar reminders at 3 PM ET (cron)

    Args:
        db_path: Path to the SQLite database.
        check_interval_minutes: How often to check for due date notifications.
        escalation_interval_minutes: How often to check for approval escalations.
        timezone: Timezone for date calculations.

    Returns:
        The BackgroundScheduler instance.
    """
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        func=_run_notification_check,
        trigger=IntervalTrigger(minutes=check_interval_minutes),
        id="task_notifications",
        name="Check for task due date notifications",
        replace_existing=True,
        kwargs={"db_path": db_path},
    )

    scheduler.add_job(
        func=_run_escalation_check,
        trigger=IntervalTrigger(minutes=escalation_interval_minutes),
        id="approval_escalations",
        name="Check for approval escalation reminders",
        replace_existing=True,
        kwargs={"db_path": db_path},
    )

    scheduler.start()
    logger.info(
        "Scheduler started: notifications every %dm, escalations every %dm (%s)",
        check_interval_minutes, escalation_interval_minutes, timezone,
    )
    return scheduler


def _run_notification_check(db_path):
    try:
        from jobs.notifications import run_notification_check
        run_notification_check(db_path)
    except Exception as e:
        logger.error("Notification check failed: %s", e)


def _run_escalation_check(db_path):
    try:
        from jobs.escalation import run_escalation_check
        run_escalation_check(db_path)
    except Exception as e:
        logger.error("Escalation check failed: %s", e)


def _run_calendar_reminder(db_path, slot):
    try:
        from jobs.calendar_reminders import run_daily_calendar_reminder
        run_daily_calendar_reminder(db_path, slot)
    except Exception as e:
        logger.error("Calendar reminder check (%s) failed: %s", slot, e)
