"""In-app web research, search results, and bounded webpage extraction."""
from __future__ import annotations

import ipaddress
import socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse

from ...ai.schema import Intent, SkillResult
from ..base import Skill

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

_HEADERS = {"User-Agent": "AXON/1.3 local research assistant"}
_MAX_PAGE_BYTES = 1_000_000


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.paragraphs: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in {"title", "p", "article", "h1", "h2"}:
            self._capture = tag
            self._buffer = []

    def handle_data(self, data) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag) -> None:
        if tag != self._capture:
            return
        text = " ".join(" ".join(self._buffer).split())
        if text:
            if tag == "title":
                self.title.append(text)
            else:
                self.paragraphs.append(text)
        self._capture = None
        self._buffer = []


def _public_http_url(value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443,
                                       type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global:
                return None
        return value.strip()
    except (OSError, ValueError):
        return None


class WebSearchSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if requests is None:
            return self.fail("In-app research requires the requests package.")
        if intent.type == "read_webpage":
            return self._read_page(str(intent.get("url", "")))
        query = str(intent.get("query", "")).strip()
        if not query or len(query) > 500:
            return self.fail("A search query of 1-500 characters is required.")
        return self._search(query, research=intent.type == "research_web")

    def _search(self, query: str, *, research: bool) -> SkillResult:
        instant = self._instant_answer(query)
        results = self._search_results(query, limit=8 if research else 5)
        if instant and not any(item["url"] == instant.get("url")
                               for item in results):
            results.insert(0, instant)
        if not results:
            return self.fail(
                "No in-app search results were available.",
                speak="I couldn't retrieve search results just now, sir.",
                query=query, results=[])
        lead = next((item.get("snippet", "") for item in results
                     if item.get("snippet")), results[0]["title"])
        lead = lead[:500]
        summary = (f"Research for {query}: " if research else
                   f"Search results for {query}: ") + lead
        return self.ok(summary,
                       speak=f"{lead}, sir.", query=query,
                       results=results, sources=[item["url"] for item in results],
                       source="in-app research", opened_browser=False)

    @staticmethod
    def _instant_answer(query: str) -> dict | None:
        try:
            response = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1,
                        "skip_disambig": 1}, headers=_HEADERS, timeout=4)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None
        for key in ("AbstractText", "Answer", "Definition"):
            value = str(data.get(key) or "").strip()
            if value:
                return {"title": str(data.get("Heading") or query),
                        "url": str(data.get("AbstractURL") or
                                   "https://duckduckgo.com/"),
                        "snippet": value[:800], "provider": "DuckDuckGo"}
        return None

    @staticmethod
    def _search_results(query: str, limit: int = 5) -> list[dict]:
        try:
            response = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss"}, headers=_HEADERS,
                timeout=6)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception:
            return []
        results = []
        for item in root.findall(".//item")[:limit]:
            title = " ".join((item.findtext("title") or "").split())
            url = (item.findtext("link") or "").strip()
            snippet = " ".join((item.findtext("description") or "").split())
            if title and url:
                results.append({"title": title[:240], "url": url,
                                "snippet": snippet[:800], "provider": "Bing"})
        return results

    def _read_page(self, raw_url: str) -> SkillResult:
        url = _public_http_url(raw_url)
        if url is None:
            return self.fail("Provide a public HTTP or HTTPS webpage URL.")
        try:
            response = requests.get(url, headers=_HEADERS, timeout=8,
                                    stream=True, allow_redirects=False)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                return self.fail("That URL did not return a readable text page.")
            chunks = []
            total = 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > _MAX_PAGE_BYTES:
                    return self.fail("The webpage exceeds the 1 MB reading limit.")
                chunks.append(chunk)
            body = b"".join(chunks).decode(response.encoding or "utf-8",
                                             errors="replace")
        except Exception as exc:
            return self.fail(f"Could not read the webpage: {exc}",
                             speak="I couldn't read that webpage, sir.")
        if "text/plain" in content_type:
            title, text = urlparse(url).netloc, " ".join(body.split())[:8000]
        else:
            parser = _PageText()
            parser.feed(body)
            title = parser.title[0] if parser.title else urlparse(url).netloc
            text = "\n\n".join(parser.paragraphs)[:8000]
        if not text:
            return self.fail("No readable page text was found.")
        preview = text[:500]
        return self.ok(f"{title}: {preview}",
                       speak=f"I read {title}. {preview}, sir.",
                       title=title, url=url, text=text, source=url,
                       bytes=total)


SKILL = WebSearchSkill()
