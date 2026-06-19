"""Local structured crash reports with conservative secret/path redaction."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from .. import __version__
from ..config import DATA_DIR

CRASH_DIR = DATA_DIR / "crashes"

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|password)(\s*[=:]\s*)[^\s,;]+"),
)


def scrub(value: str) -> str:
    text = str(value or "")
    home = str(Path.home())
    if home:
        text = re.sub(re.escape(home), "%USERPROFILE%", text,
                      flags=re.IGNORECASE)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class CrashReporter:
    def __init__(self, config, session_id: str, root: Path | None = None) -> None:
        self.config = config
        self.session_id = session_id
        self.root = root or CRASH_DIR
        self._old_sys_hook = None
        self._old_thread_hook = None

    def install(self) -> None:
        if not self.config.crash_reporting_enabled or self._old_sys_hook is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._prune()
        self._old_sys_hook = sys.excepthook
        self._old_thread_hook = threading.excepthook

        def sys_hook(exc_type, exc, tb):
            self.capture(exc_type, exc, tb, threading.current_thread().name)
            self._old_sys_hook(exc_type, exc, tb)

        def thread_hook(args):
            self.capture(args.exc_type, args.exc_value, args.exc_traceback,
                         getattr(args.thread, "name", "unknown"))
            self._old_thread_hook(args)

        sys.excepthook = sys_hook
        threading.excepthook = thread_hook

    def uninstall(self) -> None:
        if self._old_sys_hook is not None:
            sys.excepthook = self._old_sys_hook
            self._old_sys_hook = None
        if self._old_thread_hook is not None:
            threading.excepthook = self._old_thread_hook
            self._old_thread_hook = None

    def capture(self, exc_type, exc, tb, thread_name: str = "unknown") -> Path | None:
        if not self.config.crash_reporting_enabled or exc_type in (
                KeyboardInterrupt, SystemExit):
            return None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            report_id = uuid.uuid4().hex[:12]
            report = {
                "id": report_id,
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "session": self.session_id,
                "axon_version": __version__,
                "exception_type": getattr(exc_type, "__name__", str(exc_type)),
                "message": scrub(str(exc))[:1000],
                "stack": scrub("".join(traceback.format_exception(
                    exc_type, exc, tb)))[:20000],
                "process_id": os.getpid(),
                "thread": scrub(thread_name)[:200],
            }
            path = self.root / f"crash-{report['timestamp'][:10]}-{report_id}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
            tmp.replace(path)
            self._prune()
            return path
        except Exception:
            return None

    def summary(self) -> dict:
        files = sorted(self.root.glob("crash-*.json"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        last = None
        if files:
            try:
                raw = json.loads(files[0].read_text(encoding="utf-8"))
                last = {k: raw.get(k) for k in
                        ("id", "timestamp", "exception_type", "message")}
            except Exception:
                last = {"id": files[0].stem, "timestamp": "", "exception_type": "unknown"}
        return {"enabled": self.config.crash_reporting_enabled,
                "count": len(files), "last": last}

    def _prune(self) -> None:
        cutoff = time.time() - max(1, self.config.crash_retention_days) * 86400
        for path in self.root.glob("crash-*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
