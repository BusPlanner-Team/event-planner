import logging

import requests

logger = logging.getLogger(__name__)


class OutlookClient:
    """Microsoft Graph Calendar API client using OAuth2 client credentials flow.

    Creates and updates Outlook calendar events. Requires Azure AD app registration
    with Calendars.ReadWrite application permission and admin consent.

    Auth: Client credentials grant (no user interaction needed).
    Token endpoint: https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token
    Graph endpoint: https://graph.microsoft.com/v1.0
    """

    TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    GRAPH_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, tenant_id, client_id, client_secret, organizer_email):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.organizer_email = organizer_email
        self.session = requests.Session()
        self._access_token = None

    def _get_token(self):
        """Acquire an access token using OAuth2 client credentials flow."""
        resp = requests.post(
            self.TOKEN_URL.format(tenant_id=self.tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        return self._access_token

    def _headers(self):
        """Return auth headers, refreshing token if needed."""
        if not self._access_token:
            self._get_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method, path, json_data=None):
        """Make a Graph API request with automatic token retry on 401."""
        url = f"{self.GRAPH_URL}{path}"
        resp = self.session.request(method, url, headers=self._headers(), json=json_data)

        # If 401, token may have expired — retry once
        if resp.status_code == 401:
            self._get_token()
            resp = self.session.request(method, url, headers=self._headers(), json=json_data)

        resp.raise_for_status()
        # Some responses (204 No Content) have no body
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def verify_connection(self):
        """Test credentials by fetching the organizer's calendar list."""
        try:
            self._request("GET", f"/users/{self.organizer_email}/calendars")
            return True
        except Exception:
            return False

    def create_event(self, subject, start_datetime, end_datetime, location,
                     description, attendee_emails, timezone="Eastern Standard Time"):
        """Create a calendar event and send meeting invitations.

        Args:
            subject: Event name/title.
            start_datetime: ISO 8601 datetime string, e.g. "2026-03-15T09:00:00".
            end_datetime: ISO 8601 datetime string, e.g. "2026-03-15T17:00:00".
            location: Location string (can be empty/None).
            description: Event description (plain text).
            attendee_emails: List of email addresses to invite.
            timezone: Microsoft timezone identifier (default: Eastern Standard Time).

        Returns:
            dict with 'id' (the Graph event ID) and full 'response'.
        """
        attendees = [
            {
                "emailAddress": {"address": email, "name": email.split("@")[0]},
                "type": "required",
            }
            for email in attendee_emails
        ]

        body = {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": description or "",
            },
            "start": {
                "dateTime": start_datetime,
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": timezone,
            },
            "attendees": attendees,
            "isOnlineMeeting": False,
        }

        if location:
            body["location"] = {"displayName": location}

        result = self._request(
            "POST",
            f"/users/{self.organizer_email}/calendar/events",
            body,
        )
        return {"id": result.get("id"), "response": result}

    def add_attendee(self, event_id, email, existing_attendees=None):
        """Add an attendee to an existing calendar event.

        Microsoft Graph PATCH replaces the attendees list, so we must
        include all existing attendees plus the new one.

        Args:
            event_id: The Microsoft Graph event ID.
            email: Email address of the new attendee.
            existing_attendees: List of existing attendee email strings.
                If None, fetches current attendees from the event.

        Returns:
            The updated event response.
        """
        if existing_attendees is None:
            # Fetch current event to get existing attendees
            event = self._request(
                "GET",
                f"/users/{self.organizer_email}/calendar/events/{event_id}"
                "?$select=attendees",
            )
            existing_attendees = [
                a["emailAddress"]["address"]
                for a in event.get("attendees", [])
            ]

        # Avoid duplicates
        all_emails = list(set(existing_attendees + [email]))
        attendees = [
            {
                "emailAddress": {"address": e, "name": e.split("@")[0]},
                "type": "required",
            }
            for e in all_emails
        ]

        return self._request(
            "PATCH",
            f"/users/{self.organizer_email}/calendar/events/{event_id}",
            {"attendees": attendees},
        )
