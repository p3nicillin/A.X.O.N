"""Central configuration and well-known paths.

Settings resolve in this order (later wins):
    1. defaults defined here
    2. ``config.toml`` next to the repo root (optional)
    3. environment variables (``AXON_*`` and ``ANTHROPIC_API_KEY``)
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

# --- Well-known directories -------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"          # logs, notes, runtime state
MEMORY_DIR = DATA_DIR / "memory"  # §4 episodic vault + semantic index
MODELS_DIR = ROOT / "models"      # downloaded STT models live here
SKILLS_DIR = Path(__file__).resolve().parent / "skills"

for _d in (DATA_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


_LEGACY_ENV_PREFIX = "J" + "ARVIS_"


def _env(name: str) -> str | None:
    current = os.getenv("AXON_" + name)
    if current is not None:
        return current
    legacy = os.getenv(_LEGACY_ENV_PREFIX + name)
    if legacy is not None:
        print(f"[config] legacy env var for {name} is deprecated; use AXON_{name}.")
    return legacy


# --- AI core backend config (§1/§6 of the Phase-2 prompt) -------------------
@dataclass
class LocalAIConfig:
    """Local LLM runtime — the free, on-device default."""
    runtime: str = "ollama"                    # ollama | llamacpp | openai_compatible
    endpoint: str = "http://localhost:11434"
    model: str = "llama3.2:3b"                 # baseline 3B runs ~everywhere
    temperature: float = 0.1
    keep_alive: str = "30m"
    timeout_ms: int = 10000


@dataclass
class CloudAIConfig:
    """Optional, opt-in Claude backend — OFF by default. The key is loaded from
    the secrets store at runtime, never stored here."""
    enabled: bool = False
    model: str = "claude-haiku-4-5-20251001"


@dataclass
class AIConfig:
    engine: str = "local"                      # auto | local | cloud | rules
    fallback: list[str] = field(default_factory=lambda: ["local", "rules"])
    hybrid_fastpath: bool = True               # §5 rule fast-path before the LLM
    warm_on_start: bool = True                 # preload the model at boot
    local: LocalAIConfig = field(default_factory=LocalAIConfig)
    cloud: CloudAIConfig = field(default_factory=CloudAIConfig)


@dataclass
class Config:
    # --- AI intent engine ---
    anthropic_api_key: str = ""   # legacy; prefer ANTHROPIC_API_KEY / secrets store
    ai_model: str = "claude-haiku-4-5-20251001"   # legacy cloud model alias
    ai_max_tokens: int = 512
    # pluggable AI-core backend (local-by-default). See AIConfig above.
    ai: AIConfig = field(default_factory=AIConfig)

    # --- Speech to text ---
    stt_model_path: str = ""        # command model (large, accurate). "" = auto
    stt_wake_model_path: str = ""   # wake model (small, grammar-capable). "" = auto
    sample_rate: int = 16000

    # --- Voice activity detection (energy based) ---
    vad_silence_ms: int = 800       # trailing silence that ends an utterance
    vad_start_rms: float = 0.012    # rms threshold to consider "speech started"

    # --- Wake word ---
    wake_word: str = "Axon"
    require_wake_word: bool = False  # if True, ignore speech until wake word heard
    acknowledge_wake: bool = True    # speak a short ack when woken with no command
    wake_ack_phrase: str = "Yes, sir?"
    address_term: str = "sir"        # how AXON addresses the user
    # Small STT models mishear the proper noun "Axon". Accept these leading
    # mishearings as the wake word, plus anything within wake_fuzzy_threshold.
    wake_aliases: list[str] = field(default_factory=lambda: [
        "axon", "akson", "axen", "axton", "action", "axis", "javis",
        "jervis", "this",
    ])
    wake_fuzzy_threshold: float = 0.72  # 0..1 similarity to "Axon" (lower = laxer)
    # Dedicated always-on wake spotter: a grammar-biased recogniser listens only
    # for the wake word, then a full recogniser captures the command.
    use_wake_spotter: bool = True
    active_listen_timeout: float = 8.0  # seconds to wait for a command after waking

    # --- Text to speech ---
    tts_rate: int = 178             # words per minute
    tts_voice: str = ""             # SAPI voice name substring; "" = system default

    # --- Enterprise: audit & logging ---
    audit_enabled: bool = True       # append-only JSONL audit trail of every action
    log_level: str = "INFO"          # DEBUG | INFO | WARN | ERROR
    audit_retention_days: int = 90   # auto-prune audit/log files older than this

    # --- Visual ---
    window_width: int = 1100
    window_height: int = 720
    target_fps: int = 60
    # Rendering frontend: "auto" prefers the AXON web UI (pywebview), then the
    # PySide6 + GLSL bloom core, then the pure-Tkinter HUD.
    ui_backend: str = "auto"          # "auto" | "web" | "qt" | "tk"
    bloom_intensity: float = 1.35     # additive bloom strength in the composite
    bloom_threshold: float = 0.55     # luminance above which pixels bloom
    bloom_bokeh_radius: float = 18.0  # disc radius (texels) for bokeh highlights

    # --- Memory (§4 episodic + semantic) ---
    memory_enabled: bool = True       # persist durable facts + semantic recall
    memory_recall_k: int = 3          # max memories injected into AI context/turn
    memory_min_score: float = 0.28    # min cosine similarity to count as a hit
    memory_embedding_dim: int = 384   # local embedder vector width
    memory_allow_secrets: bool = False  # §4: never store secrets unless allowed

    # --- Reasoning (§5 planning + §7 critic) ---
    planning_enabled: bool = True     # generate a structured plan before acting
    critic_enabled: bool = True       # safety/logic gate before execution
    critic_min_confidence: float = 0.0  # >0 flags low-confidence intents as risky

    # --- User model (§17) ---
    user_model_enabled: bool = True   # infer a persistent profile to bias replies

    # --- Autonomy (§16, opt-in: observes the system, suggestion-only) ---
    autonomy_enabled: bool = False    # background context awareness + suggestions
    autonomy_interval: float = 5.0    # seconds between context ticks
    autonomy_idle_threshold: float = 300.0   # seconds of inactivity = idle
    autonomy_load_threshold: float = 90.0    # cpu/mem % that triggers an alert
    autonomy_min_confidence: float = 0.7     # §16.3 min confidence to surface advice

    # --- Safety ---
    confirm_sensitive: bool = True  # require explicit confirmation for guarded skills
    web_fallback_on_unknown: bool = False  # §2.3/§8: route UNKNOWN -> WEB_SEARCH
    disabled_skills: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()

        # 2. config.toml
        toml_path = ROOT / "config.toml"
        if toml_path.exists():
            with toml_path.open("rb") as fh:
                data = tomllib.load(fh)
            valid = {f.name for f in fields(cls)}
            for key, value in data.items():
                if key == "ai" and isinstance(value, dict):
                    cfg._apply_ai_table(value)      # nested [ai] table
                elif key in valid:
                    setattr(cfg, key, value)

        # 3. environment overrides
        cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
        for f in fields(cls):
            if f.name == "ai":                       # handled by _apply_ai_env
                continue
            env = _env(f.name.upper())
            if env is None:
                continue
            cur = getattr(cfg, f.name)
            if isinstance(cur, bool):
                setattr(cfg, f.name, env.strip().lower() in ("1", "true", "yes", "on"))
            elif isinstance(cur, int):
                setattr(cfg, f.name, int(env))
            elif isinstance(cur, float):
                setattr(cfg, f.name, float(env))
            elif isinstance(cur, list):
                setattr(cfg, f.name, [s.strip() for s in env.split(",") if s.strip()])
            else:
                setattr(cfg, f.name, env)
        cfg._apply_ai_env()
        return cfg

    # -- nested [ai] config helpers -----------------------------------------
    def _apply_ai_table(self, table: dict) -> None:
        """Merge a parsed ``[ai]`` toml table onto the AIConfig defaults."""
        for key in ("engine", "fallback", "hybrid_fastpath", "warm_on_start"):
            if key in table:
                setattr(self.ai, key, table[key])
        for sub, obj in (("local", self.ai.local), ("cloud", self.ai.cloud)):
            block = table.get(sub)
            if isinstance(block, dict):
                for k, v in block.items():
                    if hasattr(obj, k):
                        setattr(obj, k, v)

    def _apply_ai_env(self) -> None:
        """Env overrides for the AI block (AXON_AI_*). Admin policy can lock the
        engine to local-only this way (a legitimate cost/compliance control)."""
        def _b(v: str) -> bool:
            return v.strip().lower() in ("1", "true", "yes", "on")

        if _env("AI_ENGINE"):
            self.ai.engine = _env("AI_ENGINE")
        if _env("AI_FALLBACK"):
            self.ai.fallback = [s.strip() for s in _env("AI_FALLBACK").split(",")
                                if s.strip()]
        if _env("AI_HYBRID_FASTPATH") is not None:
            self.ai.hybrid_fastpath = _b(_env("AI_HYBRID_FASTPATH"))
        if _env("AI_WARM_ON_START") is not None:
            self.ai.warm_on_start = _b(_env("AI_WARM_ON_START"))
        # local.*
        if _env("AI_LOCAL_RUNTIME"):
            self.ai.local.runtime = _env("AI_LOCAL_RUNTIME")
        if _env("AI_LOCAL_ENDPOINT"):
            self.ai.local.endpoint = _env("AI_LOCAL_ENDPOINT")
        if _env("AI_LOCAL_MODEL"):
            self.ai.local.model = _env("AI_LOCAL_MODEL")
        if _env("AI_LOCAL_TEMPERATURE"):
            self.ai.local.temperature = float(_env("AI_LOCAL_TEMPERATURE"))
        if _env("AI_LOCAL_KEEP_ALIVE"):
            self.ai.local.keep_alive = _env("AI_LOCAL_KEEP_ALIVE")
        if _env("AI_LOCAL_TIMEOUT_MS"):
            self.ai.local.timeout_ms = int(_env("AI_LOCAL_TIMEOUT_MS"))
        # cloud.*
        if _env("AI_CLOUD_ENABLED") is not None:
            self.ai.cloud.enabled = _b(_env("AI_CLOUD_ENABLED"))
        if _env("AI_CLOUD_MODEL"):
            self.ai.cloud.model = _env("AI_CLOUD_MODEL")

    @property
    def has_ai(self) -> bool:
        """Legacy flag: True iff a cloud key is configured (kept for back-compat)."""
        return bool(self.anthropic_api_key)
