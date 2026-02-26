import time

import requests


class SlackClient:
    """Slack Web API client using Bot token (xoxb-).

    Required bot scopes: chat:write, channels:read.
    Rate limit: 1 message per second per channel.
    """

    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.base_url = "https://slack.com/api"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        })
        self._last_post_time = {}

    def _post(self, method, json_data=None):
        resp = self.session.post(f"{self.base_url}/{method}", json=json_data)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
        return data

    def _rate_limit(self, channel):
        now = time.time()
        last = self._last_post_time.get(channel, 0)
        wait = 1.1 - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_post_time[channel] = time.time()

    def verify_connection(self):
        try:
            data = self._post("auth.test")
            return data.get("ok", False)
        except Exception:
            return False

    def post_message(self, channel, text, blocks=None):
        self._rate_limit(channel)
        payload = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        return self._post("chat.postMessage", payload)

    def post_task_assigned(self, channel, task_title, event_name,
                           assignee_slack_id, assignee_name, due_date, task_url):
        mention = f"<@{assignee_slack_id}>" if assignee_slack_id else assignee_name
        text = f"New task assigned: {task_title} for {event_name}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":clipboard: *New Task Assigned*\n"
                        f"*{task_title}* for _{event_name}_\n"
                        f":bust_in_silhouette: Assigned to: {mention}\n"
                        f":calendar: Due: {due_date}\n"
                        f":link: <{task_url}|View Task>"
                    ),
                },
            },
        ]
        return self.post_message(channel, text, blocks)

    def post_task_due_reminder(self, channel, task_title, event_name,
                               assignee_slack_id, assignee_name, due_date,
                               days_until, task_url):
        mention = f"<@{assignee_slack_id}>" if assignee_slack_id else assignee_name

        if days_until == 7:
            emoji, urgency = ":bell:", "Due in 7 Days"
        elif days_until == 2:
            emoji, urgency = ":warning:", "Due in 2 Days"
        elif days_until == 1:
            emoji, urgency = ":rotating_light:", "Due Tomorrow"
        else:
            emoji, urgency = ":fire:", "Due Today!"

        text = f"Task {urgency}: {task_title} for {event_name}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *Task {urgency}*\n"
                        f"*{task_title}* for _{event_name}_\n"
                        f":bust_in_silhouette: Assigned to: {mention}\n"
                        f":calendar: Due: {due_date}\n"
                        f":link: <{task_url}|View Task>"
                    ),
                },
            },
        ]
        return self.post_message(channel, text, blocks)

    def post_approval_needed(self, channel, task_title, event_name,
                             step_label, step_number, total_steps,
                             approver_slack_id, task_url):
        mention = f"<@{approver_slack_id}>" if approver_slack_id else "Approver"
        text = f"Approval needed: {step_label} for {task_title}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":eyes: *Approval Needed*\n"
                        f"*{task_title}* for _{event_name}_\n"
                        f":arrow_right: Step: *{step_label}* (Step {step_number} of {total_steps})\n"
                        f":bust_in_silhouette: Waiting on: {mention}\n"
                        f":link: <{task_url}|Review & Approve>"
                    ),
                },
            },
        ]
        return self.post_message(channel, text, blocks)

    def post_approval_result(self, channel, task_title, event_name,
                             step_label, result, next_step_label=None,
                             feedback=None):
        if result == "approved":
            if next_step_label:
                text = f"{step_label} approved for {task_title} — now with {next_step_label}"
                msg = f":white_check_mark: *Step Approved*\n*{task_title}* for _{event_name}_\n:arrow_right: *{step_label}* approved — now with *{next_step_label}*"
            else:
                text = f"All approvals complete for {task_title}"
                msg = f":tada: *All Approvals Complete*\n*{task_title}* for _{event_name}_\n:arrow_right: All steps approved — task is complete!"
        else:
            text = f"{step_label} rejected for {task_title}"
            msg = f":x: *Step Rejected*\n*{task_title}* for _{event_name}_\n:arrow_right: *{step_label}* rejected"
            if next_step_label:
                msg += f" — sent back to *{next_step_label}*"
            if feedback:
                msg += f"\n:speech_balloon: Feedback: \"{feedback}\""

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": msg}}]
        return self.post_message(channel, text, blocks)

    # --- V2: Daily Calendar Reminders ---

    def post_calendar_reminder(self, channel, event_name, event_date,
                                event_type, location, days_until, event_url):
        """Post a daily reminder for an upcoming event with a calendar invite."""
        if days_until == 0:
            urgency = ":rotating_light: *Today!*"
        elif days_until == 1:
            urgency = ":warning: *Tomorrow!*"
        elif days_until <= 7:
            urgency = f":bell: *{days_until} days away*"
        else:
            urgency = f":calendar: *{days_until} days away*"

        location_line = f":round_pushpin: {location}\n" if location else ""
        text = f"Event Reminder: {event_name} — {event_date}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{urgency}\n"
                        f":mega: *{event_name}*\n"
                        f":calendar: {event_date} ({event_type})\n"
                        f"{location_line}"
                        f":link: <{event_url}|View Event>"
                    ),
                },
            },
        ]
        return self.post_message(channel, text, blocks)

    # --- User lookup cache ---

    _user_cache = {}

    def get_user_name(self, user_id):
        """Resolve a Slack user ID to a display name."""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            resp = self.session.get(
                f"{self.base_url}/users.info", params={"user": user_id}
            )
            data = resp.json()
            if data.get("ok"):
                profile = data["user"].get("profile", {})
                name = (profile.get("display_name")
                        or profile.get("real_name")
                        or data["user"].get("real_name")
                        or user_id)
            else:
                name = user_id
        except Exception:
            name = user_id
        self._user_cache[user_id] = name
        return name

    # --- V2: Channel History ---

    def get_channel_history(self, channel_id, oldest=None, limit=200):
        """Fetch message history from a Slack channel.

        Requires channels:history and/or groups:history bot scopes.
        Returns list of message dicts with 'user', 'text', 'ts' fields.
        """
        params = {"channel": channel_id, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        resp = self.session.get(
            f"{self.base_url}/conversations.history", params=params
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
        return data.get("messages", [])

    # --- V2: Escalation ---

    def post_escalation_reminder(self, channel, task_title, event_name,
                                  step_label, approver_slack_id, approver_name):
        """Send an urgent escalation message tagging the approver."""
        mention = f"<@{approver_slack_id}>" if approver_slack_id else approver_name
        text = f"URGENT: {step_label} overdue for {task_title}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":rotating_light: *Escalation — Approval Overdue*\n"
                        f"*{task_title}* for _{event_name}_\n"
                        f":arrow_right: Step: *{step_label}*\n"
                        f":bust_in_silhouette: {mention} — this has been waiting over 2 hours.\n"
                        f"Please review and approve/reject as soon as possible."
                    ),
                },
            },
        ]
        return self.post_message(channel, text, blocks)
