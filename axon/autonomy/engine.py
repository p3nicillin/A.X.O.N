"""§16.3 + §16.4 autonomy engine: the controlled proactive layer.

A background daemon that, on each tick:

  1. samples the read-only :class:`ContextSensor` (§16.1),
  2. derives §16.2 :class:`ContextEvent`s from changes (idle, resume, load,
     app open/close),
  3. checks the §16.5 task scheduler,
  4. periodically consolidates memory into the §17 user model (the §16.4
     "background thinking loop"),
  5. raises §16.3 :class:`Suggestion`s — but only when confidence clears the
     threshold and the situation is genuinely actionable.

Hard guarantees (§1, §16.3): it is observation-only. It NEVER executes a skill,
launches/closes anything, or changes state — it can only *suggest*, surfaced on
the bus for the user to accept or ignore.
"""
from __future__ import annotations

import threading

from ..core.event_bus import Event, EventBus
from . import events as E
from .context import ContextSensor
from .events import ContextEvent, Suggestion
from .tasks import TaskScheduler


class AutonomyEngine:
    def __init__(self, config, bus: EventBus, *, sensor: ContextSensor | None = None,
                 scheduler: TaskScheduler | None = None, user_model=None,
                 memory=None) -> None:
        self.config = config
        self.bus = bus
        self.sensor = sensor or ContextSensor()
        self.scheduler = scheduler
        self.user_model = user_model
        self.memory = memory

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # change-detection state
        self._prev_apps: frozenset[str] = frozenset()
        self._was_idle = False
        self._alert_streak = 0
        self._alert_active = False
        self._ticks = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autonomy",
                                        daemon=True)
        self._thread.start()
        self._log("info", "Autonomy engine online (observation-only).")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # -- loop ----------------------------------------------------------------
    def _loop(self) -> None:
        interval = max(1.0, float(self.config.autonomy_interval))
        # establish a baseline before diffing
        try:
            self._prev_apps = self.sensor.snapshot().apps
        except Exception:
            pass
        while not self._stop.wait(interval):
            try:
                self._tick()
            except Exception as exc:               # a bad tick must not kill it
                self._log("warn", f"autonomy tick error: {exc!r}")

    def tick_once(self):
        """Run a single tick synchronously (used by tests)."""
        return self._tick()

    def _tick(self) -> list[Suggestion]:
        self._ticks += 1
        snap = self.sensor.snapshot()
        suggestions: list[Suggestion] = []

        for event in self._detect(snap):
            self._emit_event(event)
            sug = self._suggest_for(event, snap)
            if sug is not None:
                suggestions.append(sug)

        # §16.5 scheduled / recurring / condition tasks
        if self.scheduler is not None:
            for task in self.scheduler.due_tasks(snap):
                event = ContextEvent(E.TASK_DUE, context=task.description)
                self._emit_event(event)
                suggestions.append(Suggestion(
                    text=task.description, reason=f"task '{task.id}' due",
                    confidence=1.0, source_event=E.TASK_DUE))

        # §16.4 background thinking: periodically consolidate memory -> profile
        if self._ticks % 12 == 0 and self.user_model is not None:
            self.user_model.refresh_preferences(self.memory)

        for sug in suggestions:
            self._raise(sug)
        return suggestions

    # -- §16.2 detection -----------------------------------------------------
    def _detect(self, snap) -> list[ContextEvent]:
        evs: list[ContextEvent] = []

        # idle / resume
        idle_thr = float(self.config.autonomy_idle_threshold)
        if snap.idle_seconds >= idle_thr and not self._was_idle:
            self._was_idle = True
            evs.append(ContextEvent(E.IDLE, context=f"idle {snap.idle_seconds:.0f}s"))
        elif snap.idle_seconds < idle_thr and self._was_idle:
            self._was_idle = False
            evs.append(ContextEvent(E.RESUME, context="user active again"))

        # sustained system load (require two consecutive ticks = "repeated")
        load_thr = float(self.config.autonomy_load_threshold)
        over = snap.cpu >= load_thr or snap.memory >= load_thr
        if over:
            self._alert_streak += 1
            if self._alert_streak >= 2 and not self._alert_active:
                self._alert_active = True
                evs.append(ContextEvent(
                    E.SYSTEM_ALERT,
                    context=f"cpu {snap.cpu:.0f}% mem {snap.memory:.0f}%"))
        else:
            self._alert_streak = 0
            self._alert_active = False

        # app open / close (event stream only; not every one is a suggestion)
        if self._prev_apps:
            opened = snap.apps - self._prev_apps
            closed = self._prev_apps - snap.apps
            for name in sorted(opened)[:5]:
                evs.append(ContextEvent(E.APP_OPEN, context=name))
            for name in sorted(closed)[:5]:
                evs.append(ContextEvent(E.APP_CLOSE, context=name))
        self._prev_apps = snap.apps
        return evs

    # -- §16.3 suggestion (gated) -------------------------------------------
    def _suggest_for(self, event: ContextEvent, snap) -> Suggestion | None:
        if event.type == E.SYSTEM_ALERT:
            # confidence scales with how far over the threshold we are
            load = max(snap.cpu, snap.memory)
            conf = min(1.0, 0.6 + (load - self.config.autonomy_load_threshold) / 50.0)
            return Suggestion(
                text=(f"System load is high ({event.context}). Would you like a "
                      "status report, sir?"),
                reason="sustained high system load", confidence=conf,
                source_event=event.type)
        if event.type == E.IDLE:
            return Suggestion(
                text="You've been away a while. I'll keep watch, sir.",
                reason="user idle", confidence=0.7, source_event=event.type)
        return None

    def _raise(self, sug: Suggestion) -> None:
        # §16.3: only surface confident, safe advice; never act on it.
        if sug.confidence < float(self.config.autonomy_min_confidence):
            self._log("debug", f"suggestion withheld (low conf {sug.confidence:.2f}): "
                               f"{sug.text}")
            return
        self.bus.publish(Event.SUGGESTION, sug.as_dict())
        self._log("info", f"suggestion: {sug.text}", source="autonomy")

    # -- helpers -------------------------------------------------------------
    def _emit_event(self, event: ContextEvent) -> None:
        self.bus.publish(Event.CONTEXT_EVENT, event.as_dict())
        self._log("debug", f"{event.type}: {event.context}", source="autonomy")

    def _log(self, level: str, message: str, source: str = "autonomy") -> None:
        self.bus.publish(Event.LOG, {"level": level, "source": source,
                                     "message": message})
