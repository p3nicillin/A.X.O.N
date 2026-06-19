"""Wake-word gate with fuzzy, mishearing-tolerant matching.

Small offline STT models routinely mistranscribe the uncommon proper noun
"AXON" (we observed it becoming "this", "javis", "jervis"…). A strict string
match therefore rejects most genuine activations. So we accept the wake word if
one of the first two tokens of the utterance:

  * equals the wake word, or
  * is in the configured alias list of known mishearings, or
  * is within ``wake_fuzzy_threshold`` similarity of the wake word.

This runs after STT (v1). The roadmap replaces it with a dedicated always-on,
grammar-biased spotter for even higher reliability.
"""
from __future__ import annotations

import difflib
import re

from ..config import Config


class WakeWord:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.required = config.require_wake_word
        self.word = config.wake_word.lower()
        self.aliases = {a.lower() for a in getattr(config, "wake_aliases", [])}
        self.threshold = float(getattr(config, "wake_fuzzy_threshold", 0.72))
        # Observed tail left when the spotter activates before the old wake
        # word has fully cleared the command recogniser.
        self.residues = {"his"}

    @staticmethod
    def _norm(token: str) -> str:
        return re.sub(r"[^a-z']", "", token.lower())

    def _is_wake(self, token: str) -> bool:
        token = self._norm(token)
        if not token:
            return False
        if token == self.word or token in self.aliases:
            return True
        return difflib.SequenceMatcher(None, token, self.word).ratio() >= self.threshold

    def strip(self, text: str) -> tuple[bool, str]:
        """Return (heard, command_without_wakeword).

        When the wake word is not required, always heard=True with the text
        passed through unchanged.
        """
        if not self.required:
            return True, text

        tokens = text.split()
        if not tokens:
            return False, text
        # the wake word leads the utterance; tolerate one filler token before it
        for i in range(min(2, len(tokens))):
            if self._is_wake(tokens[i]):
                return True, " ".join(tokens[i + 1:]).strip()
        return False, text

    def clean_spotter_command(self, text: str) -> str:
        """Remove wake-word audio captured at the start of a pre-gated command.

        This is only for transcripts emitted after the dedicated wake spotter
        has already fired. It must not be applied to typed commands.
        """
        tokens = text.split()
        for i in range(min(2, len(tokens))):
            token = self._norm(tokens[i])
            if self._is_wake(token) or token in self.residues:
                return " ".join(tokens[i + 1:]).strip()
        return text.strip()
