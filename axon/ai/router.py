"""IntentRouter — selects an AI-core backend per utterance and guarantees a
schema-valid IntentPacket via an explicit fallback chain.

    [hybrid rule fast-path]  ->  backend_1  ->  backend_2  ->  ...  ->  rules
                                  (skipped if unavailable / breaker open)

Design guarantees (carried over from v1 + this phase):
  * Always returns a valid IntentPacket — the rule backend is the guaranteed
    bottom of the chain and never raises.
  * A backend that is unavailable or fails is skipped with an audited reason;
    never a crash (satisfies "removing a dependency degrades one feature").
  * A repeatedly-failing backend trips a circuit breaker and is bypassed until a
    cooldown elapses, so a hung local runtime can't block the pipeline.
  * Every returned packet is tagged with provenance (backend, model, latency,
    repaired, cloud_routed) for the audit trail and diagnostic.
"""
from __future__ import annotations

import time

from ..core.event_bus import Event, EventBus
from .backends.base import IntentBackend, IntentBackendError, all_specs
from .backends.rules import RuleBackend
from .context import Context
from .schema import IntentPacket


class _Breaker:
    """A minimal per-backend circuit breaker."""

    def __init__(self, threshold: int = 3, cooldown: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.fails = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        if self.fails < self.threshold:
            return True
        if time.monotonic() >= self.open_until:   # half-open: allow one probe
            return True
        return False

    def record_success(self) -> None:
        self.fails = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.fails += 1
        if self.fails >= self.threshold:
            self.open_until = time.monotonic() + self.cooldown


class _Metrics:
    """Per-backend counters + latency samples for the §7 diagnostic."""

    def __init__(self) -> None:
        self.attempts: dict[str, int] = {}
        self.successes: dict[str, int] = {}
        self.failures: dict[str, int] = {}
        self.repairs: dict[str, int] = {}
        self.latencies: dict[str, list[float]] = {}
        self.fast_path_hits = 0
        self.fallback_to_rules = 0
        self.total = 0

    def _bump(self, d: dict, k: str) -> None:
        d[k] = d.get(k, 0) + 1

    def record(self, backend: str, *, ok: bool, latency_ms: float,
               repaired: bool) -> None:
        self._bump(self.attempts, backend)
        self._bump(self.successes if ok else self.failures, backend)
        if repaired:
            self._bump(self.repairs, backend)
        self.latencies.setdefault(backend, []).append(latency_ms)

    @staticmethod
    def _pct(samples: list[float], p: float) -> float:
        if not samples:
            return 0.0
        s = sorted(samples)
        i = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
        return round(s[i], 1)

    def snapshot(self) -> dict:
        out = {"total": self.total, "fast_path_hits": self.fast_path_hits,
               "fallback_to_rules": self.fallback_to_rules, "backends": {}}
        for b, n in self.attempts.items():
            succ = self.successes.get(b, 0)
            lat = self.latencies.get(b, [])
            out["backends"][b] = {
                "attempts": n,
                "success_rate": round(succ / n, 2) if n else 0.0,
                "repair_rate": round(self.repairs.get(b, 0) / n, 2) if n else 0.0,
                "p50_ms": self._pct(lat, 50), "p95_ms": self._pct(lat, 95),
            }
        return out


class IntentRouter:
    def __init__(self, config, catalogue, bus: EventBus | None = None, *,
                 backends: dict[str, IntentBackend], rule_backend: RuleBackend,
                 chain: list[str], hybrid: bool = True) -> None:
        self.config = config
        self.catalogue = catalogue
        self.bus = bus
        self.backends = backends
        self.rules = rule_backend
        self.chain = chain
        self.hybrid = hybrid
        self._specs = all_specs(catalogue)
        self._breakers: dict[str, _Breaker] = {n: _Breaker() for n in backends}
        self.metrics = _Metrics()

    # -- logging -------------------------------------------------------------
    def _log(self, level: str, message: str) -> None:
        if self.bus is not None:
            self.bus.publish(Event.LOG, {"level": level, "source": "ai",
                                         "message": message})

    # -- the orchestrator-facing API (drop-in for the old engine) ------------
    def interpret(self, text: str, context: Context) -> IntentPacket:
        self.metrics.total += 1

        # §5 hybrid fast-path: a simple, unambiguous command skips the LLM.
        if self.hybrid:
            fp = self.rules.fast_path(text, context)
            if fp is not None:
                self.metrics.fast_path_hits += 1
                return fp.tag(backend="rules-fastpath",
                              model=self.rules.model_name)

        # walk the configured chain
        for name in self.chain:
            backend = self.backends.get(name)
            if backend is None:
                continue
            breaker = self._breakers.get(name)
            if breaker is not None and not breaker.allow():
                self._log("debug", f"{name}: skipped (circuit open)")
                continue
            if not backend.available():
                self._log("debug", f"{name}: skipped (unavailable)")
                if breaker is not None:
                    breaker.record_failure()
                continue

            t0 = time.monotonic()
            try:
                packet = backend.parse(text, context, self._specs)
            except IntentBackendError as exc:
                latency = (time.monotonic() - t0) * 1000
                if breaker is not None:
                    breaker.record_failure()
                self.metrics.record(name, ok=False, latency_ms=latency,
                                    repaired=False)
                self._log("warn", f"{name} failed ({exc.reason}); falling back")
                continue

            latency = (time.monotonic() - t0) * 1000
            if breaker is not None:
                breaker.record_success()
            self.metrics.record(name, ok=True, latency_ms=latency,
                                repaired=packet.repaired)
            return packet.tag(backend=name, model=backend.model_name,
                              latency_ms=round(latency, 1),
                              cloud_routed=(backend.name == "cloud"))

        # guaranteed bottom of the chain — rules never raise.
        self.metrics.fallback_to_rules += 1
        t0 = time.monotonic()
        packet = self.rules.parse(text, context, self._specs)
        latency = (time.monotonic() - t0) * 1000
        self.metrics.record("rules", ok=True, latency_ms=latency, repaired=False)
        return packet.tag(backend="rules", model=self.rules.model_name,
                          latency_ms=round(latency, 1))

    # -- diagnostics ---------------------------------------------------------
    def warm(self) -> None:
        """Preload any backend that supports warming (the local LLM)."""
        for name in self.chain:
            b = self.backends.get(name)
            if b is not None and hasattr(b, "warm"):
                try:
                    if b.available():
                        b.warm()
                        self._log("info", f"{name}: warmed")
                except Exception:
                    pass

    def health(self) -> dict:
        """§7 /health: reachability of each backend + the active chain."""
        out = {"chain": list(self.chain), "active": None, "backends": {}}
        for name, b in self.backends.items():
            entry: dict = {"available": False}
            try:
                if hasattr(b, "health_detail"):
                    ok, detail = b.health_detail()
                    entry = {"available": ok, "detail": detail}
                else:
                    entry = {"available": b.available()}
                entry["model"] = b.model_name
            except Exception as exc:
                entry = {"available": False, "detail": str(exc)}
            out["backends"][name] = entry
        for name in self.chain:                   # first healthy = active
            if out["backends"].get(name, {}).get("available"):
                out["active"] = name
                break
        if out["active"] is None:
            out["active"] = "rules"
        return out
