from types import SimpleNamespace

from axon.ai.schema import Intent
from axon.skills.web_search import handler


class Response:
    def __init__(self, *, json_data=None, content=b"", body=b"",
                 content_type="application/xml"):
        self._json = json_data
        self.content = content
        self._body = body
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None

    def json(self):
        return self._json

    def iter_content(self, _size):
        yield self._body


def test_search_returns_sources_inside_app_without_browser(monkeypatch):
    rss = (b"<rss><channel><item><title>AXON docs</title>"
           b"<link>https://example.com/axon</link>"
           b"<description>Local voice assistant documentation.</description>"
           b"</item></channel></rss>")

    def get(url, **_kwargs):
        if "duckduckgo" in url:
            return Response(json_data={})
        return Response(content=rss)

    monkeypatch.setattr(handler, "requests", SimpleNamespace(get=get))

    result = handler.SKILL.execute(Intent(
        type="research_web", parameters={"query": "AXON"}))

    assert result.ok is True
    assert result.data["opened_browser"] is False
    assert result.data["results"][0]["title"] == "AXON docs"
    assert result.data["sources"] == ["https://example.com/axon"]


def test_read_webpage_returns_bounded_text(monkeypatch):
    page = b"<html><title>Guide</title><p>Useful local content.</p></html>"
    monkeypatch.setattr(handler, "_public_http_url", lambda url: url)
    monkeypatch.setattr(handler, "requests", SimpleNamespace(
        get=lambda *_args, **_kwargs: Response(
            body=page, content_type="text/html")))

    result = handler.SKILL.execute(Intent(
        type="read_webpage", parameters={"url": "https://example.com"}))

    assert result.ok is True
    assert result.data["title"] == "Guide"
    assert result.data["text"] == "Useful local content."


def test_private_or_local_webpage_address_is_rejected(monkeypatch):
    monkeypatch.setattr(handler.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (None, None, None, None, ("127.0.0.1", 443))])

    assert handler._public_http_url("http://localhost/admin") is None
