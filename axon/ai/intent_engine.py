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
from datetime import datetime, timedelta

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

        def browser_parameters(options: str) -> dict | None:
            """Parse a constrained browser-option suffix without swallowing prose."""
            value = options.strip(" .!?-")
            params: dict = {}
            browser_match = re.search(
                r"\b(google chrome|chrome|microsoft edge|edge|firefox)\b",
                value, re.IGNORECASE)
            if browser_match:
                params["browser"] = browser_match.group(1).strip()
            if re.search(r"\b(?:incognito|private)\b", value, re.IGNORECASE):
                params["private"] = True
            residue = re.sub(
                r"\b(?:google chrome|chrome|microsoft edge|edge|firefox|"
                r"incognito|private|on|in|with|using|a|an|the|new|browser|"
                r"window|mode)\b",
                " ", value, flags=re.IGNORECASE)
            if residue.strip(" .!?-"):
                return None
            return params

        def duration_seconds(value: str) -> float | None:
            match = re.fullmatch(
                r"\s*(\d+(?:\.\d+)?)\s*"
                r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\s*",
                value, re.IGNORECASE)
            if not match:
                return None
            amount = float(match.group(1))
            unit = match.group(2).lower()
            multiplier = (86400 if unit.startswith("day") else
                          3600 if unit.startswith(("hour", "hr")) else
                          60 if unit.startswith(("minute", "min")) else 1)
            return amount * multiplier

        duration_pattern = (
            r"\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|"
            r"hours?|hrs?|days?)")

        # --- persistent timers and reminders ------------------------------
        if re.search(r"\b(?:list|show|what(?:'s| is| are)?)\b.*"
                     r"\b(?:timers?|reminders?)\b", t):
            return packet("list reminders", "list_reminders", {}, "")
        m = re.search(r"\b(?:cancel|remove|delete|stop)\s+(?:the\s+)?"
                      r"(?:timer|reminder)(?:\s+(?:called|named|id)?\s*(.*))?$",
                      text, re.IGNORECASE)
        if m:
            identifier = (m.group(1) or "").strip(" .!?")
            params = {"identifier": identifier} if identifier else {}
            return packet("cancel reminder", "cancel_reminder", params, "")
        if re.search(r"\b(?:set|start)(?:\s+a)?\b.*\btimer\b", t):
            dm = re.search(duration_pattern, text, re.IGNORECASE)
            if dm:
                seconds = duration_seconds(dm.group(0))
                tail = text[dm.end():].strip(" .!?")
                tail = re.sub(r"^timer\b\s*", "", tail,
                              flags=re.IGNORECASE).strip()
                tail = re.sub(r"^(?:called|named|for)\s+", "", tail,
                              flags=re.IGNORECASE).strip()
                params = {"seconds": seconds}
                if tail:
                    params["label"] = tail
                return packet("set timer", "set_timer", params, "")
        if re.search(r"\bremind me\b", t):
            dm = re.search(duration_pattern, text, re.IGNORECASE)
            if dm:
                seconds = duration_seconds(dm.group(0))
                tail = re.sub(r"^(?:to|about)\s+", "",
                              text[dm.end():].strip(" .!?"),
                              flags=re.IGNORECASE).strip()
                head = re.sub(r"^.*?\bremind me\b", "", text[:dm.start()],
                              flags=re.IGNORECASE).strip(" .!?")
                head = re.sub(r"^(?:to|about)\s+|\s+in$", "", head,
                              flags=re.IGNORECASE).strip()
                label = tail or head or "Reminder"
                return packet("set reminder", "set_reminder",
                              {"seconds": seconds, "label": label}, "")
            absolute = re.search(
                r"\bremind me\s+(tomorrow\s+)?at\s+(\d{1,2})"
                r"(?::(\d{2}))?\s*(am|pm)?\s+(?:to|about)\s+(.+)$",
                text, re.IGNORECASE)
            if absolute:
                now = datetime.now()
                hour = int(absolute.group(2))
                minute = int(absolute.group(3) or 0)
                meridiem = (absolute.group(4) or "").lower()
                if meridiem and 1 <= hour <= 12:
                    hour = hour % 12 + (12 if meridiem == "pm" else 0)
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    due = now.replace(hour=hour, minute=minute, second=0,
                                      microsecond=0)
                    if absolute.group(1) or due <= now:
                        due += timedelta(days=1)
                    return packet("set reminder", "set_reminder", {
                        "seconds": (due - now).total_seconds(),
                        "label": absolute.group(5).strip(" .!?")}, "")

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

        # --- system and desktop awareness ---
        if re.search(r"\b(network status|network connection|internet connection|"
                     r"am i (?:online|connected)|local ip|ip address)\b", t):
            return packet("network status", "network_status", {}, "")
        if re.search(r"\b(?:list|show|what|which)\b.*"
                     r"\b(?:apps|applications|programs|processes)\b.*"
                     r"\b(?:running|open)\b", t) or re.search(
                         r"\bwhat(?:'s| is)\s+(?:currently\s+)?running\b|"
                         r"\b(?:show|list)\s+(?:currently\s+)?(?:running|open)"
                         r"\s+(?:apps|applications|programs|processes)\b", t):
            return packet("list running apps", "list_running_apps", {}, "")
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

        # --- in-app web research and bounded page reading -----------------
        m = re.search(
            r"\b(?:read|summari[sz]e|extract)(?: this| the)? (?:webpage|page|url)"
            r"\s+(https?://\S+)", text, re.IGNORECASE)
        if m:
            return packet("read webpage", "read_webpage",
                          {"url": m.group(1).rstrip(".,!?")}, "")
        m = re.search(
            r"\b(?:research|investigate|find sources (?:for|about))\s+(.+)$",
            text, re.IGNORECASE)
        if m:
            return packet("in-app research", "research_web",
                          {"query": m.group(1).strip(" .!?")}, "")

        # --- explicit screen capture / local multimodal inspection ---------
        m = re.search(
            r"\b(?:inspect|analyse|analyze|read|describe) (?:my|the) screen"
            r"(?:\s+(?:for|and tell me|to find)\s+(.+))?$",
            text, re.IGNORECASE)
        if m:
            params = {"prompt": m.group(1).strip(" .!?")} if m.group(1) else {}
            return packet("inspect screen", "inspect_screen", params, "")
        if re.search(r"\b(?:what(?:'s| is) on (?:my|the) screen|"
                     r"inspect (?:my|the) screen|read (?:my|the) screen|"
                     r"describe (?:my|the) screen)\b", t):
            return packet("inspect screen", "inspect_screen", {}, "")
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
        # Durable workflow controls precede media's generic "resume" verb.
        if re.search(r"\b(?:list|show|what are) (?:my )?(?:recoverable |interrupted )?workflows\b", t):
            return packet("list recoverable workflows", "list_workflows", {}, "")
        m = re.search(r"\b(?:resume|continue) (?:the )?(?:(last|latest) )?"
                      r"(?:workflow|plan)(?:\s+([a-f0-9]{12}|last|latest))?\b", t)
        if m:
            return packet("resume workflow", "resume_workflow",
                          {"identifier": m.group(1) or m.group(2) or "latest"}, "")
        m = re.search(r"\b(?:cancel|discard) (?:the )?(?:(last|latest) )?"
                      r"(?:workflow|plan)(?:\s+([a-f0-9]{12}|last|latest))?\b", t)
        if m:
            return packet("cancel workflow", "cancel_workflow",
                          {"identifier": m.group(1) or m.group(2) or "latest"}, "")

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
        if re.search(r"\b(?:what|which)\b.*\b(?:active|current|foreground)\b.*"
                     r"\b(?:window|app|application)\b", t) or re.search(
                         r"\bwhat (?:app|application) am i (?:using|in)\b", t):
            return packet("active window", "get_active_window", {}, "")
        if re.search(r"\b(?:list|show|what|which)\b.*\b(?:open|visible)\b.*"
                     r"\bwindows?\b", t):
            return packet("list windows", "list_windows", {}, "")
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

        # --- browser search / private browser windows ----------------------
        m = re.search(
            r"\b(?:open|navigate|browse|go to)\s+(https?://\S+|"
            r"[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?)\s+(?:in|with|using)\s+"
            r"(?:the\s+)?(?:managed|automation|playwright) browser\b",
            text, re.IGNORECASE)
        if m:
            return packet("managed browser navigation", "browser_navigate",
                          {"url": m.group(1).rstrip(".,!?")}, "")
        if re.search(r"\b(?:read|summari[sz]e|inspect)\s+"
                     r"(?:the current|this|the|current)\s+"
                     r"(?:managed\s+)?(?:page|webpage)\b", t):
            return packet("read managed page", "browser_read_page", {}, "")
        m = re.search(r"\bclick\s+(.+?)(?:\s+in (?:the )?managed browser)?[.!]?$",
                      text, re.IGNORECASE)
        if m:
            return packet("managed browser click", "browser_click",
                          {"target": m.group(1).strip(" .!?")}, "")
        m = re.search(
            r"\bfill\s+(?:the\s+)?(.+?)(?:\s+field)?\s+with\s+(.+?)"
            r"(?:\s+in (?:the )?managed browser)?[.!]?$",
            text, re.IGNORECASE)
        if m:
            return packet("managed browser fill", "browser_fill", {
                "field": m.group(1).strip(), "text": m.group(2).strip()}, "")
        if re.search(r"\bclose (?:the )?(?:managed|automation|playwright) browser\b",
                     t):
            return packet("close managed browser", "browser_close_managed", {}, "")

        browser_actions = (
            (r"\b(?:open|create)(?: a)? new tab\b", "new_tab"),
            (r"\bclose(?: the| this)? tab\b", "close_tab"),
            (r"\b(?:reopen|restore)(?: the)? (?:last |closed )?tab\b",
             "reopen_tab"),
            (r"\b(?:reload|refresh)(?: the| this)? (?:page|tab)?\b", "reload"),
            (r"\b(?:browser )?(?:go )?back\b", "back"),
            (r"\b(?:browser )?(?:go )?forward\b", "forward"),
            (r"\b(?:open|show)(?: browser)? downloads\b", "downloads"),
            (r"\b(?:open|show)(?: browser)? history\b", "history"),
            (r"\bfind on (?:the |this )?page\b", "find"),
        )
        for pattern, action in browser_actions:
            if re.search(pattern, t):
                return packet("browser control", "browser_action",
                              {"action": action}, "")
        m = re.search(
            r"^\s*(?:open|launch|start)\s+(.+?)\s*[.!]?$",
            text, re.IGNORECASE)
        if m and re.search(
                r"\b(?:browser|chrome|edge|firefox|incognito|private)\b",
                m.group(1), re.IGNORECASE):
            params = browser_parameters(m.group(1))
            if params is not None:
                return packet("open browser", "open_browser", params, "")

        m = re.search(
            r"^\s*(?:search(?:\s+google)?|google)(?:\s+for)?\s+(.+?)\s*[.!]?$",
            text, re.IGNORECASE)
        if m:
            search_value = m.group(1).strip()
            option_match = re.search(
                r"\s+(?:in|on|with|using)\s+(.+?)\s*$",
                search_value, re.IGNORECASE)
            if option_match:
                params = browser_parameters(option_match.group(1))
                if params and ("browser" in params or params.get("private")):
                    query = search_value[:option_match.start()].strip()
                    if query:
                        params["query"] = query
                        return packet("search in browser", "search_browser",
                                      params, "")

        # --- validated website navigation ---------------------------------
        m = re.search(
            r"\b(?:open|go to|visit)\s+"
            r"(youtube|google|gmail|github|reddit|wikipedia|netflix|spotify|"
            r"amazon|(?:https?://)?[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?)"
            r"(.*)$",
            text, re.IGNORECASE)
        if m:
            params = {"site": m.group(1).strip()}
            options = browser_parameters(m.group(2))
            if options is not None:
                params.update(options)
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
