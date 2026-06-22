"""Explicit local-only multimodal screen analysis via an Ollama-compatible API."""
from __future__ import annotations

import base64
from io import BytesIO
from urllib.parse import urlparse

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class LocalVisionClient:
    def __init__(self, endpoint: str, model: str, timeout: float = 30.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = max(2.0, min(float(timeout), 120.0))

    def configured(self) -> tuple[bool, str]:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"}:
            return False, "vision endpoint must use HTTP(S)"
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False, "vision endpoint must be local"
        if not self.model.strip():
            return False, "vision model is not configured"
        if requests is None:
            return False, "requests package is unavailable"
        return True, "configured"

    def analyze(self, image, prompt: str = "") -> dict:
        ok, reason = self.configured()
        if not ok:
            return {"ok": False, "error": reason}
        question = (prompt or (
            "Describe the visible screen precisely. Identify the active app, "
            "important text, controls, errors, and likely next safe actions. "
            "Do not infer content that is not visible.")).strip()
        if len(question) > 600:
            return {"ok": False, "error": "vision prompt exceeds 600 characters"}
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "user", "content": question,
                          "images": [encoded]}],
            "options": {"temperature": 0.1},
        }
        try:
            response = requests.post(self.endpoint + "/api/chat", json=payload,
                                     timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            analysis = str((data.get("message") or {}).get("content") or "").strip()
        except Exception as exc:
            return {"ok": False, "error": f"local vision request failed: {exc}"}
        if not analysis:
            return {"ok": False, "error": "local vision model returned no analysis"}
        return {"ok": True, "analysis": analysis[:6000],
                "model": self.model, "endpoint": self.endpoint,
                "image_persisted": False}
