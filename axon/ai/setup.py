"""§9 first-run setup flow for the local LLM.

If the configured engine wants a local runtime but none is reachable, print a
one-time, non-blocking guide on how to get the free local core running, then let
the app continue (it degrades cleanly to the rule backend in the meantime).

Deliberately does NOT execute any installer or download — the user stays in
control. It only detects, informs, and remembers that it has done so.
"""
from __future__ import annotations

from ..config import DATA_DIR

_FLAG = DATA_DIR / ".local_llm_setup_seen"

# ASCII-only on purpose: the Windows console is often cp1252 and would raise
# UnicodeEncodeError on box-drawing characters.
_GUIDE = """\
+--------------------------------------------------------------------+
|  A.X.O.N runs on a FREE local LLM - no API key, no data leaves     |
|  your machine. No local runtime was detected, so intent parsing    |
|  is currently using the built-in rule engine (still functional).   |
|                                                                    |
|  To enable the smarter local core (one-time, free):                |
|    1. Install Ollama:  https://ollama.com/download                 |
|    2. Pull the model:  ollama pull {model}                         |
|    3. Restart A.X.O.N - it will detect and warm the model.         |
|                                                                    |
|  Prefer something else? Any OpenAI-compatible local server works   |
|  (llama.cpp / LM Studio / Jan); set [ai.local] in config.toml.     |
|  To silence this and stay on rules: set engine = "rules".          |
+--------------------------------------------------------------------+"""


def first_run_check(config, router) -> bool:
    """Return True if a local runtime is ready; otherwise print the guide once.

    Only acts when the engine actually wants the local backend. Never raises.
    """
    engine = (config.ai.engine or "local").lower()
    if engine == "rules":
        return False
    if engine == "cloud" and config.ai.cloud.enabled:
        # cloud is the primary; local is only a fallback, don't nag.
        return False

    try:
        health = router.health()
        local = health.get("backends", {}).get("local", {})
        if local.get("available"):
            return True
    except Exception:
        pass

    # not available — show the guide at most once per machine
    try:
        if _FLAG.exists():
            return False
        print(_GUIDE.format(model=config.ai.local.model))
        _FLAG.write_text("seen", encoding="utf-8")
    except Exception:
        pass
    return False
