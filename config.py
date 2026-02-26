import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    DATABASE_PATH = os.getenv(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(__file__), "event_planner.db"),
    )

    # Slack
    SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_NOTIFICATION_CHANNEL = os.getenv("SLACK_NOTIFICATION_CHANNEL", "")

    # MailChimp
    MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY", "")

    # Anthropic (Claude AI)
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # Outlook (Microsoft Graph)
    OUTLOOK_TENANT_ID = os.getenv("OUTLOOK_TENANT_ID", "")
    OUTLOOK_CLIENT_ID = os.getenv("OUTLOOK_CLIENT_ID", "")
    OUTLOOK_CLIENT_SECRET = os.getenv("OUTLOOK_CLIENT_SECRET", "")
    OUTLOOK_ORGANIZER_EMAIL = os.getenv("OUTLOOK_ORGANIZER_EMAIL", "")

    # Scheduler
    NOTIFICATION_CHECK_MINUTES = int(os.getenv("NOTIFICATION_CHECK_MINUTES", "30"))
    ESCALATION_CHECK_MINUTES = int(os.getenv("ESCALATION_CHECK_MINUTES", "15"))
    TIMEZONE = os.getenv("TIMEZONE", "US/Eastern")
