# AXON Feature Inventory

This is the living checklist for wiring AXON capabilities into the AXON UI.
Every user-facing capability should have a visible/control surface here unless
it is a first-install or machine-policy concern.

| Capability | Current surface today | AXON UI surface |
|---|---|---|
| `TimeDateSkill` | Manifest: `get_time`, `get_date`; enabled by discovery; not sensitive | Skills panel shows intents/parameters/enabled state; real toggle updates registry state |
| `AppLauncherSkill` | Manifest: `open_app(app)`, `close_app(app)`; whitelist in handler; not sensitive | Skills panel shows intents, parameters, and whitelist; toggle updates registry state |
| `SystemInfoSkill` | Manifest: `system_info`; metrics read via `psutil` fallback | Skills panel plus live telemetry gauges |
| `WebSearchSkill` | Manifest: `web_search(query)`; opens DuckDuckGo/browser fallback; not sensitive | Skills panel shows intent/parameter; toggle updates registry state |
| `NotesSkill` | Manifest: `add_note(text)`, `read_notes`, `clear_notes`; local JSON notes | Skills panel plus memory drawer entries for stored durable facts |
| `FileSystemSkill` | Manifest: `list_files(path)`, `find_file(query)`, `open_folder(path)`; sensitive; sandbox root `data/workspace` | Skills panel shows sandbox root and sensitive flag; toggle updates registry state; confirmation remains orchestrator-gated |
| `MediaControlSkill` | Manifest: `play_pause`, `next_track`, `previous_track`; OS media keys via ctypes; not sensitive | Skills panel shows intents/enabled state; toggle updates registry state |
| `VolumeSkill` | Manifest: `volume_up(steps)`, `volume_down(steps)`, `mute_toggle`; OS volume keys via ctypes, steps clamped 1-10; not sensitive | Skills panel shows intents/parameters; toggle updates registry state |
| `WindowControlSkill` | Manifest: `minimize_window`, `maximize_window`, `restore_window`; foreground window via user32 ShowWindow; not sensitive | Skills panel shows intents/enabled state; toggle updates registry state |
| `ClipboardSkill` | Manifest: `read_clipboard`, `set_clipboard(text)`; PowerShell Get/Set-Clipboard; read returns a bounded preview only; not sensitive | Skills panel shows intents/parameters; toggle updates registry state |
| AI backend: local | `[ai] engine`, `[ai.local]`; Ollama/llama.cpp/OpenAI-compatible runtime; health via router | AI panel shows active backend, fallback chain, model/health, latency and fallback metrics; mode control rebuilds the router live |
| AI backend: cloud | `[ai.cloud] enabled`, model, vendor key from env/secrets; off by default | AI panel shows configured health without exposing secrets |
| AI backend: rules | Guaranteed deterministic fallback and hybrid fast path | AI panel shows fallback-to-rules and fast-path counts |
| Visual state: idle | `AxonState.IDLE`, event `STATE_CHANGED` | Core status text and diagnostics state |
| Visual state: listening | `SPEECH_START`, wake/audio events | Core status text, mic chip, waveform/amplitude |
| Visual state: thinking | AI worker sets `AxonState.THINKING` | Core reasoning animation/status |
| Visual state: speaking | TTS sets `SPEAK_START`/`SPEAK_LEVEL` | Core status plus TTS amplitude |
| Visual state: error | Failed execution sets `AxonState.ERROR` | Core alert state |
| Wake word | `wake_word`, aliases, `require_wake_word`, wake spotter | Diagnostics panel shows wake word/required state; status bar reflects listening |
| Mic on/off | Audio input `set_enabled`; web mic button | Bottom mic control, waveform, diagnostics mic availability |
| Barge-in / Esc | TTS interrupt path in renderer affordances | Existing renderer control remains; listed in diagnostics |
| Dev input | Hidden developer text input/F2 affordance | Bottom input remains developer affordance, not primary chat surface |
| Follow-up/active listen | `active_listen_timeout` after wake spotter | Diagnostics panel lists timeout/config state |
| Sensitive confirmation | Orchestrator `_pending` and confirmation resolver | Spoken prompt plus audit/log panel record of confirmation decision |
| TTS backend/voice/rate | `tts_voice`, `tts_rate`, native SAPI5/pyttsx3 fallback | Voice panel selects installed voices and applies rate live; settings persist atomically |
| Persona/address style | `wake_ack_phrase`, `address_term`, AXON identity | Voice panel edits and persists persona settings and wake requirement |
| Audit trail | Hash-chained JSONL in `data/logs/audit-YYYYMMDD.jsonl` | Audit panel live-tails events and pages scrubbed historical summaries newest-first |
| Structured app log | Rotating `data/logs/AXON.log` | Audit panel live-tails `Event.LOG` entries |
| Metrics | Router `_Metrics`; system metrics via `system_info` | AI panel and telemetry gauges show bounded snapshots |
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
| C — Embodied control skills | **Done**: Media/Volume/Window/Clipboard skills shipped, manifest-driven, rules fast-path + LLM reachable, contract-tested |
| A — Multi-step agentic execution | Designed in `docs/MASTER_PROMPT_PHASE5.md`; planner still single-step |
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
