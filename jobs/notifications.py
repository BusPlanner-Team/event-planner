import logging
from datetime import date

from clients.slack import SlackClient
from database.db import (
    get_config,
    get_connection,
    get_incomplete_tasks,
    log_notification,
    was_notification_sent,
)

logger = logging.getLogger(__name__)


def run_notification_check(db_path):
    """Check all incomplete tasks for upcoming due dates and send Slack reminders.

    Notifications are sent at these intervals before the due date:
    - 7 days (due_7d)
    - 2 days (due_2d)
    - 1 day (due_1d)
    - Day of (due_today)

    Each notification is only sent once (tracked via notification_log).
    Tasks marked as completed are skipped entirely.
    Notifications go to the event's Slack channel if configured, otherwise the global channel.
    """
    conn = get_connection(db_path)

    slack_token = get_config(conn, "slack_bot_token")
    default_channel = get_config(conn, "slack_notification_channel")

    if not slack_token:
        logger.info("Slack not configured — skipping notification check.")
        conn.close()
        return

    slack = SlackClient(slack_token)
    today = date.today()
    tasks = get_incomplete_tasks(conn)
    sent_count = 0

    for task in tasks:
        due_str = task["due_date"][:10]
        try:
            due = date.fromisoformat(due_str)
        except ValueError:
            continue

        days_until = (due - today).days

        notification_type = None
        if days_until == 7:
            notification_type = "due_7d"
        elif days_until == 2:
            notification_type = "due_2d"
        elif days_until == 1:
            notification_type = "due_1d"
        elif days_until == 0:
            notification_type = "due_today"

        if notification_type is None:
            continue

        if was_notification_sent(conn, task["id"], notification_type):
            continue

        # Use event's channel if available, fall back to default
        channel = task["event_slack_channel"] or default_channel
        if not channel:
            continue

        try:
            url = f"http://localhost:5003/event/{task['event_id']}/task/{task['id']}"
            slack.post_task_due_reminder(
                channel,
                task["title"],
                task["event_name"],
                task["assignee_slack_id"],
                task["assignee_name"],
                due_str,
                days_until,
                url,
            )
            log_notification(conn, task["id"], notification_type)
            sent_count += 1
            logger.info("Sent %s notification for task %d (%s)",
                        notification_type, task["id"], task["title"])
        except Exception as e:
            logger.error("Failed to send notification for task %d: %s", task["id"], e)

    conn.close()
    if sent_count:
        logger.info("Notification check complete: %d reminders sent.", sent_count)
