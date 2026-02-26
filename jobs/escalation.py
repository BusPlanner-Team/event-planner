import logging
from database.db import (
    get_config,
    get_connection,
    get_active_approvals_needing_escalation,
    log_escalation,
)
from clients.slack import SlackClient

logger = logging.getLogger(__name__)


def run_escalation_check(db_path):
    """Check for active approval steps waiting on leads/directors for > 2 hours.

    If Vanessa (lead) or Mahbod (director) haven't acted on their approval step
    within 2 hours, send an escalation message tagging them in the event's Slack channel.
    Each approval step is only escalated once (tracked via escalation_log).
    """
    conn = get_connection(db_path)

    slack_token = get_config(conn, "slack_bot_token")
    default_channel = get_config(conn, "slack_notification_channel")

    if not slack_token:
        logger.info("Slack not configured — skipping escalation check.")
        conn.close()
        return

    slack = SlackClient(slack_token)
    approvals = get_active_approvals_needing_escalation(conn)
    sent_count = 0

    for approval in approvals:
        # Use event's channel if available, fall back to default
        channel = approval["event_slack_channel"] or default_channel
        if not channel:
            continue

        try:
            slack.post_escalation_reminder(
                channel,
                approval["task_title"],
                approval["event_name"],
                approval["step_label"],
                approval["approver_slack_id"],
                approval["approver_name"],
            )
            log_escalation(conn, approval["id"], approval["approver_id"], channel)
            sent_count += 1
            logger.info(
                "Sent escalation for approval %d (task: %s, approver: %s)",
                approval["id"], approval["task_title"], approval["approver_name"],
            )
        except Exception as e:
            logger.error("Failed to send escalation for approval %d: %s", approval["id"], e)

    conn.close()
    if sent_count:
        logger.info("Escalation check complete: %d reminders sent.", sent_count)
