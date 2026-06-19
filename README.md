# J.A.R.V.I.S

A voice-driven, visually animated AI **operating layer** for Windows — not a
chatbot. There is no chat window. You speak (or type a dev command), JARVIS
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
| 👁️ Perception | `jarvis/perception` | mic capture, energy **VAD**, **STT** (Vosk), wake word |
| 🧠 AI core | `jarvis/ai` | transcript → **structured intent JSON** (Claude *or* offline rules). Never acts. |
| 🧩 Skill engine | `jarvis/skills` | plugin router + sandboxed skills (the only place actions happen) |
| 🎙️ Audio | `jarvis/audio` | interruptible **TTS**, word-synced amplitude |
| 🎨 Visual | `jarvis/visual` | the reactive holographic **JARVIS CORE** + HUD |
| ⚙️ Core | `jarvis/core` | event bus, state machine, **orchestrator** (the pipeline) |

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
J.A.R.V.I.S/
├─ run.py / run.bat            # launchers
├─ requirements.txt
├─ config.example.toml         # copy to config.toml to customise
├─ jarvis/
│  ├─ main.py                  # wires every layer together
│  ├─ config.py                # settings + paths
│  ├─ core/
│  │  ├─ event_bus.py          # thread-safe pub/sub
│  │  ├─ states.py             # JarvisState (idle/listening/thinking/speaking/error)
│  │  └─ orchestrator.py       # THE event pipeline + state machine
│  ├─ perception/
│  │  ├─ audio_input.py        # mic stream + energy VAD
│  │  ├─ stt.py                # Vosk speech-to-text
│  │  └─ wake_word.py          # "jarvis" gate
│  ├─ ai/
│  │  ├─ schema.py             # IntentPacket / Intent / SkillResult
│  │  ├─ intent_engine.py      # Claude backend + offline rule backend
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
cd c:\Users\lukem\source\repos\J.A.R.V.I.S

# (recommended) isolated environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# install everything (all optional — see notes below)
pip install -r requirements.txt
```

Then run:

```powershell
python run.py          # or: python -m jarvis   or double-click run.bat
```

The window opens immediately. The console banner tells you which capabilities
came online.

### Capability notes (install only what you want)
* **Microphone + VAD** — `pip install sounddevice numpy`
* **Speech-to-text** — `pip install vosk`, then a model (below)
* **Text-to-speech** — `pip install pyttsx3 pywin32` (uses Windows SAPI5)
* **Claude AI engine** — `pip install anthropic` and set a key (below). Without
  it, the offline rule-based engine is used automatically.
* **System gauges** — `pip install psutil`
* **Web search** — `pip install requests`

### Speech-to-text setup
1. Download a model from <https://alphacephei.com/vosk/models> — start with
   `vosk-model-small-en-us-0.15` (~40 MB).
2. Unzip it into `models/` so you have e.g.
   `models/vosk-model-small-en-us-0.15/`.
3. JARVIS auto-detects it (or set `stt_model_path` in `config.toml`).

### Enabling the Claude AI engine
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # or put it in config.toml
```
Defaults to `claude-haiku-4-5-20251001` (fast, low latency for intent parsing).

---

## 4. Using it

* **Speak** naturally (mic + STT installed): *"open notepad"*, *"what time is
  it"*, *"system status"*, *"note that I need milk"*, *"search for the speed of
  light"*.
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

Each skill is a folder under `jarvis/skills/` with a `manifest.json` (name,
version, declared intents, `sensitive` flag) and a `handler.py` exposing a
`SKILL` object implementing `can_handle()` / `execute()`.

| Skill | Intents | Notes |
|-------|---------|-------|
| TimeDate | `get_time`, `get_date` | |
| AppLauncher | `open_app`, `close_app` | **whitelisted** apps only |
| SystemInfo | `system_info` | also feeds the HUD gauges |
| WebSearch | `web_search` | instant answer + browser fallback |
| Notes | `add_note`, `read_notes`, `clear_notes` | local JSON |
| FileSystem | `list_files`, `find_file`, `open_folder` | **sandboxed** to `data/workspace`, read-only, `sensitive` |

**Add a skill:** copy a folder, edit `manifest.json` + `handler.py`, restart.
Discovery is automatic — no other file changes.

---

## 6. Safety (hard limits)

* The AI **cannot act** — it only emits intent; the skill engine acts.
* AppLauncher only runs **whitelisted** apps; FileSystem is confined to
  `data/workspace` with path-escape checks and has **no** write/delete intents.
* `sensitive` skills require spoken **confirmation** (`confirm_sensitive`).
* No credential access, no remote code execution, no hidden background actions.
* Every action is **logged and visible** in the HUD.

---

## 7. Future roadmap

* **Memory system** — long-term preferences + history (extend `ai/context.py`).
* **True wake-word spotter** — swap the post-STT gate for openWakeWord/Porcupine.
* **GPU visual core** — PySide6 + moderngl shader renderer behind the existing
  `CoreRenderer` interface; "visual evolution" that changes with usage.
* **Plugin marketplace** — manifests already version + declare capabilities;
  add signing + a download path.
* **Desktop awareness** — an active-window skill feeding context to the AI.
* **Multi-agent** — planner / executor / critic agents around the intent engine.
* **Adaptive personality** — tone/voice profiles in `config` + TTS selection.
```
