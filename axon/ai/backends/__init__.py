"""Pluggable AI-core backends. Each turns a transcript into a schema-valid
IntentPacket or raises IntentBackendError — none of them ever execute anything.
"""
from __future__ import annotations

from .base import (CHAT_SPEC, UNKNOWN_SPEC, IntentBackend, IntentBackendError,
                   IntentSpec, all_specs, specs_from_catalogue)
from .cloud import CloudBackend
from .local_llm import LocalLLMBackend
from .rules import RuleBackend
from .runtime import LocalRuntime

__all__ = [
    "IntentBackend", "IntentBackendError", "IntentSpec",
    "all_specs", "specs_from_catalogue", "CHAT_SPEC", "UNKNOWN_SPEC",
    "RuleBackend", "CloudBackend", "LocalLLMBackend", "LocalRuntime",
]
