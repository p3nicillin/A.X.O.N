# AXON Feature Inventory

This is the living checklist for wiring AXON capabilities into the AXON UI.
Every user-facing capability should have a visible/control surface here unless
it is a first-install or machine-policy concern.

| Capability | Current surface today | AXON UI surface |
|---|---|---|
| `TimeDateSkill` | Manifest: `get_time`, `get_date`; enabled by discovery; not sensitive | Skills panel shows intents/parameters/enabled state; real toggle updates registry state |
| `AppLauncherSkill` | Manifest: `open_app(app)`, `close_app(app)`; named Windows apps; close is destructive and confirmation-gated | Skills panel shows intents/parameters; toggle updates registry state |
| `BrowserSkill` | Manifest: `open_website(site, browser)`; known-site aliases or validated HTTP(S) URLs; requested browser executable must be installed | Prevents website phrases from becoming executable names and keeps failures inside AXON |
| `SystemInfoSkill` | Manifest: `system_info`; metrics read via `psutil` fallback | Skills panel plus live telemetry gauges |
| `WebSearchSkill` | Manifest: `web_search(query)`; opens DuckDuckGo/browser fallback; not sensitive | Skills panel shows intent/parameter; toggle updates registry state |
| `WeatherSkill` | Manifest: `get_weather(location, days)`; cached Open-Meteo JSON; configurable default location; never opens a browser | Spoken and conversation responses remain in AXON; structured current/forecast data flows through `SkillResult` |
| `CalculatorSkill` | Manifest: `calculate(expression)`; bounded AST evaluation with arithmetic, constants, and common functions; no `eval` | Fast-path response plus structured result card |
| `NotesSkill` | Manifest: `add_note(text)`, `read_notes`, `clear_notes`; local JSON notes | Skills panel plus memory drawer entries for stored durable facts |
| `FileSystemSkill` | Read/list/find/open plus atomic write/append, create folder, move, and non-recursive delete; hard sandbox root `data/workspace`; mutations are intent-level sensitive | Structured result cards, sandbox metadata, per-intent confirmation, and live enable toggle |
| `MediaControlSkill` | Manifest: `play_pause`, `next_track`, `previous_track`; OS media keys via ctypes; not sensitive | Skills panel shows intents/enabled state; toggle updates registry state |
| `VolumeSkill` | Manifest: `volume_up(steps)`, `volume_down(steps)`, `mute_toggle`; OS volume keys via ctypes, steps clamped 1-10; not sensitive | Skills panel shows intents/parameters; toggle updates registry state |
| `WindowControlSkill` | Foreground/named focus, minimise, maximise, restore, and graceful `WM_CLOSE`; close is intent-level sensitive | Skills panel shows intents, title parameter, sensitivity, and enabled state |
| `ClipboardSkill` | Manifest: `read_clipboard`, `set_clipboard(text)`; read returns a bounded preview; write is intent-level sensitive | Skills panel exposes per-intent sensitivity, parameters, and enabled state |
| `ScreenshotSkill` | Manifest: `capture_screenshot(filename)`; Pillow capture; filenames cannot contain paths; PNG output confined to `data/workspace/screenshots`; sensitive | Skills panel shows sandboxed capture intent/parameter and confirmation status |
| `KeyboardSkill` | Manifest: `type_text(text)`, `send_keystroke(keys)`; Win32 input only; text bounded to 1,000 characters; shortcuts allow-listed; sensitive | Skills panel shows both confirmation-gated intents and parameters |
| AI backend: local | `[ai] engine`, `[ai.local]`; Ollama/llama.cpp/OpenAI-compatible runtime; health via router | AI panel shows active backend, fallback chain, model/health, latency and fallback metrics; mode control rebuilds the router live |
| AI backend: cloud | `[ai.cloud] enabled`, model, vendor key from env/secrets; off by default | AI panel shows configured health without exposing secrets |
| AI backend: rules | Guaranteed deterministic fallback and hybrid fast path | AI panel shows fallback-to-rules and fast-path counts |
| General in-app answer | Universal tool-less `answer` intent for knowledge, explanations, coding/writing help, and advice | Full response stays in conversation; live/action requests still require skills |
| Visual state: idle | `AxonState.IDLE`, event `STATE_CHANGED` | Core status text and diagnostics state |
| Visual state: listening | `SPEECH_START`, wake/audio events | Core status text, mic chip, waveform/amplitude |
| Visual state: thinking | AI worker sets `AxonState.THINKING` | Core reasoning animation/status |
| Visual state: speaking | TTS sets `SPEAK_START`/`SPEAK_LEVEL` | Core status plus TTS amplitude |
| Visual state: error | Failed execution sets `AxonState.ERROR` | Core alert state |
| Synaptic activity | Live CPU/memory/network telemetry, microphone amplitude, request deltas, backend latency, and pipeline state events | Firing rate, pulse energy/speed, and node glow are data-driven rather than timer-randomised |
| Wake word | `wake_word`, aliases, `require_wake_word`, wake spotter | Diagnostics panel shows wake word/required state; status bar reflects listening |
| Hybrid speech recognition | Grammar-biased Vosk wake detector plus faster-whisper `small.en` command transcription; automatic Vosk fallback | Voice panel reports active recognizer/model; model loads in background |
| Personal speech adaptation | Persistent phrase-level heard→intended corrections; no raw audio or biometric embedding retained | Voice panel adds/removes corrections live from `data/speech_profile.json` |
| Mic on/off | Audio input `set_enabled`; web mic button | Bottom mic control, waveform, diagnostics mic availability |
| Barge-in / Esc | TTS interrupt path in renderer affordances | Existing renderer control remains; listed in diagnostics |
| Dev input | Hidden developer text input/F2 affordance | Bottom input remains developer affordance, not primary chat surface |
| Follow-up/active listen | `active_listen_timeout` after wake spotter | Diagnostics panel lists timeout/config state |
| Sensitive confirmation | Orchestrator `_pending` and confirmation resolver | Spoken prompt plus audit/log panel record of confirmation decision |
| TTS backend/voice/rate | `tts_voice`, `tts_rate`, native SAPI5/pyttsx3 fallback | Voice panel selects installed voices and applies rate live; settings persist atomically |
| Persona/address style | `wake_ack_phrase`, `address_term`, AXON identity | Voice panel edits and persists persona settings and wake requirement |
| Audit trail | Hash-chained JSONL in `data/logs/audit-YYYYMMDD.jsonl` | Audit panel live-tails events and pages scrubbed historical summaries newest-first |
| Structured app log | Rotating `data/logs/AXON.log` | Audit panel live-tails `Event.LOG` entries |
| Metrics | Router `_Metrics`; system metrics; transcript/submit-to-first-speech latency history | AI panel and telemetry show backend metrics plus full-turn latest/p95 latency |
| Structured results | Data-rich `SkillResult` payloads from weather, calculator, files, system, and screenshots | Accessible in-conversation result cards plus quick-command controls |
| `/health` diagnostic equivalent | `IntentRouter.health()` and startup diagnostic payload | Diagnostics panel shows backend health, renderer, memory/planning/critic/audit flags |
| Crash reports | Local exception hooks write atomic, redacted JSON reports with retention | Diagnostics panel shows enabled state, report count, and latest timestamp |
| Secrets status | `ANTHROPIC_API_KEY` / keyring; secret value never stored | AI panel shows cloud availability only, never secret material |
| Memory | `MemoryStore`, `MemoryGate`, `data/memory` | Memory drawer plus diagnostics memory flag |
| Planner/critic | Config flags `planning_enabled`, `critic_enabled` | Diagnostics panel shows both flags; audit/log panel shows planner/critic events |
| Autonomy suggestions | `autonomy_enabled`, `SUGGESTION` events | Conversation/log surface shows suggestions; diagnostics shows flag |

## Phase 4 Completed

| Capability | Implementation |
|---|---|
| Backend switching from the UI | Orchestrator validates and atomically replaces a healthy router; busy and unavailable-cloud changes are rejected |
| Persisted voice/persona settings | `data/user_settings.json` loads between machine config and environment; writes are validated and atomic |
| Audit JSONL load-more | Bridge pages real audit files, skips malformed lines, and withholds transcript/speech content from summaries |
| Crash report object model | `CrashReporter` captures process/thread failures with path/secret redaction and hook delegation |

## Phase 5 (in progress)

| Deliverable | Status |
|---|---|
| C — Embodied control skills | **Done**: Media/Volume/Window/Clipboard/Screenshot/Keyboard skills shipped; per-intent sensitivity, sandboxing, rules + LLM reachability, and contract tests are in place |
| A — Multi-step agentic execution | **Done**: planner decomposes compound commands into a bounded multi-step plan; `Executor` runs steps through the skill engine with per-step critic gating, one correlation id, per-step confirmation pause/resume, and abort-on-failure |
| B — Visual perception | Designed; needs a local vision model integration |
| D — Speaker identity & scoped authz | Designed; needs a speaker-embedding model |
| E — Streaming conversation & barge-in | Designed; needs token-streaming TTS handoff |
| F — Agent console UI | Designed; depends on A/B/D/E |

## Next Gaps

| Gap | Reason |
|---|---|
| Dedicated neural wake model | Current offline grammar-biased Vosk spotter is functional; a trained Axon detector would reduce acoustic false rejects further |
| Speaker verification and RBAC | Sensitive actions are confirmed and sandboxed, but identity-bound authorization is not implemented |
| Signed packaging and updater | Runtime is source-launched; MSI/MSIX signing and staged updates remain deployment work |
| Optional cloud TTS providers | Native SAPI5 is reliable and offline; policy-gated neural providers remain future integrations |
