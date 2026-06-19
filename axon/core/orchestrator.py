"""The orchestrator: the canonical event pipeline.

    mic -> VAD -> STT -> [TRANSCRIPT]
        -> wake-word gate
        -> AI core (worker thread) -> [INTENT]
        -> skill engine (with sensitive-action confirmation)
        -> [SKILL_RESULT]
        -> TTS -> [SPEAK_*]
        -> back to IDLE

It owns the state machine and is the ONLY component that sets AxonState or
calls the skill engine. Heavy work (AI inference, skill execution) runs on a
worker thread so the mic callback and UI thread never block.
"""
from __future__ import annotations

import re
import threading

from ..ai.context import Context
from ..ai.intent_engine import build_engine
from ..ai.schema import Intent, IntentPacket, SkillResult
from ..config import DATA_DIR, MEMORY_DIR, Config
from ..memory import LocalEmbedder, MemoryGate, MemoryStore
from ..perception.wake_word import WakeWord
from ..reasoning import Critic, Planner
from ..skills.registry import SkillRegistry
from ..user_model import UserModel
from .event_bus import Event, EventBus
from .states import AxonState

_AFFIRMATIVE = re.compile(r"\b(yes|yeah|yep|do it|confirm|go ahead|sure|please do)\b")
_NEGATIVE = re.compile(r"\b(no|nope|cancel|stop|don'?t|never mind)\b")

# §3 STEP5: destructive / ambiguous intents that always require confirmation,
# independent of whole-skill sensitivity.
_DESTRUCTIVE_INTENTS = {"close_app", "clear_notes"}


class Orchestrator:
    def __init__(self, config: Config, bus: EventBus, registry: SkillRegistry,
                 tts, audio_input=None) -> None:
        self.config = config
        self.bus = bus
        self.registry = registry
        self.tts = tts
        self.audio_input = audio_input
        self.context = Context()
        self.wake = WakeWord(config)
        self.ai = build_engine(config, registry.catalogue(), bus)
        self.state = AxonState.IDLE
        self._pending: dict | None = None         # awaiting yes/no confirmation
        self._busy = threading.Lock()

        # §4 memory: episodic vault + semantic recall + the decision gate.
        self.memory: MemoryStore | None = None
        self.gate: MemoryGate | None = None
        if config.memory_enabled:
            self.memory = MemoryStore(
                MEMORY_DIR, LocalEmbedder(config.memory_embedding_dim))
            self.gate = MemoryGate(allow_secrets=config.memory_allow_secrets)

        # §5 planning + §7 critic: deliberate before executing.
        self.planner: Planner | None = Planner() if config.planning_enabled else None
        self.critic: Critic | None = None
        if config.critic_enabled:
            known = {it for m in registry.catalogue() for it in m.intents}
            self.critic = Critic(known, min_confidence=config.critic_min_confidence)

        # §17 user model: a persistent inferred profile that biases replies.
        self.user_model: UserModel | None = None
        if config.user_model_enabled:
            self.user_model = UserModel(DATA_DIR / "user_model.json")
            self.user_model.refresh_preferences(self.memory)

        bus.subscribe(Event.TRANSCRIPT, self._on_transcript)
        bus.subscribe(Event.SPEECH_START, self._on_speech_start)
        bus.subscribe(Event.SPEAK_END, self._on_speak_end)

        health = self.ai.health()
        self._log("info", f"AI core: active='{health['active']}' "
                          f"chain={health['chain'] or ['rules']}", source="ai")
        if health["active"] == "local":
            self._log("info", "AI core is LOCAL — transcripts stay on device.",
                      source="ai")
        elif health["active"] == "cloud":
            self._log("warn", "AI core is CLOUD — utterances leave the device "
                              "and are flagged in the audit trail.", source="ai")
        if self.memory is not None:
            self._log("info", f"Memory online: {self.memory.stats()['count']} "
                              "entries in vault.", source="memory")

    # -- state ---------------------------------------------------------------
    def set_state(self, state: AxonState) -> None:
        if state != self.state:
            self.state = state
            self.bus.publish(Event.STATE_CHANGED, state)

    def _log(self, level: str, message: str, source: str = "core") -> None:
        self.bus.publish(Event.LOG, {"level": level, "source": source,
                                     "message": message})

    # -- event handlers ------------------------------------------------------
    def _on_speech_start(self, _msg) -> None:
        if self.state in (AxonState.IDLE, AxonState.LISTENING):
            self.set_state(AxonState.LISTENING)

    def _on_speak_end(self, _msg) -> None:
        self.set_state(AxonState.IDLE)
        if self.audio_input is not None:
            self.audio_input.set_enabled(True)   # resume listening

    def _on_transcript(self, msg) -> None:
        payload = msg.payload or {}
        text = payload.get("text", "").strip()

        # The wake spotter already detected "AXON" and captured the command,
        # so these transcripts are pre-gated.
        if payload.get("wake_satisfied"):
            if payload.get("wake_only"):           # woke me, said nothing
                self.bus.publish(Event.WAKE_WORD)
                if self.config.acknowledge_wake:
                    self._respond(self.config.wake_ack_phrase)
                return
            if text:
                self.submit_text(text, bypass_wake=True)
            return

        # legacy path: apply the post-STT wake-word check
        if text:
            self.submit_text(text, bypass_wake=False)

    # -- public entry --------------------------------------------------------
    # Voice goes through the wake-word gate; the typed DEV INPUT bypasses it so
    # the system stays testable without saying "AXON" every time.
    def submit_text(self, text: str, bypass_wake: bool = True) -> None:
        text = text.strip()
        if not text:
            return

        # answering a pending sensitive-action confirmation? (no wake needed)
        if self._pending is not None:
            self._log("info", f"“{text}”", source="you")
            self._resolve_confirmation(text)
            return

        if bypass_wake or not self.wake.required:
            command = self.wake.strip(text)[1] if self.wake.required else text
        else:
            heard, command = self.wake.strip(text)
            if not heard:
                # surface the misheard text so the wake word can be tuned
                self._log("info", f"(ignored: “{text}” — no wake word)")
                return
            self.bus.publish(Event.WAKE_WORD)

        command = command.strip()
        if not command:                       # woke me, but said nothing useful
            if self.config.acknowledge_wake:
                self._respond(self.config.wake_ack_phrase)
            return

        self._log("info", f"“{command}”", source="you")
        threading.Thread(target=self._process, args=(command, True),
                         daemon=True).start()

    # -- §11 immutable command log ------------------------------------------
    def _command_log(self, wake: bool, command_type: str, intent_type: str,
                     skill_used: str, success: bool) -> None:
        self.bus.publish(Event.COMMAND_LOG, {
            "wake_detected": wake,
            "command_type": command_type,
            "intent": intent_type,
            "skill_used": skill_used,
            "success": success,
        })

    # -- pipeline core (worker thread) ---------------------------------------
    def _process(self, text: str, wake: bool = True) -> None:
        with self._busy:
            self.set_state(AxonState.THINKING)
            self._recall_into_context(text)
            self._profile_into_context()
            packet = self.ai.interpret(text, self.context)
            self.bus.publish(Event.INTENT, packet)
            self._log("debug",
                      f"intent via {packet.backend or '?'} "
                      f"({packet.model or 'n/a'}) {packet.latency_ms:.0f}ms "
                      f"conf={packet.confidence:.2f}"
                      + (" repaired" if packet.repaired else "")
                      + (" [CLOUD]" if packet.cloud_routed else ""),
                      source="ai")

            # §3 STEP3 classification (decided up front, then refined for gate)
            skill = self.registry.route(packet.intent) if packet.needs_skill else None
            requires_confirmation = bool(
                self.config.confirm_sensitive and skill is not None and (
                    skill.manifest.sensitive
                    or packet.intent.type in _DESTRUCTIVE_INTENTS))
            self._log("debug",
                      f"classify: {packet.classification(requires_confirmation)}",
                      source="ai")

            # §5 planning engine: a structured plan is generated for every
            # request before anything runs.
            plan = None
            if self.planner is not None:
                plan = self.planner.build(packet, skill, text)
                self._log("debug", f"plan: {plan.summary()}", source="planner")

            # conversational / UNKNOWN (no tool)
            if not packet.needs_skill:
                self._handle_unknown(packet, text, wake)
                return

            if skill is None:        # §2.4 don't pretend a capability exists
                self._respond("I'm afraid that function is unavailable, sir.",
                              error=True)
                self._command_log(wake, packet.command_type, packet.intent.type,
                                  "none", False)
                return

            # §7 critic: the last gate before execution. A block hard-stops.
            if self.critic is not None and plan is not None:
                verdict = self.critic.review(plan, packet, skill)
                self._log("info" if verdict.approved else "warn",
                          f"critic: {verdict.summary()}", source="critic")
                if not verdict.approved:
                    self._respond(self.critic.refusal_phrase(verdict),
                                  error=True)
                    self._command_log(wake, packet.command_type,
                                      packet.intent.type, skill.manifest.name,
                                      False)
                    return

            # §3 STEP5 safety gate
            if requires_confirmation:
                self._pending = {"intent": packet.intent, "wake": wake,
                                 "reply": packet.response_text}
                self._respond(self._confirm_question(packet.intent, skill))
                return

            self._execute(packet.intent, packet.response_text, text, wake)

    def _handle_unknown(self, packet: IntentPacket, text: str, wake: bool) -> None:
        # §2.3 / §8: unrecognised command -> clarify, or fall back to web if
        # policy allows. Greetings (intent "chat") just get a spoken reply.
        if (packet.intent.type == "unknown"
                and self.config.web_fallback_on_unknown):
            self._log("info", "UNKNOWN -> WEB_SEARCH fallback", source="ai")
            self._execute(Intent(type="web_search", parameters={"query": text}),
                          "", text, wake)
            return
        reply = packet.response_text or "I'm not sure I follow, sir."
        self._respond(reply)
        self.context.add(text, reply)
        self._consider_memory(text, packet.intent.type)
        self._command_log(wake, packet.command_type, packet.intent.type,
                          "none", True)

    def _execute(self, intent: Intent, ai_reply: str, source_text: str,
                 wake: bool) -> None:
        result: SkillResult = self.registry.execute(intent)
        self.bus.publish(Event.SKILL_RESULT, result)
        level = "info" if result.ok else "warn"
        self._log(level, f"{result.skill}: {result.summary}", source="skill")
        spoken = result.speak or ai_reply or result.summary
        self.context.add(source_text or intent.type, spoken)
        if result.ok:
            self._consider_memory(source_text, intent.type)
            if self.user_model is not None:        # §17 learn from the command
                self.user_model.observe(intent, result.ok)
        self._respond(spoken, error=not result.ok)
        self._command_log(wake, intent.command_type, intent.type,
                          result.skill, result.ok)

    def _confirm_question(self, intent: Intent, skill) -> str:
        if intent.type == "close_app":
            return f"Shall I close {intent.get('app', 'that application')}, sir?"
        if intent.type == "clear_notes":
            return "Shall I clear all of your notes, sir?"
        return f"That will use {skill.manifest.name}. Shall I proceed, sir?"

    def _resolve_confirmation(self, text: str) -> None:
        pending = self._pending
        self._pending = None
        intent: Intent = pending["intent"]
        wake: bool = pending["wake"]
        if _AFFIRMATIVE.search(text.lower()):
            self._log("info", "confirmation: approved", source="you")
            threading.Thread(target=self._execute,
                             args=(intent, pending.get("reply", ""), text, wake),
                             daemon=True).start()
        else:
            self._log("info", "confirmation: cancelled", source="you")
            self._respond("Very good, sir. I'll leave it.")
            self._command_log(wake, intent.command_type, intent.type,
                              "none", False)

    # -- §4 memory hooks -----------------------------------------------------
    def _recall_into_context(self, text: str) -> None:
        """Semantic recall (§4.3): surface durable facts relevant to this turn
        so the AI core can reason with them. Cleared each turn so stale memory
        never leaks forward."""
        if self.memory is None:
            self.context.set_recalled([])
            return
        hits = self.memory.recall(text, k=self.config.memory_recall_k,
                                  min_score=self.config.memory_min_score)
        self.context.set_recalled([e.content for e, _ in hits])
        if hits:
            preview = "; ".join(f"“{e.content}” ({s:.2f})" for e, s in hits)
            self._log("debug", f"recall: {preview}", source="memory")

    def _profile_into_context(self) -> None:
        """§17: bias the AI core with a short profile summary for this turn."""
        hint = self.user_model.hint_for_ai() if self.user_model else ""
        self.context.set_user_hint(hint)

    def _consider_memory(self, source_text: str, intent_type: str) -> None:
        """Run the §4.2 decision gate on a completed turn and persist if durable.
        The gate — not the orchestrator — decides; we only act on its verdict."""
        if self.memory is None or self.gate is None:
            return
        decision, entry = self.gate.consider(source_text, intent_type)
        if decision.store and entry is not None:
            self.memory.remember(entry)
            self._log("info", f"remembered [{entry.type}] {entry.content}",
                      source="memory")
            # a new preference may change the §17 profile — refresh it
            if self.user_model is not None and entry.type == "preference":
                self.user_model.refresh_preferences(self.memory)
        else:
            self._log("debug", f"not stored — {decision.reason}", source="memory")

    def _respond(self, text: str, error: bool = False) -> None:
        self.set_state(AxonState.ERROR if error else AxonState.SPEAKING)
        if self.audio_input is not None:
            self.audio_input.set_enabled(False)   # don't transcribe our own voice
        self.tts.speak(text)
