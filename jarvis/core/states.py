"""The single source of truth for what JARVIS is *doing* right now.

The visual engine subscribes to these states and renders a distinct animation
for each. The orchestrator is the only component allowed to set them.
"""
from __future__ import annotations

from enum import Enum


class JarvisState(str, Enum):
    IDLE = "idle"            # slow rotation, soft pulse, ambient particles
    LISTENING = "listening"  # reactive pulse expansion, directional ripples
    THINKING = "thinking"    # faster orbital motion, neural flicker
    SPEAKING = "speaking"    # waveform sync, reactive energy spikes
    ERROR = "error"          # glitch scanlines, unstable particles

    def __str__(self) -> str:  # nicer HUD labels
        return self.value
