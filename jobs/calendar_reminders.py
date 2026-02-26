import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from clients.slack import SlackClient
from database.db import (
    get_config,
    get_connection,
    get_events_with_calendar_invite,
    log_calendar_reminder,
    was_calendar_reminder_sent,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def run_daily_calendar_reminder(db_path, slot):
    """Send daily Slack reminders for upcoming events that have a calendar invite.

    Args:
        db_path: Path to the SQLite database.
        slot: 'morning' (9 AM) or 'afternoon' (3 PM).

    Logic:
    - Finds all non-completed/cancelled events with a calendar_entry deliverable
      whose event_date is today or in the future.
    - Skips events that already had a reminder sent for this date/slot.
    - Checks that system time is in Eastern Time alignment — if the system clock
      appears to be off by more than 2 hours from expected ET, skips to avoid
      sending at wrong times.
    - Sends the reminder to the event's Slack channel (or global channel as fallback).
    """
    # Sanity check: ensure system time is reasonable for ET
    now_et = datetime.now(ET)
    expected_hour = 9 if slot == "morning" else 15
    if abs(now_et.hour - expected_hour) > 2:
        logger.warning(
            "Calendar reminder (%s) fired at %s ET (expected ~%d:00). "
            "System time may be off — skipping this run.",
            slot, now_et.strftime("%H:%M"), expected_hour,
        )
        return

    conn = get_connection(db_path)

    slack_token = get_config(conn, "slack_bot_token")
    default_channel = get_config(conn, "slack_notification_channel")

    if not slack_token:
        logger.info("Slack not configured — skipping calendar reminders.")
        conn.close()
        return

    slack = SlackClient(slack_token)
    today_str = now_et.strftime("%Y-%m-%d")
    today_date = now_et.date()
    events = get_events_with_calendar_invite(conn)
    sent_count = 0

    for event in events:
        event_id = event["id"]

        # Skip if already sent for this date/slot
        if was_calendar_reminder_sent(conn, event_id, today_str, slot):
            continue

        # Determine the channel
        channel = event["slack_channel_id"] or default_channel
        if not channel:
            continue

        # Calculate days until event
        try:
            event_date = date.fromisoformat(str(event["event_date"])[:10])
        except ValueError:
            continue

        days_until = (event_date - today_date).days
        if days_until < 0:
            continue  # Event already passed

        # Build event URL and type label
        event_url = f"http://localhost:5003/event/{event_id}"
        event_type = _event_type_label(event["event_type"])
        event_date_str = event_date.strftime("%b %d, %Y")

        try:
            slack.post_calendar_reminder(
                channel,
                event["name"],
                event_date_str,
                event_type,
                event["location"],
                days_until,
                event_url,
            )
            log_calendar_reminder(conn, event_id, today_str, slot)
            sent_count += 1
            logger.info(
                "Sent %s calendar reminder for event %d (%s) — %d days away",
                slot, event_id, event["name"], days_until,
            )
        except Exception as e:
            logger.error(
                "Failed to send calendar reminder for event %d: %s",
                event_id, e,
            )

    conn.close()
    if sent_count:
        logger.info(
            "Calendar reminder check (%s) complete: %d reminders sent.",
            slot, sent_count,
        )


def _event_type_label(value):
    """Human-readable event type label (mirrors app.py filter)."""
    labels = {
        "conference": "Conference",
        "tradeshow": "Tradeshow",
        "webinar": "Webinar",
        "workshop": "Workshop",
        "meetup": "Meetup",
        "lunch_and_learn": "Lunch & Learn",
        "other": "Other",
    }
    return labels.get(value, value.replace("_", " ").title() if value else "")
