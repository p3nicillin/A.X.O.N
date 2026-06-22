"""AXON — a voice-driven, visually animated AI operating layer for Windows.

This package is organised into independent layers that communicate only through
the :class:`~AXON.core.event_bus.EventBus`. No layer imports another layer's
internals, which keeps every piece modular and independently testable:

    perception/  -> microphone, VAD, speech-to-text, wake word
    ai/          -> intent engine (Claude API or local fallback) + context
    skills/      -> plugin-based skill engine (router + sandboxed handlers)
    audio/       -> text-to-speech (interruptible)
    visual/      -> the reactive holographic "AXON CORE" renderer
    core/        -> event bus, state machine, orchestrator (the pipeline)
"""

__version__ = "1.5.0"
