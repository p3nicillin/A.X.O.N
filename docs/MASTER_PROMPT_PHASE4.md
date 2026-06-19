# A.X.O.N - Master Prompt (Phase 4): Governed Runtime Control

## Role

Act as a principal Windows/Python engineer extending the existing AXON codebase.
Read the repository before changing it. Preserve its event-driven architecture,
local-first privacy model, native web UI, and current test behavior.

## Mission

Turn AXON's diagnostics-only control surfaces into a governed runtime console.
Settings must persist, supported AI backends must be switchable without process
restart, historical audit data must be inspectable, and uncaught failures must
produce safe structured crash reports. Every displayed value must come from the
running project; never add demo data.

## Non-Negotiable Boundaries

1. The AI layer emits `IntentPacket` objects only. Skills remain the sole action
   boundary.
2. Local processing is the default. Cloud remains explicit opt-in and cannot be
   selected without its policy flag and credential.
3. Wake word remains `Axon`; microphone commands are pre-gated and cleaned of
   wake-word residue before routing.
4. Secrets are never persisted, returned to JavaScript, logged, or included in
   crash reports.
5. Missing dependencies and malformed persisted data degrade safely.
6. Existing user configuration and unrelated working-tree changes are preserved.

## Deliverable A - Layered User Settings

Add a structured user-settings layer at `data/user_settings.json`:

`defaults -> config.toml -> user_settings.json -> environment`

Only explicitly allowlisted, non-secret settings may be persisted. Validate
types and ranges before mutating the live configuration. Use atomic replacement
for writes. The first control surface must support:

- TTS voice and rate
- address term and wake acknowledgement phrase
- wake-word-required toggle
- selected AI engine (`local`, `rules`, or eligible `cloud`)

Changes must apply to the next operation without restarting AXON. Environment
overrides remain authoritative and must be reported as locked if a UI update
attempts to replace them.

## Deliverable B - Runtime AI Backend Selection

Give the orchestrator an atomic backend-rebuild operation:

- reject changes while an intent is executing
- validate the requested engine
- reject cloud when disabled or unavailable
- build and health-check the replacement router before publishing it
- keep the old router if validation fails
- persist a successful selection
- publish a structured log event and refresh the UI

The AI panel must provide real mode controls and display the resulting active
backend, configured engine, model, health detail, fallback chain, and metrics.

## Deliverable C - Live Voice and Persona Controls

The Voice panel must use actual controls rather than read-only text:

- voice selector populated from installed SAPI voices
- numeric TTS rate control with a safe range
- address-term and wake-ack text fields
- wake requirement toggle
- explicit Apply command with success/error feedback

Native SAPI reconfiguration must occur on its owning worker thread or immediately
before the next utterance. Do not access a COM voice object from the UI thread.

## Deliverable D - Historical Audit Browser

Expose a bounded, read-only audit paging API over `audit-*.jsonl`:

- newest records first
- stable offset/limit pagination with a hard maximum page size
- tolerate malformed/truncated lines
- never modify audit files
- return only JSON-safe records

The Audit panel must combine live log/command data with historical records and a
Load More command. Show event type, timestamp, session, and a concise payload
summary without rendering raw secrets.

## Deliverable E - Structured Crash Reporting

Add an opt-in local crash reporter that installs process and thread exception
hooks while preserving Python's original hooks. Reports must:

- be one JSON object per file under `data/crashes/`
- include report id, timestamp, exception type, scrubbed message, scrubbed stack,
  process/thread metadata, AXON version, and session id
- redact likely API keys, bearer tokens, and home-directory paths
- never capture transcript history, environment variables, memory contents, or
  arbitrary locals
- be written atomically and never raise into the failing path
- respect retention limits

Expose crash count and last report metadata in Diagnostics. Do not add telemetry
upload.

## UI Requirements

- All values and options come from Python bridge payloads.
- Controls show pending, success, validation-error, locked, and unavailable states.
- Existing skill toggles remain functional.
- No mock values, simulated agents, fake history, or optimistic state that is not
  reconciled with a fresh bridge snapshot.
- Keep the current compact operational layout and avoid nested cards.

## Quality Gates

Add deterministic tests for:

- settings load precedence, validation, atomic persistence, and env locks
- live SAPI configuration handoff without COM access from the caller thread
- backend switch success, busy rejection, and unavailable-cloud rejection
- audit pagination order, bounds, and malformed-line tolerance
- crash redaction, atomic report shape, retention, and hook delegation
- bridge methods and payloads for every new control

## Acceptance Criteria

1. Restarting AXON retains UI-applied settings.
2. Switching local/rules changes the actual router and the next intent provenance.
3. Cloud cannot be enabled accidentally.
4. Voice/rate changes affect the next spoken response.
5. Audit history pages from real project files.
6. A synthetic uncaught exception test creates a scrubbed report surfaced in
   Diagnostics.
7. The full suite passes, `pip check` is clean, stderr is empty, and the native
   web build launches with Ollama, Vosk, SAPI, skills, and telemetry online.

## Completion Rule

Do not stop after writing this prompt. Implement every deliverable above, test it,
run the live build, inspect the UI, update `docs/FEATURE_INVENTORY.md`, and commit
the phase as one coherent change.
