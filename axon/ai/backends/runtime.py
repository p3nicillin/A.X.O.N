"""LocalRuntime — a thin adapter over a local LLM server.

Supports three shapes behind one method, selected by config:
  * ``ollama``            -> Ollama native ``/api/chat`` with JSON-schema ``format``
  * ``openai_compatible`` -> OpenAI ``/v1/chat/completions`` + ``response_format``
                             (covers llama.cpp ``llama-server``, LM Studio, Jan)
  * ``llamacpp``          -> alias of openai_compatible (llama-server speaks it)

Uses only the standard library (``urllib``) so it adds no dependency and stays
"free, minimal footprint". Everything is local HTTP to the user's own machine.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class RuntimeError_(Exception):
    """A local runtime call failed (network, timeout, bad status)."""


class LocalRuntime:
    def __init__(self, *, runtime: str = "ollama",
                 endpoint: str = "http://localhost:11434",
                 model: str = "llama3.2:3b",
                 keep_alive: str = "30m") -> None:
        self.runtime = (runtime or "ollama").lower()
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.last_tokens_per_sec: float = 0.0

    @property
    def _is_ollama(self) -> bool:
        return self.runtime == "ollama"

    # -- health --------------------------------------------------------------
    def health(self, timeout: float = 1.5) -> tuple[bool, str]:
        """Probe the runtime. Returns (reachable, detail). Never raises."""
        url = (f"{self.endpoint}/api/tags" if self._is_ollama
               else f"{self.endpoint}/v1/models")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            data = json.loads(body) if body else {}
            models = self._model_names(data)
            if models and self.model not in models and self._is_ollama:
                return False, f"model '{self.model}' not pulled (have: {models[:3]})"
            return True, f"{len(models)} model(s)"
        except urllib.error.URLError as exc:
            return False, f"unreachable: {getattr(exc, 'reason', exc)}"
        except Exception as exc:
            return False, f"probe failed: {exc}"

    @staticmethod
    def _model_names(data: dict) -> list[str]:
        if "models" in data:                      # ollama /api/tags
            return [m.get("name", "") for m in data["models"]]
        if "data" in data:                        # openai /v1/models
            return [m.get("id", "") for m in data["data"]]
        return []

    # -- chat ----------------------------------------------------------------
    def chat(self, messages: list[dict], *, schema: dict | None = None,
             temperature: float = 0.1, max_tokens: int = 256,
             timeout: float = 4.0) -> str:
        """Send a chat completion and return the raw assistant text."""
        if self._is_ollama:
            return self._chat_ollama(messages, schema, temperature,
                                     max_tokens, timeout)
        return self._chat_openai(messages, schema, temperature,
                                 max_tokens, timeout)

    def _post(self, path: str, payload: dict, timeout: float) -> dict:
        req = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.URLError as exc:
            raise RuntimeError_(f"{getattr(exc, 'reason', exc)}") from exc
        except TimeoutError as exc:
            raise RuntimeError_("timeout") from exc
        except Exception as exc:
            raise RuntimeError_(str(exc)) from exc

    def _chat_ollama(self, messages, schema, temperature, max_tokens, timeout):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if schema is not None:
            payload["format"] = schema
        t0 = time.monotonic()
        data = self._post("/api/chat", payload, timeout)
        self._record_tps(data, t0)
        return (data.get("message") or {}).get("content", "")

    def _chat_openai(self, messages, schema, temperature, max_tokens, timeout):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "intent_packet", "schema": schema,
                                "strict": True},
            }
        t0 = time.monotonic()
        data = self._post("/v1/chat/completions", payload, timeout)
        self._record_tps_openai(data, t0)
        choices = data.get("choices") or [{}]
        return (choices[0].get("message") or {}).get("content", "")

    def _record_tps(self, data: dict, t0: float) -> None:
        # ollama reports eval_count + eval_duration (ns)
        n = data.get("eval_count")
        dur = data.get("eval_duration")
        if n and dur:
            self.last_tokens_per_sec = n / (dur / 1e9)
        else:
            self._record_tps_openai(data, t0)

    def _record_tps_openai(self, data: dict, t0: float) -> None:
        usage = data.get("usage") or {}
        out = usage.get("completion_tokens")
        elapsed = max(1e-6, time.monotonic() - t0)
        if out:
            self.last_tokens_per_sec = out / elapsed

    def warm(self, timeout: float = 60.0) -> bool:
        """Best-effort preload / keep-alive so the first real command is fast."""
        try:
            self.chat([{"role": "user", "content": "ok"}],
                      schema=None, max_tokens=1, timeout=timeout)
            return True
        except Exception:
            return False
