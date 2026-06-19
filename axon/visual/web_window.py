"""The AXON web frontend: an HTML/CSS/JS interface hosted in a native WebView2
window via pywebview, wired to the same EventBus + Orchestrator as the other
frontends.

Architecture mirrors the Qt/Tk windows so it's a drop-in backend:

    JS  -> Python   the page calls window.pywebview.api.{command,
                    toggle_listening, on_ready}; those are methods of :class:`Bridge`.
    Python -> JS    bus events are translated into window.AXON.* calls
                    (reply / thinking / setListening / setTelemetry / addMemory …)
                    via window.evaluate_js.

Bus events arrive on worker threads. ``evaluate_js`` is safe to call from any
thread (pywebview marshals it to the UI thread); high-frequency amplitude
updates are throttled so we don't flood the bridge.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

import webview

from .. import __version__
from ..config import DATA_DIR
from ..core.event_bus import Event, EventBus
from ..core.states import AxonState
from ..skills.app_launcher import handler as app_launcher
from ..skills.file_system import handler as file_system
from ..skills.system_info import handler as sysinfo

_UI_FILE = Path(__file__).resolve().parent / "web" / "axon_ui.html"

# pywebview's edgechromium backend logs noisy (non-fatal) errors while trying to
# introspect WebView2 native properties off the UI thread. Mute them.
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

# AxonState -> (AXON status text, voice-chip text, is-listening)
_STATUS = {
    AxonState.IDLE:      ("ONLINE", "IDLE", False),
    AxonState.LISTENING: ("LISTENING", "LIVE", True),
    AxonState.THINKING:  ("PROCESSING", "BUSY", False),
    AxonState.SPEAKING:  ("SPEAKING", "VOICE", False),
    AxonState.ERROR:     ("ALERT", "ERR", False),
}


class Bridge:
    """pywebview js_api object + the Python->UI control surface."""

    def __init__(self, config, bus: EventBus, orchestrator) -> None:
        self.config = config
        self.bus = bus
        self.orch = orchestrator
        self.window = None
        self._listening = False
        self._last_amp = 0.0
        self._amp_at = 0.0
        self._started_at = time.time()
        self._last_latency_ms = 0.0
        self._logs = deque(maxlen=200)
        self._commands = deque(maxlen=200)
        # net counters for throughput readout
        self._net = None
        # the page signals readiness via on_ready(); until then evaluate_js is
        # premature (DOM not built) and WebView2 spams off-thread COM errors.
        self.ready = threading.Event()

    # ====================  JS -> Python  ====================
    def on_ready(self) -> bool:
        """Fired once the page's boot sequence completes."""
        self.ready.set()
        data = self.panel_snapshot()
        enabled = sum(skill["enabled"] for skill in data["skills"])
        status = data["status"]
        self.set_greeting(
            f"{status['model']} via {status['backend']} is active. "
            f"{enabled} of {len(data['skills'])} skills enabled.")
        self.set_panel_data(data)
        return True

    def command(self, text: str) -> bool:
        """A typed command from the AXON input box. Bypasses the wake word
        (the same contract as the Qt/Tk dev inputs)."""
        text = (text or "").strip()
        if text:
            self.orch.submit_text(text, bypass_wake=True)
        return True

    def toggle_listening(self) -> bool:
        """Mic button: enable/disable live capture and reflect it in the UI."""
        self._listening = not self._listening
        audio = getattr(self.orch, "audio_input", None)
        if audio is not None:
            try:
                audio.set_enabled(self._listening)
            except Exception:
                pass
        self.set_listening(self._listening)
        return self._listening

    def set_skill_enabled(self, name: str, enabled: bool) -> dict:
        ok = self.orch.registry.set_enabled(name, bool(enabled))
        if ok:
            self.config.disabled_skills = self.orch.registry.disabled_skills()
            self.bus.publish(Event.LOG, {
                "level": "info",
                "source": "ui",
                "message": f"skill {name} {'enabled' if enabled else 'disabled'}",
            })
            self.set_panel_data(self.panel_snapshot())
        return {"ok": ok, "disabled_skills": list(self.config.disabled_skills)}

    # ====================  Python -> JS  ====================
    def js(self, code: str) -> None:
        # hold every Python->JS call until the page has booted (on_ready),
        # otherwise evaluate_js runs against an unbuilt DOM and WebView2 errors.
        if self.window is None or not self.ready.is_set():
            return
        try:
            self.window.evaluate_js(code)
        except Exception:
            pass  # window may be mid-teardown

    def reply(self, text: str) -> None:
        self.js(f"window.AXON.reply({json.dumps(text)})")

    def user_said(self, text: str) -> None:
        self.js(f"window.AXON.addMessage('You', {json.dumps(text)}, 'me')")

    def thinking(self) -> None:
        self.js("window.AXON.thinking()")

    def stop_thinking(self) -> None:
        self.js("window.AXON.stopThinking()")

    def push_thought(self, text: str) -> None:
        self.js(f"window.AXON.pushThought({json.dumps(text)})")

    def add_memory(self, text: str) -> None:
        self.js(f"window.AXON.addMemory({json.dumps(text)})")

    def set_agents(self, agents: list[dict]) -> None:
        self.js(f"window.AXON.setAgents({json.dumps(agents)})")

    def set_listening(self, on: bool) -> None:
        self.js(f"window.AXON.setListening({json.dumps(bool(on))})")

    def set_amplitude(self, level: float) -> None:
        self.js(f"window.AXON.setAmplitude({float(level):.3f})")

    def set_status(self, text: str) -> None:
        self.js(f"window.AXON.setStatus({json.dumps(text)})")

    def set_voice_chip(self, text: str) -> None:
        self.js(f"window.AXON.setVoiceChip({json.dumps(text)})")

    def set_greeting(self, text: str) -> None:
        self.js(f"window.AXON.setGreeting({json.dumps(text)})")

    def set_telemetry(self, data: dict) -> None:
        self.js(f"window.AXON.setTelemetry({json.dumps(data)})")

    def set_panel_data(self, data: dict) -> None:
        self.js(f"window.AXON.setPanelData({json.dumps(data)})")

    # ====================  bus -> UI bridge  ====================
    def on_event(self, msg) -> None:
        ev = msg.event
        if ev == Event.STATE_CHANGED:
            self._on_state(msg.payload)
        elif ev == Event.TRANSCRIPT:
            self._on_transcript(msg.payload or {})
        elif ev == Event.SPEAK_START:
            text = (msg.payload or {}).get("text", "")
            if text:
                self.reply(text)
        elif ev in (Event.SPEAK_LEVEL, Event.AUDIO_LEVEL):
            self._on_amplitude(float(msg.payload or 0.0))
        elif ev == Event.INTENT:
            self._on_intent(msg.payload)
            self.set_panel_data(self.panel_snapshot())
        elif ev == Event.SKILL_RESULT:
            self.set_panel_data(self.panel_snapshot())
        elif ev == Event.COMMAND_LOG:
            self._commands.append(msg.payload or {})
            self.set_panel_data(self.panel_snapshot())
        elif ev == Event.SUGGESTION:
            self._on_suggestion(msg.payload or {})
        elif ev == Event.LOG:
            self._on_log(msg.payload or {})

    def _on_state(self, state: AxonState) -> None:
        status, voice, listening = _STATUS.get(
            state, ("ONLINE", "IDLE", False))
        self.set_status(status)
        self.set_voice_chip(voice)
        self.set_listening(listening)
        if state == AxonState.THINKING:
            self.thinking()
        elif state in (AxonState.IDLE, AxonState.ERROR):
            self.stop_thinking()

    def _on_transcript(self, payload: dict) -> None:
        # show the user's spoken command (typed commands echo themselves in JS)
        text = payload.get("text", "").strip()
        if text and payload.get("wake_satisfied") and not payload.get("wake_only"):
            self.user_said(self.orch.wake.clean_spotter_command(text))

    def _on_amplitude(self, level: float) -> None:
        # throttle to ~25 Hz; evaluate_js per audio frame would saturate the bridge
        now = time.monotonic()
        if now - self._amp_at < 0.04 and abs(level - self._last_amp) < 0.05:
            return
        self._amp_at = now
        self._last_amp = level
        self.set_amplitude(level)

    def _on_intent(self, packet) -> None:
        self._last_latency_ms = float(getattr(packet, "latency_ms", 0.0) or 0.0)
        thought = getattr(packet, "thought", "")
        if thought:
            self.push_thought(thought)

    def _on_suggestion(self, payload: dict) -> None:
        # §16.3 proactive advice — shown as an AXON message, never auto-executed.
        text = payload.get("text", "")
        if text:
            self.js(f"window.AXON.addMessage('AXON', {json.dumps('💡 ' + text)}, 'ai')")

    def _on_log(self, payload: dict) -> None:
        self._logs.append({
            "level": payload.get("level", "info"),
            "source": payload.get("source", "core"),
            "message": payload.get("message", ""),
            "ts": time.strftime("%H:%M:%S"),
        })
        self.set_panel_data(self.panel_snapshot())

    # ====================  telemetry  ====================
    def _agents(self, ai_health: dict | None = None) -> list[dict]:
        audio = getattr(self.orch, "audio_input", None)
        stt = getattr(audio, "stt", None)
        memory = getattr(self.orch, "memory", None)
        ai_health = ai_health or self.orch.ai.health()
        skills = self.orch.registry.catalogue()
        enabled = sum(self.orch.registry.is_enabled(m.name) for m in skills)
        return [
            {"name": "Microphone", "status": "active" if bool(getattr(audio, "_running", False)) else "offline"},
            {"name": "Speech recognition", "status": "active" if bool(getattr(stt, "available", False)) else ("loading" if bool(getattr(stt, "can_load", lambda: False)()) else "offline")},
            {"name": "Memory store", "status": "active" if memory is not None else "disabled"},
            {"name": f"Intent engine ({ai_health.get('active', 'rules')})", "status": "active"},
            {"name": f"Skills ({enabled}/{len(skills)})", "status": "active" if enabled else "disabled"},
            {"name": "Audit trail", "status": "active" if self.config.audit_enabled else "disabled"},
            {"name": "Autonomy", "status": "active" if getattr(self.orch, "autonomy", None) is not None else "disabled"},
        ]

    def snapshot(self) -> dict:
        """Map the project's read-only metrics onto AXON's telemetry fields."""
        m = sysinfo.read_metrics()
        cpu = m.get("cpu") or 0.0
        mem = m.get("memory") or 0.0
        disk = m.get("disk") or 0.0
        up = down = 0.0
        try:
            import psutil
            now = psutil.net_io_counters()
            t = time.time()
            if self._net is not None:
                dt = max(0.001, t - self._net[2])
                up = max(0.0, (now.bytes_sent - self._net[0]) * 8 / 1e6 / dt)
                down = max(0.0, (now.bytes_recv - self._net[1]) * 8 / 1e6 / dt)
            self._net = (now.bytes_sent, now.bytes_recv, t)
        except Exception:
            pass
        return {
            "cpu": cpu, "mem": mem,
            "disk": disk, "battery": m.get("battery"),
            "up": up, "down": down,
            "requests": getattr(getattr(self.orch.ai, "metrics", None), "total", 0),
            "latency": self._last_latency_ms,
            "uptime_seconds": max(0, int(time.time() - self._started_at)),
        }

    def panel_snapshot(self) -> dict:
        ai_health = self.orch.ai.health()
        ai_metrics = getattr(getattr(self.orch, "ai", None), "metrics", None)
        metrics = ai_metrics.snapshot() if ai_metrics is not None else {}
        memory_store = getattr(self.orch, "memory", None)
        memories = []
        if memory_store is not None:
            try:
                memories = [entry.as_dict() for entry in memory_store.all_entries()]
                memories.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
            except Exception:
                memories = []
        active = ai_health.get("active", "rules")
        active_info = ai_health.get("backends", {}).get(active, {})
        state = getattr(self.orch, "state", AxonState.IDLE)
        skills = []
        for skill in self.orch.registry.skills:
            m = skill.manifest
            settings = {}
            if m.name == "AppLauncherSkill":
                settings["whitelist"] = sorted(set(app_launcher.ALIASES))
            elif m.name == "FileSystemSkill":
                settings["sandbox_root"] = str(file_system.WORKSPACE)
            skills.append({
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "intents": list(m.intents),
                "parameters": {k: m.params_for(k) for k in m.intents},
                "sensitive": bool(m.sensitive),
                "enabled": self.orch.registry.is_enabled(m.name),
                "settings": settings,
            })
        return {
            "status": {
                "backend": active,
                "model": active_info.get("model", "rule-based"),
                "chain": (ai_health.get("chain") or []) + ["rules"],
                "wake_word": self.config.wake_word,
                "wake_required": self.config.require_wake_word,
                "mic": bool(getattr(getattr(self.orch, "audio_input", None), "available", False)),
                "state": getattr(state, "value", str(state)),
                "session": getattr(self.orch, "audit_session_id", ""),
                "version": __version__,
            },
            "memory": memories,
            "agents": self._agents(ai_health),
            "skills": skills,
            "ai": {"health": ai_health, "metrics": metrics},
            "audit": {
                "logs": list(self._logs)[-80:],
                "commands": list(self._commands)[-80:],
            },
            "voice": {
                "tts_backend": getattr(self.orch.tts, "backend_name", "unavailable"),
                "voice": getattr(self.orch.tts, "selected_voice", self.config.tts_voice or "system default"),
                "rate": self.config.tts_rate,
                "stt": "online" if bool(getattr(getattr(self.orch.audio_input, "stt", None), "available", False)) else "offline",
                "stt_model": Path(getattr(getattr(self.orch.audio_input, "stt", None), "_cmd_path", "") or "").name or "none",
                "wake_ack": self.config.wake_ack_phrase,
                "persona": "AXON",
                "address_term": self.config.address_term,
            },
            "diagnostics": {
                "data_dir": str(DATA_DIR),
                "logs_dir": str(DATA_DIR / "logs"),
                "renderer": "web",
                "audit_enabled": self.config.audit_enabled,
                "disabled_skills": list(self.config.disabled_skills),
                "memory_enabled": self.config.memory_enabled,
                "memory_entries": len(memories),
                "planning_enabled": self.config.planning_enabled,
                "critic_enabled": self.config.critic_enabled,
                "confirm_sensitive": self.config.confirm_sensitive,
            },
        }

    def telemetry_loop(self, stop: threading.Event) -> None:
        self.ready.wait(timeout=15)       # don't push telemetry before the DOM
        while not stop.is_set():
            self.set_telemetry(self.snapshot())
            stop.wait(1.3)


class _Api:
    """The object pywebview exposes to JS as ``window.pywebview.api``.

    Deliberately minimal: it holds only the three inbound methods the page
    calls. The controller is stored under an underscore name so pywebview does
    NOT try to serialise it — exposing the window/orchestrator/bus here makes
    pywebview recurse into the .NET WebView2 object graph and spam errors.
    """

    def __init__(self, bridge: "Bridge") -> None:
        self._bridge = bridge

    def on_ready(self) -> bool:
        return self._bridge.on_ready()

    def command(self, text: str) -> bool:
        return self._bridge.command(text)

    def toggle_listening(self) -> bool:
        return self._bridge.toggle_listening()

    def get_panel_data(self) -> dict:
        return self._bridge.panel_snapshot()

    def set_skill_enabled(self, name: str, enabled: bool) -> dict:
        return self._bridge.set_skill_enabled(name, enabled)


class AxonWebWindow:
    """Public surface mirrors AxonQtWindow/AxonWindow: construct, set_on_close,
    then run (blocking) with a boot callback."""

    def __init__(self, config, bus: EventBus, orchestrator) -> None:
        self.config = config
        self.bus = bus
        self.orch = orchestrator
        self.bridge = Bridge(config, bus, orchestrator)
        self._api = _Api(self.bridge)
        self._on_close_cb = None
        self._boot = None
        self._stop = threading.Event()
        bus.subscribe_all(self.bridge.on_event)

    def set_on_close(self, cb) -> None:
        self._on_close_cb = cb

    def run(self, boot_subsystems=None) -> None:
        """Create the window and enter the (blocking) GUI loop."""
        self._boot = boot_subsystems
        self.bridge.window = webview.create_window(
            "A.X.O.N — AXON",
            url=str(_UI_FILE),
            js_api=self._api,
            width=self.config.window_width + 220,
            height=self.config.window_height + 100,
            min_size=(960, 620),
            background_color="#04030b",
        )
        self.bridge.window.events.closed += self._on_closed
        # webview.start runs `func` on a worker thread once the GUI is up, so
        # the window exists before we boot the (event-publishing) subsystems.
        webview.start(self._after_start, debug=False)

    def _after_start(self) -> None:
        threading.Thread(target=self.bridge.telemetry_loop, args=(self._stop,),
                         daemon=True).start()
        if self._boot is not None:
            self._boot()

    def _on_closed(self) -> None:
        self._stop.set()
        if self._on_close_cb:
            self._on_close_cb()
