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

        # --- deterministic arithmetic -------------------------------------
        if (re.search(r"\b(calculate|compute|work out)\b", t)
                or re.search(r"\d\s*(?:\+|-|\*|/|%|plus|minus|times|"
                             r"multiplied|divided)\s*\d", t)
                or re.search(r"\d+(?:\.\d+)?\s+percent of\s+\d", t)):
            expression = re.sub(
                r"^(?:please\s+)?(?:calculate|compute|work out|what is)\s+",
                "", t).strip(" ?.!")
            replacements = (
                (r"\bmultiplied by\b", "*"), (r"\btimes\b", "*"),
                (r"\bdivided by\b", "/"), (r"\bplus\b", "+"),
                (r"\bminus\b", "-"), (r"\bto the power of\b", "**"),
            )
            for pattern, replacement in replacements:
                expression = re.sub(pattern, replacement, expression)
            expression = re.sub(
                r"(\d+(?:\.\d+)?)\s+percent of\s+(\d+(?:\.\d+)?)",
                r"(\1/100)*\2", expression)
            return packet("arithmetic", "calculate",
                          {"expression": expression}, "")

        # --- weather (structured in-app result; never a browser fallback) ---
        if re.search(r"\b(weather|forecast|temperature outside)\b", t):
            location = ""
            m = re.search(r"\b(?:in|at|for)\s+(.+)$", text, re.IGNORECASE)
            if m:
                location = re.sub(
                    r"\s+\b(today|tonight|tomorrow|this week|next week)\b.*$",
                    "", m.group(1), flags=re.IGNORECASE).strip(" ?.!")
                if location.lower() in {"today", "tonight", "tomorrow",
                                       "this week", "next week"}:
                    location = ""
            days = 7 if re.search(r"\b(week|7 day)\b", t) else (
                3 if re.search(r"\b(forecast|next few days)\b", t) else (
                    2 if "tomorrow" in t else 1))
            params = {"days": days}
            if location:
                params["location"] = location
            return packet("weather query", "get_weather", params, "")

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

        # --- explicit screen capture (saved only inside the workspace) ---
        m = re.search(
            r"\b(?:take|capture|save)(?: a)? screenshot(?: (?:called|named)\s+([^\\/:*?\"<>|]+))?\b",
            text, re.IGNORECASE)
        if m:
            params = {"filename": m.group(1).strip()} if m.group(1) else {}
            return packet("capture screenshot", "capture_screenshot", params, "")

        # --- keyboard input (always confirmation-gated by its manifest) ---
        m = re.search(r"\btype\s+(.+)", text, re.IGNORECASE)
        if m and m.group(1).strip():
            return packet("type text", "type_text",
                          {"text": m.group(1).strip()}, "")
        m = re.search(
            r"\b(?:press(?: the)?\s+(.+?)(?:\s+key)?|"
            r"send(?: the)?\s+(.+?)\s+(?:key|keystroke|shortcut))$",
            text, re.IGNORECASE)
        keys = next((group.strip() for group in m.groups() if group), "") if m else ""
        if keys:
            return packet("send keystroke", "send_keystroke",
                          {"keys": keys}, "")

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
        if re.search(
                r"\b(volume up|louder|raise (?:the )?volume|"
                r"increase (?:the )?volume|turn (?:it |the volume )?up)\b", t):
            return packet("volume up", "volume_up", {}, "")
        if re.search(
                r"\b(volume down|quieter|softer|lower (?:the )?volume|"
                r"decrease (?:the )?volume|turn (?:it |the volume )?down)\b", t):
            return packet("volume down", "volume_down", {}, "")

        # --- window state ---
        m = re.search(r"\b(?:focus|switch to)(?: the)? window\s+(.+)$",
                      text, re.IGNORECASE)
        if m:
            return packet("focus window", "focus_window",
                          {"title": m.group(1).strip()}, "")
        m = re.search(r"\bclose(?: the)? (?:current |active )?window(?:\s+(.+))?$",
                      text, re.IGNORECASE)
        if m:
            params = {"title": m.group(1).strip()} if m.group(1) else {}
            return packet("close window", "close_window", params, "")
        for pattern, thought, intent_type in (
            (r"\bminimi[sz]e(?: the)?(?: window)?(?:\s+(.+))?$",
             "minimize window", "minimize_window"),
            (r"\bmaximi[sz]e(?: the)?(?: window)?(?:\s+(.+))?$",
             "maximize window", "maximize_window"),
            (r"\brestore(?: the)? window(?:\s+(.+))?$",
             "restore window", "restore_window"),
        ):
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                params = {"title": m.group(1).strip()} if m.group(1) else {}
                return packet(thought, intent_type, params, "")

        # --- workspace file management (all paths remain sandboxed) --------
        m = re.search(r"\b(?:append|add)\s+(.+?)\s+to(?: the)? file\s+(.+)$",
                      text, re.IGNORECASE)
        if m:
            return packet("append workspace file", "write_file", {
                "text": m.group(1).strip(), "path": m.group(2).strip(),
                "append": True}, "")
        m = re.search(r"\b(?:write|save)\s+(.+?)\s+to(?: the)? file\s+(.+)$",
                      text, re.IGNORECASE)
        if m:
            return packet("write workspace file", "write_file", {
                "text": m.group(1).strip(), "path": m.group(2).strip(),
                "append": False}, "")
        m = re.search(r"\bread(?: the)? file\s+(.+)$", text, re.IGNORECASE)
        if m:
            return packet("read workspace file", "read_file",
                          {"path": m.group(1).strip()}, "")
        m = re.search(r"\bcreate(?: a)? folder\s+(.+)$", text, re.IGNORECASE)
        if m:
            return packet("create workspace folder", "create_folder",
                          {"path": m.group(1).strip()}, "")
        m = re.search(r"\bdelete(?: the)? (?:file|folder|path)\s+(.+)$",
                      text, re.IGNORECASE)
        if m:
            return packet("delete workspace path", "delete_path",
                          {"path": m.group(1).strip()}, "")
        m = re.search(r"\bmove(?: the)? (?:file|folder)?\s*(.+?)\s+to\s+(.+)$",
                      text, re.IGNORECASE)
        if m:
            return packet("move workspace path", "move_path", {
                "source": m.group(1).strip(),
                "destination": m.group(2).strip()}, "")
        m = re.search(r"\bopen(?: the)? folder(?:\s+(.+))?$", text,
                      re.IGNORECASE)
        if m:
            return packet("open workspace folder", "open_folder",
                          {"path": (m.group(1) or "").strip()}, "")

        # --- validated website navigation ---------------------------------
        m = re.search(
            r"\b(?:open|go to|visit)\s+"
            r"(youtube|google|gmail|github|reddit|wikipedia|netflix|spotify|"
            r"amazon|(?:https?://)?[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?)"
            r"(?:\s+(?:on|in|with)\s+"
            r"(google chrome|chrome|microsoft edge|edge|firefox))?[.!]?$",
            text, re.IGNORECASE)
        if m:
            params = {"site": m.group(1).strip()}
            if m.group(2):
                params["browser"] = m.group(2).strip()
            return packet("open website", "open_website", params, "")

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
