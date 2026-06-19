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
| AI backend: local | `[ai] engine`, `[ai.local]`; Ollama/llama.cpp/OpenAI-compatible runtime; health via router | AI panel shows active backend, fallback chain, model/health, latency and fallback metrics |
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
| TTS backend/voice/rate | `tts_voice`, `tts_rate`, SAPI/default fallback | Voice panel shows backend, voice, rate, wake ack, address term |
| Persona/address style | `wake_ack_phrase`, `address_term`, AXON identity | Voice panel shows editable-by-config persona settings |
| Audit trail | Hash-chained JSONL in `data/logs/audit-YYYYMMDD.jsonl` | Audit panel live-tails bounded command/log entries |
| Structured app log | Rotating `data/logs/AXON.log` | Audit panel live-tails `Event.LOG` entries |
| Metrics | Router `_Metrics`; system metrics via `system_info` | AI panel and telemetry gauges show bounded snapshots |
| `/health` diagnostic equivalent | `IntentRouter.health()` and startup diagnostic payload | Diagnostics panel shows backend health, renderer, memory/planning/critic/audit flags |
| Crash reports | No dedicated crash reporter yet; failures are logged/audited | Diagnostics panel marks logs directory as current failure surface |
| Secrets status | `ANTHROPIC_API_KEY` / keyring; secret value never stored | AI panel shows cloud availability only, never secret material |
| Memory | `MemoryStore`, `MemoryGate`, `data/memory` | Memory drawer plus diagnostics memory flag |
| Planner/critic | Config flags `planning_enabled`, `critic_enabled` | Diagnostics panel shows both flags; audit/log panel shows planner/critic events |
| Autonomy suggestions | `autonomy_enabled`, `SUGGESTION` events | Conversation/log surface shows suggestions; diagnostics shows flag |

## Current Gaps

| Gap | Reason |
|---|---|
| Backend switching from the UI | Backend router is built at startup; live backend replacement needs an orchestrator-owned rebuild path before it should be exposed as a control |
| Editing voice/persona values from the UI | Config persistence is not implemented yet; current UI surfaces values read-only |
| Full audit JSONL backfill/load-more | Live bounded tail is implemented; historical paging is still a follow-up |
| Crash report object model | Existing structured logs are surfaced; no separate crash-report schema exists yet |
