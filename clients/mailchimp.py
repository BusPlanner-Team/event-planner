import requests


class MailChimpClient:
    """MailChimp Marketing API v3 client.

    Auth: HTTP Basic Auth with "anystring" as username and API key as password.
    The data center is extracted from the API key suffix (e.g. "xxxxx-us21" -> "us21").
    """

    def __init__(self, api_key):
        self.api_key = api_key
        dc = api_key.split("-")[-1] if "-" in api_key else "us1"
        self.base_url = f"https://{dc}.api.mailchimp.com/3.0"
        self.session = requests.Session()
        self.session.auth = ("anystring", api_key)
        self.session.headers.update({"Content-Type": "application/json"})

    def _get(self, path, params=None):
        resp = self.session.get(f"{self.base_url}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _put(self, path, json_data=None):
        resp = self.session.put(f"{self.base_url}{path}", json=json_data)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path, json_data=None):
        resp = self.session.patch(f"{self.base_url}{path}", json=json_data)
        resp.raise_for_status()
        return resp.json()

    def verify_connection(self):
        try:
            data = self._get("/ping")
            return data.get("health_status") == "Everything's Chimpy!"
        except requests.HTTPError:
            return False

    def get_campaigns(self, count=100):
        data = self._get("/campaigns", params={"count": count, "sort_field": "create_time", "sort_dir": "DESC"})
        return data.get("campaigns", [])

    def get_campaign(self, campaign_id):
        return self._get(f"/campaigns/{campaign_id}")

    def get_campaign_content(self, campaign_id):
        """Get the HTML and plain text content of a campaign."""
        return self._get(f"/campaigns/{campaign_id}/content")

    def update_campaign_content(self, campaign_id, html_content, plain_text=None):
        """Set campaign HTML body (and optionally plain text)."""
        payload = {"html": html_content}
        if plain_text:
            payload["plain_text"] = plain_text
        return self._put(f"/campaigns/{campaign_id}/content", payload)

    def update_campaign_settings(self, campaign_id, subject_line=None, preview_text=None):
        """Update campaign settings (subject line, preview text) via PATCH."""
        settings = {}
        if subject_line is not None:
            settings["subject_line"] = subject_line
        if preview_text is not None:
            settings["preview_text"] = preview_text
        if settings:
            return self._patch(f"/campaigns/{campaign_id}", {"settings": settings})
        return None
