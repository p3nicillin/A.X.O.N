"""Read-only staged update discovery; downloads/installations remain explicit."""
from __future__ import annotations

import json
import re
import urllib.request

from . import __version__

LATEST_RELEASE_API = "https://api.github.com/repos/p3nicillin/A.X.O.N/releases/latest"


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+){1,3})", value or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def check_for_update(timeout: float = 4.0) -> dict:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"AXON/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(512_000).decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "current": __version__, "error": str(exc)}
    latest = str(payload.get("tag_name") or "").lstrip("v")
    current_tuple, latest_tuple = _version_tuple(__version__), _version_tuple(latest)
    assets = [{"name": str(asset.get("name", "")),
               "url": str(asset.get("browser_download_url", ""))}
              for asset in payload.get("assets", []) if isinstance(asset, dict)]
    return {"ok": bool(latest_tuple), "current": __version__, "latest": latest,
            "available": bool(latest_tuple and latest_tuple > current_tuple),
            "release_url": str(payload.get("html_url") or ""),
            "assets": assets, "automatic_install": False}
