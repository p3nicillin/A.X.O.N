"""AXON entrypoint — constructs and wires every layer, then runs.

    python -m axon        (from the repo root)
or  python run.py

The visual frontend is pluggable (config.ui_backend "auto"|"web"|"qt"|"tk"):
"auto" prefers the AXON web UI (pywebview), then the PySide6 GLSL core, then the
pure-Tkinter HUD. Everything below the window — the bus, orchestrator, audio,
STT, TTS — is identical either way.
"""
from __future__ import annotations

from .audio.tts import TtsEngine
from .config import DATA_DIR, Config
from .core.event_bus import Event, EventBus
from .core.orchestrator import Orchestrator
from .enterprise.audit import AuditLogger
from .perception.audio_input import AudioInput
from .perception.stt import SttEngine
from .skills.registry import SkillRegistry


def _have_web() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def _have_qt() -> bool:
    try:
        import PySide6  # noqa: F401
        from PySide6.QtOpenGLWidgets import QOpenGLWidget  # noqa: F401
        return True
    except Exception:
        return False


def select_backend(config: Config) -> str:
    """Resolve config.ui_backend to a concrete frontend.

    "auto" prefers the AXON web UI (pywebview) → PySide6 GLSL core → Tkinter.
    An explicit choice falls back gracefully (with a notice) if unavailable.
    """
    pref = (config.ui_backend or "auto").lower()
    if pref == "tk":
        return "tk"
    if pref == "web" and not _have_web():
        print("[ui] pywebview unavailable — falling back.")
    if pref == "qt" and not _have_qt():
        print("[ui] PySide6 unavailable — falling back.")
    # honour an available explicit pref, else pick best available in order
    if pref == "web" and _have_web():
        return "web"
    if pref == "qt" and _have_qt():
        return "qt"
    if _have_web():
        return "web"
    if _have_qt():
        return "qt"
    return "tk"


def banner(config: Config, registry: SkillRegistry, stt: SttEngine,
           audio: AudioInput, tts: TtsEngine, backend: str,
           ai_health: dict | None = None) -> None:
    print("=" * 58)
    print("  A.X.O.N  —  voice-driven AI operating layer")
    print("=" * 58)
    ai_health = ai_health or {"active": "rules", "backends": {}}
    active = ai_health.get("active", "rules")
    if active == "local":
        info = ai_health.get("backends", {}).get("local", {})
        detail = info.get("detail", "")
        ai_line = (f"local LLM ({info.get('model', '?')})"
                   + (f" — {detail}" if detail else ""))
        privacy = "LOCAL — transcripts stay on this device"
    elif active == "cloud":
        info = ai_health.get("backends", {}).get("cloud", {})
        ai_line = f"cloud ({info.get('model', 'claude')})"
        privacy = "CLOUD — utterances leave the device (audited)"
    else:
        ai_line = "local (rule-based, no LLM runtime)"
        privacy = "LOCAL — transcripts stay on this device"
    print(f"  AI core       : {ai_line}")
    print(f"  privacy       : {privacy}")
    print(f"  fallback chain: {' -> '.join((ai_health.get('chain') or []) + ['rules'])}")
    print(f"  microphone    : {'ready' if audio.available else 'unavailable'}")
    stt_status = ("loading in background…" if stt.can_load()
                  else "off — " + stt.reason)
    print(f"  speech-to-text: {stt_status}")
    print(f"  wake spotter  : {'on (grammar-biased)' if config.use_wake_spotter else 'off'}")
    print(f"  text-to-speech: {'ready' if tts.available else 'simulated'} "
          f"(voice: {config.tts_voice or 'system default'})")
    print(f"  wake word     : {'REQUIRED — say “AXON …”' if config.require_wake_word else 'off'}")
    renderer = {"web": "AXON web UI (pywebview)",
                "qt": "PySide6 + GLSL bloom",
                "tk": "Tkinter HUD"}.get(backend, backend)
    print(f"  renderer      : {renderer}")
    print(f"  autonomy      : {'on (suggestion-only)' if config.autonomy_enabled else 'off'}")
    print(f"  audit trail   : {'on' if config.audit_enabled else 'off'}  (data/logs/)")
    print(f"  skills loaded : {', '.join(m.name for m in registry.catalogue())}")
    print("-" * 58)
    print("  Say “AXON” then your command, or type in DEV INPUT + Enter.")
    print("  Esc = interrupt speech.  F2 = hide dev input.")
    print("=" * 58)


def main() -> None:
    config = Config.load()
    bus = EventBus()

    # enterprise audit + structured logging attaches first so it captures every
    # subsequent event from startup onward.
    audit = AuditLogger(config, bus)

    registry = SkillRegistry(config.disabled_skills).discover()
    tts = TtsEngine(config, bus)
    stt = SttEngine(config)
    audio = AudioInput(config, bus, stt)
    orchestrator = Orchestrator(config, bus, registry, tts, audio)

    # §16 autonomy is opt-in (it observes the system). Construct it here so it
    # can share the orchestrator's user model + memory, but start it with the
    # other subsystems once the window exists.
    autonomy = None
    if config.autonomy_enabled:
        from .autonomy import AutonomyEngine, ContextSensor, TaskScheduler
        autonomy = AutonomyEngine(
            config, bus, sensor=ContextSensor(),
            scheduler=TaskScheduler(DATA_DIR / "tasks.json"),
            user_model=orchestrator.user_model, memory=orchestrator.memory)

    backend = select_backend(config)

    # §9 first-run check: nudge the user to set up a local LLM if the chosen
    # engine wants one but none is reachable. Never blocks; never auto-installs.
    from .ai.setup import first_run_check
    first_run_check(config, orchestrator.ai)

    ai_health = orchestrator.ai.health()
    banner(config, registry, stt, audio, tts, backend, ai_health)

    diagnostic = {
        "ai_core": ai_health.get("active"),
        "ai_chain": (ai_health.get("chain") or []) + ["rules"],
        "ai_model": ai_health.get("backends", {})
                    .get(ai_health.get("active", ""), {}).get("model", "rule-based"),
        "privacy": "local" if ai_health.get("active") != "cloud" else "cloud",
        "microphone": audio.available,
        "speech_to_text": "loading" if stt.can_load() else False,
        "text_to_speech": tts.available,
        "wake_word_required": config.require_wake_word,
        "renderer": backend,
        "tts_voice": config.tts_voice or "system default",
        "skills": [m.name for m in registry.catalogue()],
        "session": audit.session_id,
    }
    bus.publish(Event.LOG, {"level": "info", "source": "diagnostic",
                            "message": str(diagnostic)})

    def shutdown() -> None:
        if autonomy is not None:
            autonomy.stop()
        audio.stop()
        tts.stop()

    def on_stt_ready(ok: bool, reason: str) -> None:
        if ok:
            bus.publish(Event.LOG, {"level": "info", "source": "stt",
                "message": "Speech recognition online."})
        else:
            bus.publish(Event.LOG, {"level": "warn", "source": "stt",
                "message": f"Speech recognition unavailable: {reason}"})

    def boot_subsystems() -> None:
        """Spin up audio + speech once the window exists. The STT model is
        large, so load it in the background — the window appears instantly."""
        tts.start()
        audio.start()
        if stt.can_load():
            bus.publish(Event.LOG, {"level": "info", "source": "stt",
                "message": "Loading speech model in the background…"})
            stt.load_async(on_stt_ready)
        # §5 warm the local LLM so the first command doesn't pay cold-load cost.
        if config.ai.warm_on_start:
            import threading as _t
            _t.Thread(target=orchestrator.ai.warm, daemon=True).start()
        if autonomy is not None:
            autonomy.start()
        bus.publish(Event.LOG, {"level": "info", "source": "core",
                                "message": "AXON online."})

    if backend == "web":
        _run_web(config, bus, orchestrator, boot_subsystems, shutdown)
    elif backend == "qt":
        _run_qt(config, bus, orchestrator, boot_subsystems, shutdown)
    else:
        _run_tk(config, bus, orchestrator, boot_subsystems, shutdown)


def _run_web(config, bus, orchestrator, boot_subsystems, shutdown) -> None:
    from .visual.web_window import AxonWebWindow

    window = AxonWebWindow(config, bus, orchestrator)
    window.set_on_close(shutdown)
    try:
        window.run(boot_subsystems)   # blocks until the window closes
    finally:
        shutdown()


def _run_qt(config, bus, orchestrator, boot_subsystems, shutdown) -> None:
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from .visual.qt_window import AxonQtWindow

    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication.instance() or QApplication([])
    window = AxonQtWindow(config, bus, orchestrator)
    window.set_on_close(shutdown)
    window.show()
    window.start()
    boot_subsystems()
    try:
        app.exec()
    finally:
        shutdown()


def _run_tk(config, bus, orchestrator, boot_subsystems, shutdown) -> None:
    import tkinter as tk

    from .visual.main_window import AxonWindow

    root = tk.Tk()
    window = AxonWindow(root, config, bus, orchestrator)
    window.set_on_close(shutdown)
    boot_subsystems()
    window.start()
    try:
        root.mainloop()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
