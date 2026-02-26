"""Entry point for the Event Marketing Planner server.

Usage:
    python run.py

Starts the Flask dev server on port 5003 with the background scheduler
for notifications, escalation reminders, and daily calendar reminders.
"""

import logging
import sys

logging.basicConfig(level=logging.INFO)

# Import the app (as a module, not __main__)
sys.path.insert(0, ".")
from app import app
from config import Config
from database.db import get_config, get_connection
from jobs.scheduler import init_scheduler


def main():
    conn = get_connection(Config.DATABASE_PATH)
    check_minutes = int(get_config(conn, "notification_check_minutes",
                                   Config.NOTIFICATION_CHECK_MINUTES))
    escalation_minutes = int(get_config(conn, "escalation_check_minutes",
                                        Config.ESCALATION_CHECK_MINUTES))
    tz = get_config(conn, "timezone") or Config.TIMEZONE
    conn.close()

    scheduler = init_scheduler(Config.DATABASE_PATH, check_minutes,
                               escalation_minutes, tz)

    try:
        app.run(host="0.0.0.0", port=5003, debug=True, use_reloader=False)
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
