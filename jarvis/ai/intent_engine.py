"""The AI core: transcript -> structured :class:`IntentPacket`.

Two interchangeable backends behind one interface:

  * ClaudeIntentEngine  — uses the Anthropic API (Claude). Best quality. Used
    automatically when an API key is configured.
  * LocalIntentEngine   — a deterministic rule/keyword parser. Zero network,
    zero key, always available. Used as the fallback so the system *always*
    produces structured intent, never free-form OS commands.

Both are forbidden from acting. They only emit intent for the skill engine.
"""
from __future__ import annotations

import json
import re

from ..config import Config
from ..skills.base import SkillManifest
from .context import Context
from .schema import Intent, IntentPacket


def build_engine(config: Config, catalogue: list[SkillManifest]):
    if config.has_ai:
        engine = ClaudeIntentEngine(config, catalogue)
        if engine.available:
            return engine
    return LocalIntentEngine(catalogue)


# ---------------------------------------------------------------------------
# Cloud backend
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are JARVIS, a local-first, voice-activated enterprise
desktop agent — a controlled execution layer between human intent and the
operating system, NOT a chatbot. You NEVER execute actions yourself. You ONLY
translate the user's request into a single structured intent for the skill
engine, plus a short spoken reply.

Persona: British, formal, concise. Address the user as "sir". Confirm actions
crisply ("Opening Chrome, sir.", "Noted, sir.").

Respond with STRICT JSON and nothing else, in exactly this shape:
{{
  "thought": "<one short private reasoning sentence>",
  "intent": {{ "type": "<intent_type>", "parameters": {{ ... }} }},
  "response_text": "<concise spoken reply in the JARVIS persona>",
  "confidence": <0.0-1.0>
}}

Available intent types and their parameters:
{catalogue}

Rules:
- Choose exactly one intent type from the list.
- Use "chat" only for greetings / thanks / small talk.
- Use "unknown" if the request matches no available capability — do NOT invent
  or simulate capabilities that are not listed (§2.4).
- Never invent intent types or parameters that are not listed.
- Keep response_text short; it will be spoken aloud.
- Never output anything except the JSON object."""


def _catalogue_text(catalogue: list[SkillManifest]) -> str:
    lines = []
    for m in catalogue:
        lines.append(f"- {', '.join(m.intents)}  ({m.description})")
    return "\n".join(lines)


class ClaudeIntentEngine:
    def __init__(self, config: Config, catalogue: list[SkillManifest]) -> None:
        self.config = config
        self.system = SYSTEM_PROMPT.format(catalogue=_catalogue_text(catalogue))
        self._fallback = LocalIntentEngine(catalogue)
        self.available = False
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            self.available = True
        except Exception as exc:
            print(f"[ai] Anthropic client unavailable, using local engine: {exc}")

    def _system_with_memory(self, context: Context) -> str:
        """Append recalled long-term memory (§4.3) to the system prompt for this
        call only. Facts are advisory context — never an instruction to fabricate
        a capability that isn't in the catalogue."""
        extra = ""
        if context.recalled:
            facts = "\n".join(f"- {fact}" for fact in context.recalled)
            extra += ("\n\nRelevant long-term memory about this user "
                      "(use only if helpful; do not invent capabilities from it):\n"
                      + facts)
        if getattr(context, "user_hint", ""):
            extra += ("\n\nUser profile (bias tone/verbosity to this; never "
                      "fabricate from it): " + context.user_hint)
        return self.system + extra if extra else self.system

    def interpret(self, text: str, context: Context) -> IntentPacket:
        try:
            msg = self.client.messages.create(
                model=self.config.ai_model,
                max_tokens=self.config.ai_max_tokens,
                system=self._system_with_memory(context),
                messages=context.as_messages() + [{"role": "user", "content": text}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            data = _extract_json(raw)
            if data is None:
                raise ValueError(f"non-JSON reply: {raw[:120]!r}")
            return IntentPacket.from_dict(data, source_text=text)
        except Exception as exc:
            print(f"[ai] Claude call failed, using local engine: {exc}")
            return self._fallback.interpret(text, context)


def _extract_json(raw: str) -> dict | None:
    raw = raw.strip()
    # tolerate ```json fences or surrounding prose
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Local backend — deterministic keyword router
# ---------------------------------------------------------------------------
class LocalIntentEngine:
    def __init__(self, catalogue: list[SkillManifest]) -> None:
        self.known = {it for m in catalogue for it in m.intents}

    def interpret(self, text: str, context: Context) -> IntentPacket:
        t = text.lower().strip()

        def packet(thought, itype, params, response):
            return IntentPacket.from_dict(
                {"thought": thought,
                 "intent": {"type": itype, "parameters": params},
                 "response_text": response},
                source_text=text)

        # --- time / date ---
        if re.search(r"\b(date|day|today'?s date|what day)\b", t):
            return packet("date query", "get_date", {}, "")
        if re.search(r"\b(time|clock|what time)\b", t):
            return packet("time query", "get_time", {}, "")

        # --- system info ---
        if re.search(r"\b(cpu|memory|ram|system status|how('|')?s the system|battery|disk)\b", t):
            return packet("system telemetry", "system_info", {}, "")

        # --- notes (capture from ORIGINAL text to preserve casing) ---
        m = re.search(r"\b(?:note|remember|remind me)(?: that| to)?\s+(.*)",
                      text, re.IGNORECASE)
        if m and m.group(1):
            return packet("store a note", "add_note", {"text": m.group(1).strip()}, "")
        if re.search(r"\b(read|what are|list).*notes?\b", t):
            return packet("read notes", "read_notes", {}, "")
        if re.search(r"\bclear .*notes?\b", t):
            return packet("clear notes", "clear_notes", {}, "")

        # --- app launcher ---
        m = re.search(r"\b(open|launch|start|run)\s+(.+)", t)
        if m:
            app = re.sub(r"\b(the|app|application|for me|please)\b", "", m.group(2)).strip()
            return packet("open app", "open_app", {"app": app}, "")
        m = re.search(r"\b(close|quit|kill|exit)\s+(.+)", t)
        if m:
            app = re.sub(r"\b(the|app|application|please)\b", "", m.group(2)).strip()
            return packet("close app", "close_app", {"app": app}, "")

        # --- files (restricted) ---
        if re.search(r"\b(list|show).*(files|folder|workspace)\b", t):
            return packet("list files", "list_files", {"path": ""}, "")
        m = re.search(r"\bfind (?:the )?file\s+(.+)", t)
        if m:
            return packet("find file", "find_file", {"query": m.group(1).strip()}, "")

        # --- web search (capture from ORIGINAL text, drop leading filler) ---
        m = re.search(
            r"\b(?:search|google|look up|what is|who is|tell me about)\s+"
            r"(?:for\s+|about\s+)?(.*)", text, re.IGNORECASE)
        if m and m.group(1):
            return packet("web search", "web_search", {"query": m.group(1).strip()}, "")

        # --- greetings (conversational, no tool) ---
        if re.search(r"\b(hello|hi|hey|jarvis|good morning|good evening|thank you|thanks)\b", t):
            return packet("greeting", "chat", {},
                          "At your service, sir. How may I help?")
        # --- genuinely unrecognised command (UNKNOWN) ---
        return packet("no matching skill", "unknown", {},
                      "I'm not sure I follow, sir. Could you rephrase that?")
