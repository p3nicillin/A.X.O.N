"""WebSearchSkill — instant answers + browser fallback.

Tries DuckDuckGo's Instant Answer API for a spoken one-liner. If there's no
concise answer (or no network), it opens the query in the default browser so
the action is always user-visible. No API key required.
"""
from __future__ import annotations

import urllib.parse
import webbrowser

from ...ai.schema import Intent, SkillResult
from ..base import Skill

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class WebSearchSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        query = str(intent.get("query", "")).strip()
        if not query:
            return self.fail("No search query was provided.")

        answer = self._instant_answer(query) if requests else None
        if answer:
            return self.ok(answer, speak=answer, query=query, source="duckduckgo")

        # fallback: open the search so the result is visible to the user
        url = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        msg = f"I've opened a web search for {query}."
        return self.ok(msg, speak=msg, query=query, url=url, source="browser")

    @staticmethod
    def _instant_answer(query: str) -> str | None:
        try:
            resp = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1,
                        "skip_disambig": 1},
                timeout=4,
            )
            data = resp.json()
        except Exception:
            return None
        for key in ("AbstractText", "Answer", "Definition"):
            val = (data.get(key) or "").strip()
            if val:
                return val[:400]
        topics = data.get("RelatedTopics") or []
        if topics and isinstance(topics[0], dict):
            txt = (topics[0].get("Text") or "").strip()
            if txt:
                return txt[:400]
        return None


SKILL = WebSearchSkill()
