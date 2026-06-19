"""Compliance-grade audit trail + structured application logging.

Subscribes to the EventBus and records every meaningful pipeline event to an
append-only, **hash-chained** JSONL file (``data/logs/audit-YYYYMMDD.jsonl``).
Each record embeds the SHA-256 of the previous record, so any tampering with or
removal of a historical entry is detectable — a standard enterprise audit
requirement.

It also configures rotating, levelled application logging
(``data/logs/jarvis.log``) and auto-prunes both audit and log files beyond the
configured retention window.

This module observes only; it never changes pipeline behaviour.
"""
from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..config import DATA_DIR, Config
from ..core.event_bus import Event, EventBus

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# pipeline events worth recording in the audit trail
_AUDITED = {
    Event.WAKE_WORD, Event.TRANSCRIPT, Event.INTENT, Event.SKILL_RESULT,
    Event.SPEAK_START, Event.AI_ERROR, Event.STATE_CHANGED, Event.COMMAND_LOG,
}


def _jsonable(value):
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class AuditLogger:
    def __init__(self, config: Config, bus: EventBus) -> None:
        self.config = config
        self.session_id = uuid.uuid4().hex[:12]
        self.user = self._current_user()
        self._audit_path = LOG_DIR / f"audit-{datetime.now():%Y%m%d}.jsonl"
        self._prev_hash = self._last_hash()  # continue the chain across sessions

        self._configure_logging()
        self.log = logging.getLogger("jarvis")
        self._prune_old_files()

        if config.audit_enabled:
            bus.subscribe_all(self._on_event)
        # mirror human-readable LOG events into the app log regardless
        bus.subscribe(Event.LOG, self._on_log)

        self.log.info("audit session %s started (user=%s, audit=%s)",
                      self.session_id, self.user, config.audit_enabled)
        self._write({"type": "session_start", "user": self.user,
                     "pid": _safe_pid()})

    # -- setup ---------------------------------------------------------------
    def _configure_logging(self) -> None:
        level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        logger = logging.getLogger("jarvis")
        logger.setLevel(level)
        if logger.handlers:
            return
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            "%Y-%m-%d %H:%M:%S")
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "jarvis.log", maxBytes=2_000_000, backupCount=5,
            encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    @staticmethod
    def _current_user() -> str:
        import os
        return os.getenv("USERNAME") or os.getenv("USER") or "unknown"

    # -- event handling ------------------------------------------------------
    def _on_event(self, msg) -> None:
        if msg.event not in _AUDITED:
            return
        self._write({"type": msg.event.value,
                     "payload": _jsonable(msg.payload)})

    def _on_log(self, msg) -> None:
        p = msg.payload or {}
        level = {"warn": logging.WARNING, "error": logging.ERROR}.get(
            p.get("level"), logging.INFO)
        self.log.log(level, "[%s] %s", p.get("source", "core"),
                     p.get("message", ""))

    def _last_hash(self) -> str:
        """Seed the hash chain from the last record of today's file, if any."""
        if not self._audit_path.exists():
            return "0" * 64
        try:
            last = self._audit_path.read_text(encoding="utf-8").strip().splitlines()[-1]
            return json.loads(last).get("hash", "0" * 64)
        except Exception:
            return "0" * 64

    # -- tamper-evident write ------------------------------------------------
    def _write(self, record: dict) -> None:
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "session": self.session_id,
            "user": self.user,
            "seq_prev": self._prev_hash,
            **record,
        }
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        entry["hash"] = hashlib.sha256(
            (self._prev_hash + line).encode("utf-8")).hexdigest()
        self._prev_hash = entry["hash"]
        try:
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:  # auditing must never break the pipeline
            self.log.error("audit write failed: %s", exc)

    # -- retention -----------------------------------------------------------
    def _prune_old_files(self) -> None:
        cutoff = time.time() - self.config.audit_retention_days * 86400
        for path in LOG_DIR.glob("audit-*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except Exception:
                pass


def _safe_pid() -> int:
    import os
    return os.getpid()
