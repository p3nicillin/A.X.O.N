import json
import os
import sys
import threading
import time

from axon.config import Config
from axon.enterprise.crash import CrashReporter


def test_crash_report_is_structured_and_redacted(tmp_path):
    cfg = Config()
    reporter = CrashReporter(cfg, "session-1", tmp_path)
    try:
        raise RuntimeError(f"api_key=supersecret sk-abcdefghijk {os.path.expanduser('~')}")
    except RuntimeError as exc:
        path = reporter.capture(type(exc), exc, exc.__traceback__, "worker")

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["session"] == "session-1"
    assert report["exception_type"] == "RuntimeError"
    rendered = json.dumps(report)
    assert "supersecret" not in rendered
    assert "sk-abcdefghijk" not in rendered
    assert "%USERPROFILE%" in rendered


def test_crash_retention_and_hook_restore(tmp_path):
    cfg = Config(); cfg.crash_retention_days = 1
    old = tmp_path / "crash-old.json"
    old.write_text("{}", encoding="utf-8")
    stale = time.time() - 3 * 86400
    os.utime(old, (stale, stale))
    reporter = CrashReporter(cfg, "session", tmp_path)
    sys_hook, thread_hook = sys.excepthook, threading.excepthook

    reporter.install()
    assert sys.excepthook is not sys_hook
    assert threading.excepthook is not thread_hook
    assert not old.exists()
    reporter.uninstall()
    assert sys.excepthook is sys_hook
    assert threading.excepthook is thread_hook
