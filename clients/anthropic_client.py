import json

import anthropic


class AnthropicClient:
    """Anthropic Claude API client for summarizing Slack messages.

    Uses the Claude 3.5 Haiku model to analyze event-planning conversations
    and extract summaries, decisions, action items, and deadlines.
    """

    MODEL = "claude-3-5-haiku-20241022"

    def __init__(self, api_key):
        self.client = anthropic.Anthropic(api_key=api_key)

    def verify_connection(self):
        """Test the API key by sending a minimal message."""
        try:
            self.client.messages.create(
                model=self.MODEL,
                max_tokens=16,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False

    def summarize_slack_messages(self, messages, event_name):
        """Analyze Slack messages for an event and return structured insights.

        Args:
            messages: list of dicts, each with 'user', 'text', and 'ts' keys.
            event_name: the name of the event being planned.

        Returns:
            dict with keys: summary (str), decisions (list[str]),
            action_items (list[str]), deadlines (list[str]).
        """
        formatted = "\n".join(
            f"[{msg.get('ts', '')}] {msg.get('user', 'unknown')}: {msg.get('text', '')}"
            for msg in messages
        )

        system_prompt = (
            "You are an assistant that analyzes Slack messages from an event-planning team. "
            "Focus ONLY on messages relevant to the specific event being planned. "
            "Ignore off-topic chatter, general announcements, or messages about other events. "
            "Given a transcript of messages, produce a JSON object with exactly these keys:\n"
            "  - \"summary\": a brief paragraph summarizing event-relevant discussion only.\n"
            "  - \"decisions\": a list of strings, each a key decision made about this event.\n"
            "  - \"action_items\": a list of strings, each an action item related to this event.\n"
            "  - \"deadlines\": a list of strings, each a deadline or date mentioned for this event.\n"
            "If no messages are relevant to the event, return an empty summary and empty lists.\n"
            "Return ONLY valid JSON with no extra text."
        )

        user_prompt = (
            f"Event: {event_name}\n\n"
            f"Analyze ONLY messages related to the \"{event_name}\" event.\n\n"
            f"Slack messages:\n{formatted}"
        )

        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw_text = response.content[0].text

            try:
                result = json.loads(raw_text)
                return {
                    "summary": result.get("summary", ""),
                    "decisions": result.get("decisions", []),
                    "action_items": result.get("action_items", []),
                    "deadlines": result.get("deadlines", []),
                }
            except (json.JSONDecodeError, IndexError, KeyError):
                return {
                    "summary": raw_text,
                    "decisions": [],
                    "action_items": [],
                    "deadlines": [],
                }

        except Exception as exc:
            raise RuntimeError(f"Error communicating with Claude API: {exc}") from exc
