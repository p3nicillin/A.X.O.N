"""LocalLLMBackend — the new default AI core. Parses intent with a local LLM via
:class:`LocalRuntime`, enforcing structured output at every layer (§4).

Pipeline per utterance:
    schema-constrained chat  ->  validate against the live schema
                             ->  on failure: ONE repair retry
                             ->  still invalid: raise IntentBackendError

Malformed model output never reaches the skill engine: it is repaired, or the
backend raises and the router falls back to rules. The capability boundary holds.
"""
from __future__ import annotations

import json
import re
import time

from ..context import Context
from ..schema import IntentPacket
from .base import IntentBackend, IntentBackendError, IntentSpec
from .runtime import LocalRuntime, RuntimeError_
from .schema_gen import (build_json_schema, build_system_prompt,
                         repair_instruction, validate_packet_dict)


def _extract_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class LocalLLMBackend(IntentBackend):
    name = "local"

    def __init__(self, config, runtime: LocalRuntime | None = None) -> None:
        self.config = config
        ai = getattr(config, "ai", None)
        local = getattr(ai, "local", None) or getattr(config, "local", None)
        self.runtime = runtime or LocalRuntime(
            runtime=getattr(local, "runtime", "ollama") if local else "ollama",
            endpoint=getattr(local, "endpoint", "http://localhost:11434")
            if local else "http://localhost:11434",
            model=getattr(local, "model", "llama3.2:3b") if local else "llama3.2:3b",
            keep_alive=getattr(local, "keep_alive", "30m") if local else "30m",
        )
        self.temperature = getattr(local, "temperature", 0.1) if local else 0.1
        self.timeout = (getattr(local, "timeout_ms", 10000) / 1000.0
                        if local else 10.0)
        self._healthy: bool | None = None
        self._health_at = 0.0
        self._health_ttl = 30.0           # cache health so we don't probe per call

    @property
    def model_name(self) -> str:
        return self.runtime.model

    # -- health (cached) -----------------------------------------------------
    def available(self) -> bool:
        now = time.monotonic()
        if self._healthy is not None and now - self._health_at < self._health_ttl:
            return self._healthy
        ok, _ = self.runtime.health()
        self._healthy = ok
        self._health_at = now
        return ok

    def health_detail(self) -> tuple[bool, str]:
        ok, detail = self.runtime.health()
        self._healthy, self._health_at = ok, time.monotonic()
        return ok, detail

    def warm(self) -> bool:
        return self.runtime.warm()

    # -- parse ---------------------------------------------------------------
    def _system(self, specs: list[IntentSpec], context: Context) -> str:
        extra = ""
        if context.recalled:
            facts = "\n".join(f"- {f}" for f in context.recalled)
            extra += ("\nRelevant long-term memory (use only if helpful; do not "
                      "invent capabilities from it):\n" + facts)
        if getattr(context, "user_hint", ""):
            extra += "\nUser profile (bias tone only): " + context.user_hint
        return build_system_prompt(specs, extra=extra)

    def parse(self, transcript: str, context: Context,
              allowed_intents: list[IntentSpec]) -> IntentPacket:
        schema = build_json_schema(allowed_intents)
        messages = [{"role": "system", "content": self._system(allowed_intents,
                                                               context)}]
        messages += context.as_messages()
        messages.append({"role": "user", "content": transcript})

        max_tokens = 256
        try:
            raw = self.runtime.chat(messages, schema=schema,
                                    temperature=self.temperature,
                                    max_tokens=max_tokens, timeout=self.timeout)
        except RuntimeError_ as exc:
            raise IntentBackendError(f"runtime error: {exc}",
                                     backend=self.name) from exc

        data = _extract_json(raw)
        ok, err = (False, "non-JSON reply") if data is None else \
            validate_packet_dict(data, allowed_intents)
        repaired = False

        if not ok:
            # §4: exactly one repair retry, feeding the error back.
            repaired = True
            messages.append({"role": "assistant", "content": raw or ""})
            messages.append({"role": "user",
                             "content": repair_instruction(err, schema)})
            try:
                raw = self.runtime.chat(messages, schema=schema,
                                        temperature=0.0, max_tokens=max_tokens,
                                        timeout=self.timeout)
            except RuntimeError_ as exc:
                raise IntentBackendError(f"runtime error on repair: {exc}",
                                         backend=self.name) from exc
            data = _extract_json(raw)
            ok, err = (False, "non-JSON reply") if data is None else \
                validate_packet_dict(data, allowed_intents)
            if not ok:
                raise IntentBackendError(f"schema invalid after repair: {err}",
                                         backend=self.name)

        return IntentPacket.from_dict(data, source_text=transcript).tag(
            repaired=repaired)
