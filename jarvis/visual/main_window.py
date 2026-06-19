"""The JARVIS window: HUD + the holographic core, on the Tkinter main thread.

Threading model: every other layer runs off-thread and publishes to the
EventBus. Those callbacks fire on worker threads, so we cannot touch Tk from
them. Instead each event is pushed onto a thread-safe queue that the frame loop
drains on the UI thread — the standard safe pattern for Tk.

There is no chat window. The only text affordance is a small, clearly-labelled
DEV INPUT line so the system can be driven/tested when no microphone or STT
model is present. Hide it with F2.
"""
from __future__ import annotations

import queue
import tkinter as tk

from ..core.event_bus import Event, EventBus
from ..core.states import JarvisState
from ..skills.system_info import handler as sysinfo
from .core_widget import CoreRenderer

_ACCENT = "#39c6ff"
_DIM = "#1b5e80"
_OK = "#42ff9e"
_WARN = "#ffb454"
_ERR = "#ff5a5a"
_BG = "#03070f"
_FONT = "Consolas"


class JarvisWindow:
    def __init__(self, root: tk.Tk, config, bus: EventBus, orchestrator) -> None:
        self.root = root
        self.config = config
        self.bus = bus
        self.orch = orchestrator
        self._q: "queue.Queue" = queue.Queue()
        self._log_lines: list[tuple[str, str]] = []
        self._metric_tick = 0
        self._frame_ms = max(16, int(1000 / config.target_fps))

        root.title("J.A.R.V.I.S")
        root.configure(bg=_BG)
        root.geometry(f"{config.window_width}x{config.window_height}")
        root.minsize(820, 560)

        self.canvas = tk.Canvas(root, bg=_BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.renderer = CoreRenderer(self.canvas, config)

        self._build_hud()
        self._build_dev_input()

        # marshal every event onto the UI queue
        bus.subscribe_all(lambda m: self._q.put(m))

        root.bind("<Configure>", self._on_resize)
        root.bind("<Escape>", lambda e: self.orch.tts.stop())   # barge-in
        root.bind("<F2>", lambda e: self._toggle_dev())
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._on_close_cb = None

    def set_on_close(self, cb) -> None:
        self._on_close_cb = cb

    # -- HUD construction ----------------------------------------------------
    def _build_hud(self) -> None:
        cv = self.canvas
        self._hud = {}
        # title
        self._hud["title"] = cv.create_text(34, 34, anchor="w", text="JARVIS",
            fill=_ACCENT, font=(_FONT, 30, "bold"))
        cv.create_text(34, 70, anchor="w", text="SYSTEM STATUS", fill=_DIM,
            font=(_FONT, 9))
        self._hud["status"] = cv.create_text(34, 88, anchor="w", text="ONLINE",
            fill=_OK, font=(_FONT, 12, "bold"))

        # gauges
        self._gauges = {}
        labels = [("CPU", "cpu"), ("MEMORY", "memory"),
                  ("DISK", "disk"), ("ENERGY", "battery")]
        y = 124
        for label, key in labels:
            cv.create_text(34, y, anchor="w", text=label, fill=_DIM, font=(_FONT, 8))
            track = cv.create_rectangle(34, y + 14, 230, y + 22, outline="#0c2738",
                                        fill="#06141f")
            fill = cv.create_rectangle(34, y + 14, 34, y + 22, outline="", fill=_ACCENT)
            self._gauges[key] = (track, fill)
            y += 44

        # right-side activity indicators
        self._indicators = {}
        states = [("LISTENING", JarvisState.LISTENING),
                  ("PROCESSING", JarvisState.THINKING),
                  ("SPEAKING", JarvisState.SPEAKING)]
        ry = 60
        for label, st in states:
            tid = cv.create_text(0, ry, anchor="e", text=label + "...",
                                 fill=_DIM, font=(_FONT, 13, "bold"))
            self._indicators[st] = tid
            ry += 40

        # rolling log (bottom-left)
        self._log_id = cv.create_text(34, 0, anchor="sw", text="", fill="#5fa8c9",
                                      font=(_FONT, 9), justify="left")
        self._reposition_hud()

    def _build_dev_input(self) -> None:
        self.dev_visible = True
        self.dev_var = tk.StringVar()
        self.dev_entry = tk.Entry(self.root, textvariable=self.dev_var,
            bg="#06141f", fg=_ACCENT, insertbackground=_ACCENT,
            font=(_FONT, 11), relief="flat", highlightthickness=1,
            highlightbackground=_DIM, highlightcolor=_ACCENT)
        self.dev_entry.bind("<Return>", self._submit_dev)
        self._place_dev()

    def _place_dev(self) -> None:
        if self.dev_visible:
            self.dev_entry.place(relx=0.5, rely=0.97, anchor="center",
                                 relwidth=0.5)
        else:
            self.dev_entry.place_forget()

    def _toggle_dev(self) -> None:
        self.dev_visible = not self.dev_visible
        self._place_dev()

    def _submit_dev(self, _e) -> None:
        text = self.dev_var.get().strip()
        self.dev_var.set("")
        if text:
            self.orch.submit_text(text)

    # -- layout --------------------------------------------------------------
    def _on_resize(self, event) -> None:
        if event.widget is self.root:
            self.renderer.resize(self.canvas.winfo_width(),
                                 self.canvas.winfo_height())
            self._reposition_hud()

    def _reposition_hud(self) -> None:
        w = self.canvas.winfo_width() or self.config.window_width
        h = self.canvas.winfo_height() or self.config.window_height
        cv = self.canvas
        for st, tid in self._indicators.items():
            cv.coords(tid, w - 34, cv.coords(tid)[1])
        cv.coords(self._log_id, 34, h - 26)

    # -- frame loop ----------------------------------------------------------
    def start(self) -> None:
        self.dev_entry.focus_set()
        self._frame()

    def _frame(self) -> None:
        self._drain_events()
        self.renderer.step()
        self._metric_tick += 1
        if self._metric_tick % 30 == 0:        # ~ once per second
            self._update_gauges()
        self.root.after(self._frame_ms, self._frame)

    def _drain_events(self) -> None:
        try:
            while True:
                msg = self._q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass

    def _handle(self, msg) -> None:
        ev = msg.event
        if ev == Event.STATE_CHANGED:
            self._set_state(msg.payload)
        elif ev == Event.AUDIO_LEVEL:
            self.renderer.push_audio(msg.payload)
        elif ev == Event.SPEAK_LEVEL:
            self.renderer.push_speak(msg.payload)
        elif ev == Event.LOG:
            self._append_log(msg.payload)

    def _set_state(self, state: JarvisState) -> None:
        self.renderer.set_state(state)
        status = {
            JarvisState.IDLE: ("ONLINE", _OK),
            JarvisState.LISTENING: ("LISTENING", _ACCENT),
            JarvisState.THINKING: ("PROCESSING", _ACCENT),
            JarvisState.SPEAKING: ("SPEAKING", _OK),
            JarvisState.ERROR: ("ALERT", _ERR),
        }[state]
        self.canvas.itemconfig(self._hud["status"], text=status[0], fill=status[1])
        for st, tid in self._indicators.items():
            on = (st == state)
            self.canvas.itemconfig(tid, fill=_ACCENT if on else _DIM)

    def _update_gauges(self) -> None:
        m = sysinfo.read_metrics()
        for key, (track, fill) in self._gauges.items():
            val = m.get(key)
            if val is None:
                continue
            x0, y0, x1, y1 = self.canvas.coords(track)
            width = (x1 - x0) * (val / 100.0)
            colour = _ERR if val > 88 else (_WARN if val > 70 else _ACCENT)
            self.canvas.coords(fill, x0, y0, x0 + width, y1)
            self.canvas.itemconfig(fill, fill=colour)

    def _append_log(self, payload) -> None:
        level = payload.get("level", "info")
        source = payload.get("source", "core")
        message = payload.get("message", "")
        colour = {"warn": _WARN, "error": _ERR}.get(level, "#5fa8c9")
        self._log_lines.append((f"[{source}] {message}", colour))
        self._log_lines = self._log_lines[-6:]
        text = "\n".join(line for line, _ in self._log_lines)
        self.canvas.itemconfig(self._log_id, text=text,
                               fill=self._log_lines[-1][1])

    # -- shutdown ------------------------------------------------------------
    def _on_close(self) -> None:
        if self._on_close_cb:
            self._on_close_cb()
        self.root.destroy()
