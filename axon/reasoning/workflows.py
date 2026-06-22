"""Atomic, privacy-aware checkpoints for multi-step workflow recovery."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_PRIVATE_KEYS = {"text", "password", "secret", "token", "content", "body"}


def _safe_parameters(parameters: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    safe, resumable = {}, True
    for key, value in parameters.items():
        if key.lower() in _PRIVATE_KEYS:
            safe[key] = "<redacted>"
            resumable = False
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = repr(value)[:200]
            resumable = False
    return safe, resumable


class WorkflowStore:
    """Persist bounded workflow metadata; never stores free-form private inputs."""

    def __init__(self, path: Path, history_limit: int = 100) -> None:
        self.path = path
        self.history_limit = max(10, int(history_limit))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "workflows": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("workflows"), list):
                return data
        except Exception:
            pass
        return {"version": 1, "workflows": []}

    def _write(self, data: dict) -> None:
        data["workflows"] = data.get("workflows", [])[-self.history_limit:]
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def create(self, correlation: str, source_text: str, steps: list) -> dict:
        serialised, resumable = [], True
        for step in steps:
            params, safe = _safe_parameters(dict(step.parameters))
            serialised.append({"type": step.type, "parameters": params})
            resumable = resumable and safe
        now = time.time()
        record = {"id": correlation, "status": "running", "index": 0,
                  "total": len(serialised), "steps": serialised,
                  "source": "", "resumable": resumable,
                  "created_at": now, "updated_at": now, "results": []}
        with self._lock:
            data = self._read()
            data["workflows"] = [r for r in data["workflows"]
                                 if r.get("id") != correlation] + [record]
            self._write(data)
        return record

    def checkpoint(self, correlation: str, index: int, result) -> None:
        with self._lock:
            data = self._read()
            record = next((r for r in data["workflows"]
                           if r.get("id") == correlation), None)
            if record is None:
                return
            record["index"] = int(index)
            record["updated_at"] = time.time()
            record.setdefault("results", []).append({
                "ok": bool(result.ok), "skill": str(result.skill)[:100],
                "summary": str(result.summary)[:300]})
            self._write(data)

    def finish(self, correlation: str, status: str) -> None:
        if status not in {"completed", "failed", "cancelled", "timed_out"}:
            status = "failed"
        with self._lock:
            data = self._read()
            for record in data["workflows"]:
                if record.get("id") == correlation:
                    record["status"] = status
                    record["updated_at"] = time.time()
                    break
            self._write(data)

    def get(self, correlation: str) -> dict | None:
        with self._lock:
            record = next((r for r in self._read()["workflows"]
                           if r.get("id") == correlation), None)
            return dict(record) if record else None

    def list(self, *, recoverable_only: bool = False) -> list[dict]:
        with self._lock:
            records = list(reversed(self._read()["workflows"]))
        if recoverable_only:
            records = [r for r in records if r.get("status") == "running"
                       and r.get("resumable") and r.get("index", 0) < r.get("total", 0)]
        return records

    def cancel(self, correlation: str) -> bool:
        record = self.get(correlation)
        if not record or record.get("status") != "running":
            return False
        self.finish(correlation, "cancelled")
        return True
