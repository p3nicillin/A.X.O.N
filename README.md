# A.X.O.N

A voice-driven, visually animated AI **operating layer** for Windows — not a
chatbot. There is no chat window. You speak (or type a dev command), AXON
interprets intent, routes it to a sandboxed **skill**, speaks the result, and a
reactive holographic **core** animates the whole time.

```
  mic ─▶ VAD ─▶ STT ─▶ AI core (intent) ─▶ skill engine ─▶ TTS
                                  │                  │
                                  ▼                  ▼
                          structured JSON     reactive visual core
```

---

## 1. Architecture

Every layer is independent and communicates **only** through a thread-safe
`EventBus`. No layer reaches into another's internals, so each is modular and
independently testable.

| Layer | Package | Responsibility |
|-------|---------|----------------|
| 👁️ Perception | `axon/perception` | mic capture, energy **VAD**, **STT** (Vosk), wake word |
| 🧠 AI core | `axon/ai` | transcript → **structured intent JSON** via a pluggable backend chain (local LLM → optional cloud → rules). Never acts. |
| 🧩 Skill engine | `axon/skills` | plugin router + sandboxed skills (the only place actions happen) |
| 🎙️ Audio | `axon/audio` | interruptible **TTS**, word-synced amplitude |
| 🎨 Visual | `axon/visual` | the reactive holographic **AXON CORE** + HUD |
| ⚙️ Core | `axon/core` | event bus, state machine, **orchestrator** (the pipeline) |

**Hard rule:** the AI core may only emit an `IntentPacket`. The orchestrator
routes it to the skill engine. The AI never executes anything itself.

### Why this stack
Pure-Python, single process — the whole pipeline runs without an IPC bridge to
break, which is what makes it run end-to-end on day one. The visual core uses
Tkinter (ships with Python, zero install, runs on Python 3.14 where GPU/Unity
wheels don't exist yet) but lives behind a tiny renderer interface
(`set_state` / `push_audio` / `push_speak` / `step`) so a PySide6 + moderngl
**shader** renderer can drop in later without touching any other layer.

Every heavy dependency is **optional and guarded** — the app launches and
animates with nothing installed, then lights up capabilities as you add them.

---

## 2. Folder structure

```
A.X.O.N/
├─ run.py / run.bat            # launchers
├─ requirements.txt
├─ config.example.toml         # copy to config.toml to customise
├─ axon/
│  ├─ main.py                  # wires every layer together
│  ├─ config.py                # settings + paths
│  ├─ core/
│  │  ├─ event_bus.py          # thread-safe pub/sub
│  │  ├─ states.py             # AxonState (idle/listening/thinking/speaking/error)
│  │  └─ orchestrator.py       # THE event pipeline + state machine
│  ├─ perception/
│  │  ├─ audio_input.py        # mic stream + energy VAD
│  │  ├─ stt.py                # Vosk speech-to-text
│  │  └─ wake_word.py          # "AXON" gate
│  ├─ ai/
│  │  ├─ schema.py             # IntentPacket / Intent / SkillResult
│  │  ├─ intent_engine.py      # builds the backend chain + rule engine
│  │  ├─ router.py             # selection, fallback chain, breaker, metrics
│  │  ├─ backends/             # local LLM / cloud / rules backends + runtime
│  │  └─ context.py            # rolling conversation memory
│  ├─ skills/
│  │  ├─ base.py               # Skill ABC + manifest
│  │  ├─ registry.py           # discovery + router (sandbox)
│  │  ├─ app_launcher/         # ─┐
│  │  ├─ web_search/           #  │ each: manifest.json + handler.py
│  │  ├─ system_info/          #  │
│  │  ├─ time_date/            #  │
│  │  ├─ notes/                #  │
│  │  └─ file_system/          # ─┘ (restricted, sandboxed, sensitive)
│  ├─ audio/
│  │  └─ tts.py                # interruptible SAPI5 TTS
│  └─ visual/
│     ├─ core_widget.py        # the holographic core renderer
│     └─ main_window.py        # HUD + frame loop
├─ models/                     # drop a Vosk model here
└─ data/                       # notes, file-system workspace, logs
```

---

## 3. Setup (Windows 10/11)

```powershell
cd c:\Users\lukem\source\repos\A.X.O.N

# (recommended) isolated environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# install everything (all optional — see notes below)
pip install -r requirements.txt
```

Then run:

```powershell
python run.py          # or: python -m axon   or double-click run.bat
```

The window opens immediately. The console banner tells you which capabilities
came online.

### Migration note
AXON replaces the former JARVIS package and product name. Use `python -m axon`
instead of `python -m jarvis`, `axon/` instead of `jarvis/`, and `AXON_*`
environment variables instead of `JARVIS_*`. A one-release compatibility shim
still reads legacy `JARVIS_*` variables when the matching `AXON_*` variable is
absent and prints a deprecation notice. Vendor keys such as
`ANTHROPIC_API_KEY` are unchanged.

### Capability notes (install only what you want)
* **Microphone + VAD** — `pip install sounddevice numpy`
* **Speech-to-text** — `faster-whisper` transcribes commands locally while a
  small Vosk model handles the wake word. `auto` falls back to full Vosk.
* **Text-to-speech** — `pip install pyttsx3 pywin32` (uses Windows SAPI5)
* **Local LLM core (default, free)** — install [Ollama](https://ollama.com) and
  run `ollama pull llama3.2:3b`. No API key, nothing leaves the device.
* **Cloud AI engine (optional)** — `pip install anthropic`, set `[ai] engine`
  and `[ai.cloud] enabled = true`, and provide a key (below). Off by default.
* **Secrets store (optional)** — `pip install keyring` to load the cloud key
  from the Windows Credential Manager instead of an env var.
* **System gauges** — `pip install psutil`
* **Web search** — `pip install requests`
* **In-app weather** — key-free Open-Meteo current conditions and forecasts;
  set `weather_default_location` when you want a default other than London.

### Speech-to-text setup
1. Download a wake model from <https://alphacephei.com/vosk/models> — start with
   `vosk-model-small-en-us-0.15` (~40 MB).
2. Unzip it into `models/` so you have e.g.
   `models/vosk-model-small-en-us-0.15/`.
3. AXON auto-detects it. On first run, faster-whisper downloads `small.en` into
   `models/whisper`; set `stt_engine = "vosk"` to use the previous backend.

The Voice panel provides personal transcript adaptation. Add a recurring
mishearing and its intended phrase (for example, `ma is` → `what is`). AXON
applies it to future transcripts and stores only text corrections in
`data/speech_profile.json`; raw enrollment audio is never retained.

### The AI core — free & local by default
A.X.O.N parses intent with a **local LLM on your own machine**. The entire
pipeline runs at **zero recurring cost with no API key**, and with the local
core **no transcript or audio ever leaves the device** — the startup diagnostic
states `AI core: LOCAL`. The core is a pluggable backend chain:

```
[rule fast-path] → local LLM → (optional cloud) → rules     # rules is the floor
```

* **`engine = "local"`** (default) — Ollama / llama.cpp / any OpenAI-compatible
  local server (`[ai.local]` in config). Output is **schema-constrained**, then
  validated → repaired → and, only if all else fails, it falls back to the
  deterministic rule engine. Malformed model output never reaches a skill.
* **`engine = "rules"`** — no LLM at all; pure deterministic parsing.
* **`engine = "cloud"` / `auto`** — opt-in Claude. Enabling it is a privacy
  change: every cloud-routed utterance is flagged in the audit trail.

If no local runtime is found the app still launches and animates, prints a
one-time setup guide, and runs on rules until you install one.

**Choosing a local model (all open-weight, all free):**

| Tier | Hardware | Suggested `[ai.local] model` |
|------|----------|------------------------------|
| Baseline (anywhere) | CPU-only / ≤4 GB | `llama3.2:3b`, `gemma3:2b` |
| Recommended | ~8 GB VRAM | `qwen3:7b`, `mistral-small`, `llama3.1:8b` |
| Strong reasoning | 12–16 GB VRAM | `phi-4` (14B) |
| Enthusiast | 24 GB+ VRAM | a 30B-class model at Q4_K_M |

Use **Q4_K_M** quantization (the common default) to halve VRAM with minimal
quality loss. p95 perceived latency for local skills stays **< 2 s** on the
reference machine; the rule fast-path answers simple commands instantly.

### Enabling the optional cloud (Claude) engine
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."          # never store keys in config.toml
$env:AXON_AI_CLOUD_ENABLED = "true"
$env:AXON_AI_ENGINE = "cloud"                # or "auto" (cloud → local → rules)
```
The key is read only from the environment or your OS credential store.

### Governed runtime controls

The native AXON UI can switch between healthy local and deterministic rules
backends without restarting. Voice, rate, address style, wake acknowledgement,
and wake enforcement are validated and saved to `data/user_settings.json`.
Environment overrides remain locked and take precedence. The Audit panel pages
real hash-chained history, while Diagnostics reports local redacted crash
artifacts from `data/crashes/`.

---

## 4. Using it

* **Speak** naturally (mic + STT installed): *"open notepad"*, *"what time is
  it"*, *"what is the weather"*, *"set a timer for 10 minutes"*, *"what is my
  active window"*, *"research local speech recognition"*, *"what is on my
  screen"*, *"reopen the closed tab"*, or *"write hello to file notes.txt"*.
* **No mic?** Type the same phrases in the **DEV INPUT** box and press Enter.
  (This is a developer affordance, not a chat UI — hide it with **F2**.)
* **Esc** interrupts speech (barge-in). **F2** toggles the dev input.

### Visual states
| State | Core behaviour |
|-------|----------------|
| Idle | slow rotation, soft pulse, ambient particles |
| Listening | reactive pulse expansion + live waveform from your voice |
| Thinking | faster spin, neural flicker, violet shift |
| Speaking | waveform synced to TTS, energy spikes |
| Error | red shift, glitch jitter, unstable particles |

---

## 5. Skills

Each skill is a folder under `axon/skills/` with a `manifest.json` (name,
version, declared intents, skill/intent sensitivity) and a `handler.py` exposing a
`SKILL` object implementing `can_handle()` / `execute()`.

| Skill | Intents | Notes |
|-------|---------|-------|
| TimeDate | `get_time`, `get_date` | |
| AppLauncher | `open_app`, `close_app` | named Windows applications; closing requires confirmation |
| Browser | `open_website`, `search_browser`, `open_browser`, `browser_action` | validated navigation plus verified-foreground tab/history/download controls |
| BrowserAutomation | `browser_navigate`, `browser_read_page`, `browser_click`, `browser_fill`, `browser_close_managed` | isolated Playwright browser; grounded element IDs, confirmation-gated mutations, state verification, and private-network blocking |
| WorkflowControl | `list_workflows`, `resume_workflow`, `cancel_workflow` | atomic per-step checkpoints with privacy-redacted recovery data |
| NativeAutomation | `desktop_inspect`, `desktop_click`, `desktop_fill` | isolated Windows accessibility grounding (`u*`) plus Win32 fallback (`n*`), with guarded and verified actions |
| SystemInfo | `system_info` | also feeds the HUD gauges |
| System awareness | `list_running_apps`, `network_status` | running process names and local interface/IP status remain inside AXON |
| WebSearch | `web_search`, `research_web`, `read_webpage` | sourced results and bounded public-page text stay inside AXON; no browser fallback |
| Weather | `get_weather` | current conditions/forecast remain inside AXON; no browser or API key |
| Calculator | `calculate` | safe local arithmetic/functions; no code execution |
| Notes | `add_note`, `read_notes`, `clear_notes` | local JSON |
| Reminders | `set_timer`, `set_reminder`, `list_reminders`, `cancel_reminder` | persistent scheduling, task centre, spoken alerts, and optional native toast |
| FileSystem | `list_files`, `find_file`, `read_file`, `write_file`, `create_folder`, `move_path`, `delete_path`, `open_folder` | sandboxed to `data/workspace`; mutations require confirmation |
| MediaControl | `play_pause`, `next_track`, `previous_track` | bounded OS media keys |
| VolumeControl | `volume_up`, `volume_down`, `mute_toggle` | adjustment steps are clamped |
| WindowControl | `get_active_window`, `list_windows`, `focus_window`, `minimize_window`, `maximize_window`, `restore_window`, `close_window` | foreground/open-window awareness and control; graceful close requires confirmation |
| Clipboard | `read_clipboard`, `set_clipboard` | writes require confirmation; reads return a bounded preview |
| Screenshot | `capture_screenshot`, `inspect_screen` | confirmed sandbox capture or ephemeral local Gemma 3 analysis with OCR fallback; inspection is never persisted |
| Keyboard | `type_text`, `send_keystroke` | bounded, allow-listed, and always confirmed |

**Add a skill:** copy a folder, edit `manifest.json` + `handler.py`, restart.
Discovery is automatic — no other file changes.

---

## 6. Safety (hard limits)

* The AI **cannot act** — it only emits intent; the skill engine acts.
* FileSystem is confined to `data/workspace` with path-escape checks, bounded
  text reads/writes, atomic replacement, and non-recursive deletion;
  screenshots cannot escape that workspace.
* Sensitive skills and individual mutating intents require spoken
  **confirmation** (`confirm_sensitive`).
* No credential access, no remote code execution, no hidden background actions.
* Every action is **logged and visible** in the HUD.
* Webpage reading rejects private/local addresses, redirects and responses over
  1 MB. Browser controls work only when a supported browser is foreground.
* Voice audio training samples are off by default, remain under
  `data/voice_samples`, and can be deleted from the Voice panel. Phrase
  corrections immediately bias transcription; WAV collection prepares a local
  fine-tuning dataset but does not claim to retrain Whisper by itself.

General explanations, coding questions, writing help, and advice use the local
model's tool-less `answer` intent and remain in AXON. Live or actionable requests
still route through a declared skill.

---

## 7. Windows release build

Run `scripts/build_release.ps1` to execute tests and build `dist/AXON/AXON.exe`
with PyInstaller. If `AXON_SIGN_CERT_PATH` and `AXON_SIGN_CERT_PASSWORD` are
provided, the script signs the executable. Installing Inno Setup also produces
the versioned installer from `installer/AXON.iss`.

Tagged GitHub releases run the same clean Windows build. Signing occurs only
when the repository has the certificate secrets; AXON never fabricates a signed
status. The Diagnostics panel can check the release feed, but downloads and
installation always require an explicit user action.

Packaged builds keep mutable data and downloaded speech models under
`%LOCALAPPDATA%\AXON`; source checkouts continue using the repository `data/`
and `models/` directories.

### v1.4 capability setup

Run `scripts/setup_v14_capabilities.ps1` once to install Playwright Chromium and
pull the local `gemma3:4b` vision model. Then set `vision_enabled = true` in
`config.toml`. Screen images are sent only to the configured loopback Ollama
endpoint and are never persisted by `inspect_screen`.

The managed browser is separate from personal browser profiles. It blocks
private/local network destinations and downloads; clicks and form filling pass
through AXON's spoken confirmation gate. Example commands include “open
https://example.com in the managed browser”, “read the current page”, “click
Sign in in the managed browser”, and “fill email with … in the managed browser”.

Run `python scripts/benchmark_commands.py --minimum 0.98` to execute the
checked-in command corpus. CI requires at least 98% accuracy and publishes the
latency/miss report for every change.

### v1.5 closed-loop execution

The managed browser now returns visible interactive elements with stable IDs
(`e1`, `e2`, …). Guarded click and fill actions can use those IDs, and every
mutation verifies the resulting URL, page state, expected text, or field value
before reporting success. This makes browser operation an observe → act →
verify loop instead of a fire-and-forget input simulation.

Compound commands are checkpointed atomically in `data/workflows.json` after
every successful step. Say “list interrupted workflows”, “resume the latest
workflow”, or “cancel workflow <id>” to manage recovery. Free-form form values
and other private text parameters are redacted; workflows containing redacted
inputs deliberately cannot auto-resume.

### v1.6 native application control

AXON can now inspect HWND-backed controls in the foreground Windows
application, assigning bounded IDs (`n1`, `n2`, …), roles, labels and screen
bounds. “Click desktop control n3” and “fill native control n2 with …” are
confirmation-gated and must produce an observable verified result. Every
Windows message uses an abort-if-hung timeout, the active application must
still match the inspected snapshot, and the worker is closed during shutdown.

The release workflow also avoids illegal direct secret references in step
conditions, preventing the pre-job GitHub Actions failures that generated
failure emails after otherwise successful pushes.

### v1.7 modern Windows accessibility

Native inspection now combines Microsoft UI Automation controls (`u1`, `u2`,
…) with the existing HWND-backed fallback (`n1`, `n2`, …). WPF, WinUI and
other accessible applications expose semantic roles, labels, bounds, supported
click/fill patterns and protected-field status. AXON uses only safe UIA
patterns—Value, Invoke, Selection, Toggle and Expand/Collapse—and still
requires confirmation plus a verified post-action state change.

UI Automation runs in a fresh hidden Windows PowerShell process for each
operation. Requests travel as JSON over stdin, private values never appear in
the command line, and a hard timeout terminates a stalled provider. The main
AXON process therefore remains responsive even when an application's
accessibility implementation is defective.

## 8. Future roadmap

* **Low-latency response streaming** — stream conversational output into the UI
  and sentence-level TTS while retaining complete structured intent validation.
* **True wake-word spotter** — swap the post-STT gate for openWakeWord/Porcupine.
* **GPU visual core** — PySide6 + moderngl shader renderer behind the existing
  `CoreRenderer` interface; "visual evolution" that changes with usage.
* **Plugin marketplace** — manifests already version + declare capabilities;
  add signing + a download path.
* **Speaker identity** — local voice embeddings and capability-scoped authz.
* **Adaptive personality** — tone/voice profiles in `config` + TTS selection.
```
