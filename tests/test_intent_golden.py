"""Regression golden set: a fixed list of utterances -> expected intent types,
run through the full router. Acts as a quality gate on the deterministic parser
and the routing/fallback path.

The same set is intended to be run against `local` and `cloud` backends too when
a runtime/key is available (see test_ai_backends for the mocked-local path); here
we gate the always-available `rules` engine so CI stays hermetic.
"""
from axon.ai.context import Context
from axon.ai.intent_engine import build_engine
from axon.config import Config
from axon.skills.registry import SkillRegistry

CATALOGUE = SkillRegistry().discover().catalogue()

# (utterance, expected intent type)
GOLDEN = [
    ("what time is it", "get_time"),
    ("tell me the time", "get_time"),
    ("what's today's date", "get_date"),
    ("system status", "system_info"),
    ("how's the system doing", "system_info"),
    ("open notepad", "open_app"),
    ("launch calculator", "open_app"),
    ("close chrome", "close_app"),
    ("note that I need to buy milk", "add_note"),
    ("read my notes", "read_notes"),
    ("clear my notes", "clear_notes"),
    ("search for the speed of light", "web_search"),
    ("look up the capital of France", "web_search"),
    ("list my files", "list_files"),
    ("take a screenshot", "capture_screenshot"),
    ("type Hello AXON", "type_text"),
    ("press ctrl+shift+s", "send_keystroke"),
    ("what is the weather", "get_weather"),
    ("weather forecast in Manchester", "get_weather"),
    ("calculate 17 times 6", "calculate"),
    ("what is 20 percent of 50", "calculate"),
    ("read file report.txt", "read_file"),
    ("write hello world to file notes.txt", "write_file"),
    ("create folder projects", "create_folder"),
    ("move file old.txt to archive/new.txt", "move_path"),
    ("delete file old.txt", "delete_path"),
    ("focus window Spotify", "focus_window"),
    ("close the current window", "close_window"),
    ("open YouTube on Google Chrome", "open_website"),
    ("hello AXON", "chat"),
    ("thanks", "chat"),
    ("reverse the polarity of the neutron flow", "unknown"),
]


def _router():
    cfg = Config()
    cfg.ai.engine = "rules"        # hermetic: deterministic floor only
    return build_engine(cfg, CATALOGUE)


def test_golden_set_rules_backend():
    r = _router()
    ctx = Context()
    misses = []
    for utterance, expected in GOLDEN:
        got = r.interpret(utterance, ctx).intent.type
        if got != expected:
            misses.append((utterance, expected, got))
    assert not misses, f"golden regressions: {misses}"


def test_golden_set_always_produces_valid_packet():
    """JSON-valid-on-first-try is 100% for rules (it never emits raw text)."""
    r = _router()
    ctx = Context()
    for utterance, _ in GOLDEN:
        p = r.interpret(utterance, ctx)
        assert p.intent.type            # non-empty, structured
        assert p.backend in ("rules", "rules-fastpath")


def test_router_metrics_track_fast_path_and_fallback():
    r = _router()
    ctx = Context()
    for utterance, _ in GOLDEN:
        r.interpret(utterance, ctx)
    snap = r.metrics.snapshot()
    assert snap["total"] == len(GOLDEN)
    # simple commands took the fast-path; free-text/chat/unknown fell through
    assert snap["fast_path_hits"] > 0
    assert snap["fallback_to_rules"] > 0
