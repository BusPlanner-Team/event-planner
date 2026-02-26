"""WSGI entry point for production (gunicorn).

Imports the Flask app, ensures the database exists, and starts the
background scheduler so that notifications and escalation checks
continue to run in production.

Usage (Azure App Service / gunicorn):
    gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 1 wsgi:app
"""

import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Flask app (also runs init_db on import) ---------------------------------
from app import app  # noqa: E402
from config import Config  # noqa: E402
from database.db import get_config, get_connection  # noqa: E402
from jobs.scheduler import init_scheduler  # noqa: E402

# --- Background scheduler (notifications + escalation) -----------------------
try:
    conn = get_connection(Config.DATABASE_PATH)
    check_minutes = int(
        get_config(conn, "notification_check_minutes", Config.NOTIFICATION_CHECK_MINUTES)
    )
    escalation_minutes = int(
        get_config(conn, "escalation_check_minutes", Config.ESCALATION_CHECK_MINUTES)
    )
    tz = get_config(conn, "timezone") or Config.TIMEZONE
    conn.close()

    scheduler = init_scheduler(Config.DATABASE_PATH, check_minutes, escalation_minutes, tz)
    logger.info("Background scheduler started in production.")
except Exception as exc:
    logger.error("Failed to start scheduler: %s", exc)
