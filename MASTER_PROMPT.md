# AXON — Enterprise Master Prompt

> The implemented next-phase execution specification is
> [`docs/MASTER_PROMPT_PHASE4.md`](docs/MASTER_PROMPT_PHASE4.md).

> Feed this document to a principal-level engineering agent to evolve the
> AXON v1 prototype into a hardened, enterprise-grade, voice-driven AI
> operating layer for Windows. It assumes the existing modular architecture
> (perception → AI core → skill engine → audio → visual, wired through a
> thread-safe `EventBus`) and extends it. Preserve all existing safety rules
> and the "AI never acts, only emits intent" boundary.

---

## 0. Mission

Transform AXON from a single-user desktop prototype into an
**enterprise-deployable assistant platform**: secure, observable, governable,
testable, updatable, and supportable at fleet scale — without sacrificing the
sub-2-second perceived latency or the immersive holographic experience.

The assistant must:
* **Only activate on the wake word "AXON."** No audio is acted on, retained,
  or transcribed-for-action until the wake word is detected. This is both a UX
  and a privacy/compliance requirement.
* Speak with a **British "AXON" voice** by default (configurable per user).
* Treat every action as **explicit, authorized, audited, and reversible where
  possible**.

---

## 1. Non-negotiable principles (carry forward)

1. **Capability boundary** — the AI core emits structured `IntentPacket`s only.
   It never executes. The skill engine is the sole action surface.
2. **Least privilege** — every skill declares its capabilities in a manifest
   and runs with the minimum permissions required.
3. **Graceful degradation** — any missing dependency, model, or network path
   degrades a feature, never crashes the system.
4. **Everything observable** — every utterance, intent, action, and response is
   logged to an immutable audit trail.
5. **Privacy by default** — local-first processing; no audio leaves the device
   unless a policy explicitly allows it.

---

## 2. Wake-word activation (hard requirement)

* Implement an **always-on, low-power wake-word spotter** ("AXON") that runs
  before VAD/STT. Recommended: **openWakeWord** (Apache-2.0, no cloud) or
  **Porcupine** (commercial license). The current post-STT keyword gate is the
  fallback only.
* State machine: `DORMANT → (wake word) → ACTIVE-LISTEN (bounded window, e.g.
  8 s) → capture command → process → return to DORMANT`.
* While `DORMANT`, audio is processed **only** by the wake-word detector and is
  never persisted or sent to STT-for-action.
* On wake: brief audible/visual acknowledgement ("Yes, sir?") + core transition
  to the listening animation.
* Configurable: wake word string/model, sensitivity, active-listen timeout,
  optional "follow-up mode" (stay active for N seconds after a response for
  back-to-back commands without re-triggering).
* Barge-in: speaking the wake word during TTS must interrupt and re-listen.

---

## 3. Voice & persona

* Default TTS voice: **British English** (SAPI5 "Hazel", or a neural voice such
  as Azure `en-GB-RyanNeural`/`en-GB-SoniaNeural` when a cloud TTS policy is
  enabled). Voice, rate, and pitch configurable per user profile.
* Persona: concise, formal, calm — addresses the user as "sir/ma'am" per
  profile. Persona prompt lives in config, not code.
* Pluggable TTS backends behind one interface: SAPI5 (offline default), Azure
  Neural, ElevenLabs — selected by policy. All must support **interruption**
  and emit amplitude for waveform sync.

---

## 4. Security & identity

* **Authentication**: optional Windows account / Azure AD (Entra ID) binding so
  the assistant knows *who* is speaking. Support **voice-profile speaker
  verification** to reject commands from unrecognized speakers for sensitive
  skills.
* **Authorization (RBAC)**: roles → allowed skill set. Sensitive skills
  (filesystem, app control, admin) gated by role + explicit spoken
  confirmation. Policy defined in signed config.
* **Secrets management**: no secrets in code or plaintext config. Integrate
  **Windows DPAPI / Credential Manager** locally and **Azure Key Vault** for
  managed fleets. `ANTHROPIC_API_KEY` and any cloud TTS keys load from there.
* **Skill sandboxing**: skills run with constrained capabilities; filesystem
  skill confined to an allow-listed workspace; no arbitrary process exec; app
  launcher remains whitelist-only. Consider running untrusted/marketplace
  skills in a separate low-integrity process (AppContainer) with brokered IPC.
* **Input safety**: validate every intent against a JSON schema before
  execution; reject unknown intent types/parameters (already enforced — keep).
* **Supply chain**: pin dependencies, generate an SBOM, sign releases, verify
  skill-package signatures before load.

---

## 5. Observability & audit

* **Audit trail** (compliance-grade): append-only JSONL of every transcript,
  intent, skill result, spoken response, confirmation decision, and error —
  with timestamp, session id, user id, and correlation id. Tamper-evident
  (hash-chained) and retained per policy with automatic pruning.
* **Structured app logging**: rotating logs with levels; redact PII.
* **Metrics**: latency per pipeline stage (wake→STT→intent→skill→TTS), skill
  success/error rates, wake-word false-accept/reject — exportable to
  OpenTelemetry / Prometheus.
* **Health**: `/health` self-test command and a startup diagnostic that reports
  each capability's status.
* **Crash reporting**: structured, opt-in, PII-scrubbed.

---

## 6. Reliability & performance

* Fully asynchronous, non-blocking UI (already). Add **timeouts, retries with
  backoff, and circuit breakers** around all I/O (cloud AI, web, TTS).
* Per-stage latency budget with telemetry; target <2 s perceived response.
* Watchdog that restarts a failed subsystem (mic stream, TTS worker) without
  killing the app.
* Bounded queues and backpressure on the event bus.

---

## 7. Configuration & deployment

* **Layered config**: defaults → machine policy (admin, signed) → user profile
  → env. Admin policy can lock settings (e.g. force wake word on, disable cloud
  TTS).
* **Packaging**: MSI/MSIX installer; optional **Windows Service** for the
  background voice runtime + a session UI process. Auto-start, auto-update
  (signed, staged rollout).
* **Multi-user**: per-user profiles (voice, persona, history, permissions) on
  shared machines.
* **Fleet management**: settings via Group Policy / Intune; centralized audit
  log shipping.

---

## 8. Extensibility — skill platform

* Versioned skill manifests with declared intents, capabilities, required
  permissions, and **author signature**.
* **Skill marketplace**: discovery, signed download, capability prompt on
  install, sandboxed execution, per-skill enable/disable and permission review.
* Stable skill SDK + golden-path template + contract tests every skill must
  pass.

---

## 9. Intelligence roadmap

* **Long-term memory**: encrypted local store of preferences + history with
  user-controlled retention and a "forget" command.
* **Desktop/context awareness**: active-window and selection context fed to the
  AI core (read-only, consented).
* **Multi-agent**: planner / executor / critic around the intent engine for
  complex multi-step tasks, with the executor still bounded by the skill engine.
* **Adaptive persona** and **visual evolution** of the core driven by usage —
  both policy-bounded and reversible.

---

## 10. Quality engineering

* **Test pyramid**: unit tests (skills, intent parsing, wake gate), integration
  tests (full pipeline with mocked audio/AI), and contract tests for skills.
* **CI/CD**: lint, type-check (mypy), test, SBOM, sign, package on every change.
* **Accessibility**: visual-only and audio-only operation modes; captions for
  TTS; high-contrast HUD.
* **Localization**: externalized strings; multi-locale STT/TTS.

---

## 11. Compliance

* Data inventory + retention policy (audio is transient by default; transcripts
  and audit retained per policy).
* GDPR/CCPA: data subject export & deletion (`export my data`, `forget
  everything`).
* Configurable data residency for any cloud features.

---

## 12. Acceptance criteria

* AXON ignores all speech until "AXON" is spoken, then acknowledges in a
  British voice and acts only on the following command.
* Every action produces an immutable audit record with user + correlation id.
* No secret is ever stored in plaintext.
* Removing any optional dependency degrades one feature and logs it; the app
  still launches and animates.
* Full test suite and startup diagnostic pass on a clean Windows 10/11 machine.
* p95 perceived response latency < 2 s for local skills.

---

### Build order (suggested)
1. Wake-word spotter + activation state machine + British voice default.
2. Audit trail + structured logging + startup diagnostic.   ← foundation
3. Secrets management (DPAPI/Key Vault) + RBAC + speaker verification.
4. Reliability (timeouts/retries/circuit breakers/watchdog) + metrics.
5. Packaging (MSI/Service) + layered/admin policy config.
6. Skill marketplace + SDK + contract tests.
7. Memory, context awareness, multi-agent, adaptive persona.

> Implement incrementally. After each step the system must still run
> end-to-end and pass the diagnostic. Never break the capability boundary.
