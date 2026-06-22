"""CloudBackend — the optional, opt-in Claude path behind the IntentBackend
interface.

DISABLED by default (see config ``[ai.cloud] enabled = false``). Enabling it is a
material privacy change: the key is loaded only from the secrets store (never
plaintext config) and every utterance it parses is flagged ``cloud_routed`` so
the audit trail records that data left the device (§8).
"""
from __future__ import annotations

import json
import re

from ..context import Context
from ..schema import IntentPacket
from ..secrets import get_anthropic_key
from .base import IntentBackend, IntentBackendError, IntentSpec
from .schema_gen import build_system_prompt, validate_packet_dict


def _extract_json(raw: str) -> dict | None:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class CloudBackend(IntentBackend):
    name = "cloud"

    def __init__(self, config) -> None:
        self.config = config
        cloud = getattr(getattr(config, "ai", None), "cloud", None)
        self._model = (getattr(cloud, "model", None)
                       or getattr(config, "ai_model", "claude-haiku-4-5-20251001"))
        self._key = get_anthropic_key(config)
        self._client = None
        if self._key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._key)
            except Exception as exc:
                print(f"[ai.cloud] Anthropic client unavailable: {exc}")
                self._client = None

    @property
    def model_name(self) -> str:
        return self._model

    def available(self) -> bool:
        return self._client is not None

    def _system(self, specs: list[IntentSpec], context: Context) -> str:
        extra = ""
        if context.recalled:
            facts = "\n".join(f"- {f}" for f in context.recalled)
            extra += ("\nRelevant long-term memory (use only if helpful; do not "
                      "invent capabilities from it):\n" + facts)
        if getattr(context, "user_hint", ""):
            extra += "\nUser profile (bias tone only): " + context.user_hint
        if getattr(context, "desktop_hint", ""):
            extra += ("\nCurrent desktop context (read-only; do not infer hidden "
                      "content): " + context.desktop_hint)
        return build_system_prompt(specs, extra=extra)

    def parse(self, transcript: str, context: Context,
              allowed_intents: list[IntentSpec]) -> IntentPacket:
        if self._client is None:
            raise IntentBackendError("cloud client not configured",
                                     backend=self.name, retryable=False)
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=getattr(self.config, "ai_max_tokens", 512),
                system=self._system(allowed_intents, context),
                messages=context.as_messages()
                + [{"role": "user", "content": transcript}],
            )
            raw = "".join(b.text for b in msg.content
                          if getattr(b, "type", "") == "text")
        except Exception as exc:
            raise IntentBackendError(f"cloud call failed: {exc}",
                                     backend=self.name) from exc

        data = _extract_json(raw)
        if data is None:
            raise IntentBackendError(f"non-JSON reply: {raw[:120]!r}",
                                     backend=self.name)
        ok, err = validate_packet_dict(data, allowed_intents)
        if not ok:
            raise IntentBackendError(f"schema invalid: {err}", backend=self.name)
        return IntentPacket.from_dict(data, source_text=transcript)
