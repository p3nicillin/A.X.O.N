# A.X.O.N - Master Prompt (Phase 5): Agentic Execution & Embodied Perception

## Role

Act as a principal Windows/Python engineer extending the existing AXON codebase.
Read the repository before changing it. Phase 4 delivered a governed runtime
console; the reasoning scaffolding already exists (`axon/reasoning/planner.py`,
`axon/reasoning/critic.py`, `axon/autonomy/`, `axon/ai/context.py`, the
`planning_enabled` / `critic_enabled` / `autonomy_enabled` / `user_model_enabled`
flags). Your job is to make that scaffolding *do the work* and to give AXON
hands and eyes — without ever breaking the capability boundary.

## Mission

Turn AXON from a single-intent command-response assistant into a **bounded
autonomous agent**: it plans and executes multi-step goals through the skill
engine, perceives the active screen on explicit consent, controls more of the
real desktop through least-privilege skills, knows *who* is speaking, and
converses with streaming sub-second responses. Every new power lands inside the
existing safety model — the AI still only emits intent, skills are still the
sole action surface, and everything stays auditable, reversible, and local-first.

## Non-Negotiable Boundaries (carry forward, do not weaken)

1. The AI layer emits `IntentPacket` objects only. The planner *describes*; the
   executor runs **only** through `SkillRegistry`. Nothing in the AI/reasoning
   layer ever calls a side-effecting API directly.
2. Local processing is the default. Vision and any cloud model remain explicit
   opt-in, gated by a policy flag plus credential, and cannot be enabled by a
   spoken command alone.
3. Wake word remains `Axon`. No perception sensor (screen, mic, context) is read
   while `DORMANT`. Perceived frames/text are never persisted and are redacted
   from the audit trail.
4. Secrets and raw biometric audio are never persisted, returned to JavaScript,
   logged, or placed in crash reports.
5. Sensitive and destructive steps keep the orchestrator confirmation gate.
   Multi-step plans do not bypass it — each sensitive step is confirmed.
6. Missing dependencies (vision model, speaker model, screen API) degrade the
   feature and log it; the system still launches and animates.
7. Existing user configuration, settings precedence, and unrelated working-tree
   changes are preserved.

## Deliverable A — Multi-Step Agentic Execution

Promote the single-step planner into a real plan/execute/critique loop:

- `Planner.build` may emit an ordered, bounded `Plan` of `PlanStep`s for goals
  that decompose into several known intents. Cap step count and total wall-clock
  per plan.
- Add an **Executor** that runs steps sequentially through `SkillRegistry` only,
  threading one correlation id across the whole plan in the audit trail.
- The `Critic` gates **each** step before it runs (reusing the existing four
  dimensions). A block hard-stops the remaining plan.
- Abort-on-failure: a failed or blocked step stops the plan and reports cleanly.
  Where a step is reversible, record a compensating action and offer rollback.
- Each sensitive/destructive step routes through the existing confirmation gate
  individually; the plan pauses, speaks the prompt, and resumes on consent.
- This must run for real when `planning_enabled` is set — not as a flag-only
  no-op. Single-intent requests keep their current fast path unchanged.

## Deliverable B — Visual Perception (consented, read-only)

Give AXON eyes for read-only understanding, never for autonomous action:

- Add an optional screen/window capture sensor and a vision-capable model path
  (local first; cloud only behind its existing policy flag and credential).
- Captures occur only on an explicit spoken/UI consent for the current request
  ("what's on my screen", "read this", "summarize this window") and only while
  ACTIVE — never while `DORMANT`.
- Captured pixels and extracted text are handed to the AI core as **per-turn
  read-only context only**, never persisted, never sent to a cloud model unless
  the cloud policy is on, and redacted from audit summaries.
- The result can inform a spoken answer or seed a plan, but vision never directly
  triggers a side effect — any resulting action still flows intent → critic →
  confirmation → skill.
- Absent a vision model, the feature reports unavailable and degrades gracefully.

## Deliverable C — Expanded Embodied Control Skills

Add least-privilege desktop-control skills, each with a manifest, declared
intents/parameters, correct `sensitive` flag, and contract tests:

- `ClipboardSkill` — read/replace clipboard text (read is non-sensitive; write
  is sensitive).
- `MediaControlSkill` — play/pause, next/previous, via the OS media keys.
- `VolumeSkill` — set/adjust/mute system volume within a safe range.
- `WindowControlSkill` — focus/minimize/maximize/close the foreground or a named
  window (close is sensitive).
- `ScreenshotSkill` — capture to a file inside the sandbox workspace.
- `KeyboardSkill` — type text / send a keystroke (sensitive, confirmation-gated;
  never used to bypass another skill's gate).

Every new skill is discovered by the registry, schema-validated, and refuses
unknown/empty parameters. No skill performs arbitrary process exec.

## Deliverable D — Speaker Identity & Scoped Authorization

Let AXON know who is speaking and scope power accordingly:

- Voice-profile **enrollment** ("learn my voice") that stores a local speaker
  embedding only — never the raw audio.
- On each command, compute a speaker match score. A recognized speaker keeps
  full capability; an **unrecognized** speaker is downgraded to read-only,
  non-sensitive skills.
- Sensitive, destructive, and multi-step agentic actions require a recognized
  speaker in addition to the confirmation gate.
- Enrollment, matching, and the downgrade decision are auditable; the active
  speaker is surfaced to the UI. Absent the speaker model, fall back to current
  behavior and log that identity is unavailable.

## Deliverable E — Streaming Conversation & Robust Barge-In

Make AXON feel instantaneous and interruptible:

- Stream tokens from the local LLM and feed incremental, sentence-chunked TTS so
  first audio is sub-second; emit amplitude for the waveform throughout.
- Barge-in: speaking the wake word or pressing Esc cuts the current utterance
  mid-stream and returns to ACTIVE-LISTEN immediately, on the owning worker
  thread (no COM access from the UI thread — keep the Phase-4 handoff rule).
- Follow-up window: after a response, stay ACTIVE for the configured
  `active_listen_timeout` so back-to-back commands need no re-wake.
- Record per-stage latency (wake → STT → intent → plan → skill → first-audio) in
  the existing metrics and surface a p95 in Diagnostics.

## Deliverable F — Agent Console (UI)

Extend the native web UI, drawing every value from Python bridge payloads:

- A live **plan view**: ordered steps with per-step status (pending / running /
  done / blocked / confirmed), critic verdict, and the plan correlation id.
- A **perception consent** indicator showing when the screen sensor is active and
  what was captured this turn (type only, never raw content).
- A **speaker identity** chip (recognized / unknown / enrolling) and the active
  capability scope.
- A **streaming transcript** that renders partial responses as they arrive and
  reconciles to the final text.
- Controls expose pending, success, validation-error, locked, and unavailable
  states. No mock plans, fake speakers, simulated perception, or optimistic
  state left unreconciled with a fresh bridge snapshot. Keep the compact
  operational layout; no nested cards.

## Quality Gates

Add deterministic tests for:

- multi-step plan construction, bounded size/timeout, per-step critic gating,
  abort-on-failure, and one-correlation-id audit threading;
- sensitive step inside a plan still triggers the confirmation gate;
- vision context is per-turn only, never persisted, and redacted from audit;
  unavailable vision model degrades safely;
- each new control skill: manifest contract, intent routing, parameter
  validation, and refusal of empty/unknown input;
- speaker enrollment stores an embedding (not audio), unknown-speaker downgrade,
  and sensitive-action gating on identity;
- streaming chunker ordering, barge-in cancels mid-utterance on the worker
  thread, and follow-up window timing;
- bridge methods and payloads for plan view, perception consent, speaker chip,
  and streaming transcript.

## Acceptance Criteria

1. A spoken multi-step goal produces a visible plan, runs each step through the
   skill engine, gates sensitive steps, and aborts cleanly on failure.
2. "What's on my screen" answers from a consented, non-persisted capture; with no
   vision model it reports unavailable without crashing.
3. The new control skills work end-to-end and refuse malformed intents.
4. An unrecognized speaker cannot trigger a sensitive or agentic action.
5. First spoken audio begins sub-second and the wake word interrupts it
   mid-utterance.
6. The Agent Console reflects live plan, perception, speaker, and streaming state
   with no mock data.
7. The full suite passes, `pip check` is clean, stderr is empty, and the native
   web build launches with Ollama, Vosk, SAPI, skills, telemetry, planning,
   autonomy, and (when present) vision and speaker models online.

## Completion Rule

Do not stop after writing this prompt. Implement every deliverable above, test
it, run the live build, inspect the UI, update `docs/FEATURE_INVENTORY.md`, and
commit the phase as one coherent change. Never break the capability boundary:
the AI describes, the critic gates, the user confirms, and the skill engine acts.
