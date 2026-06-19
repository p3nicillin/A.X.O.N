"""The AI core assembly point.

The core is now a pluggable :class:`IntentRouter` over several backends
(local LLM by default, optional cloud, deterministic rules as the guaranteed
fallback). :func:`build_engine` wires the chain from config and returns the
router, which exposes the same ``interpret(text, context)`` contract the
orchestrator has always used.

:class:`LocalIntentEngine` is the deterministic keyword parser. It is the heart
of the RuleBackend (fast-path + final fallback) and remains importable for tests.
Every backend is forbidden from acting — they only emit intent for the skill
engine. The capability boundary is preserved.
"""
from __future__ import annotations

import re

from ..config import Config
from ..skills.base import SkillManifest
from .backends import CloudBackend, LocalLLMBackend, RuleBackend
from .context import Context
from .router import IntentRouter
from .schema import IntentPacket


def _resolve_chain(config: Config, cloud_enabled: bool) -> list[str]:
    """Resolve the ordered backend chain (rules is always the implicit bottom)."""
    pref = (config.ai.engine or "local").lower()
    if pref == "rules":
        return []
    if pref == "cloud":
        chain = ["cloud", "local"]
    elif pref == "auto":
        chain = (["cloud"] if cloud_enabled else []) + ["local"]
    elif pref == "local":
        chain = [b for b in config.ai.fallback if b != "rules"] or ["local"]
    else:
        chain = ["local"]
    if not cloud_enabled:
        chain = [c for c in chain if c != "cloud"]
    # dedupe, keep only real LLM backends (rules handled separately)
    seen: set[str] = set()
    out: list[str] = []
    for c in chain:
        if c in ("local", "cloud") and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def build_engine(config: Config, catalogue: list[SkillManifest],
                 bus=None) -> IntentRouter:
    """Construct the AI-core router from config. Local-by-default; cloud is
    opt-in. Always returns a router whose worst case is the rule backend."""
    rules = RuleBackend(LocalIntentEngine(catalogue))
    backends: dict = {"local": LocalLLMBackend(config)}

    cloud_enabled = bool(getattr(config.ai.cloud, "enabled", False))
    if cloud_enabled:
        backends["cloud"] = CloudBackend(config)

    chain = _resolve_chain(config, cloud_enabled)
    return IntentRouter(config, catalogue, bus, backends=backends,
                        rule_backend=rules, chain=chain,
                        hybrid=bool(config.ai.hybrid_fastpath))


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

        # --- clipboard (set captures from ORIGINAL text to preserve casing) ---
        m = re.search(r"\b(?:copy|set clipboard to|put)\s+(.+?)\s+"
                      r"(?:to|on|in)(?: the)? clipboard\b", text, re.IGNORECASE)
        if m and m.group(1):
            return packet("set clipboard", "set_clipboard",
                          {"text": m.group(1).strip()}, "")
        if re.search(r"\bclipboard\b", t) and re.search(
                r"\b(read|show|what(?:'s| is)?|paste|whats)\b", t):
            return packet("read clipboard", "read_clipboard", {}, "")

        # --- media transport ---
        if re.search(r"\b(next|skip)\b.*\b(track|song)\b", t) or \
                re.search(r"\bnext track\b", t):
            return packet("next track", "next_track", {}, "")
        if re.search(r"\b(previous|last|go back)\b.*\b(track|song)\b", t) or \
                re.search(r"\bprevious track\b", t):
            return packet("previous track", "previous_track", {}, "")
        if re.search(r"\b(play|pause|resume)\b", t):
            return packet("toggle playback", "play_pause", {}, "")

        # --- volume ---
        if re.search(r"\b(mute|unmute)\b", t):
            return packet("toggle mute", "mute_toggle", {}, "")
        if re.search(r"\b(volume up|louder|raise (?:the )?volume|increase (?:the )?volume|turn (?:it |the volume )?up)\b", t):
            return packet("volume up", "volume_up", {}, "")
        if re.search(r"\b(volume down|quieter|softer|lower (?:the )?volume|decrease (?:the )?volume|turn (?:it |the volume )?down)\b", t):
            return packet("volume down", "volume_down", {}, "")

        # --- window state ---
        if re.search(r"\bminimi[sz]e\b", t):
            return packet("minimize window", "minimize_window", {}, "")
        if re.search(r"\bmaximi[sz]e\b", t):
            return packet("maximize window", "maximize_window", {}, "")
        if re.search(r"\brestore (?:the )?window\b", t):
            return packet("restore window", "restore_window", {}, "")

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
        if re.search(r"\b(hello|hi|hey|axon|good morning|good evening|thank you|thanks)\b", t):
            return packet("greeting", "chat", {},
                          "At your service, sir. How may I help?")
        # --- genuinely unrecognised command (UNKNOWN) ---
        return packet("no matching skill", "unknown", {},
                      "I'm not sure I follow, sir. Could you rephrase that?")
