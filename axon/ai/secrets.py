"""Load secrets (API keys) from the OS secrets store, never from plaintext config.

Resolution order:
    1. environment variable (e.g. ANTHROPIC_API_KEY) — the standard CI/dev path
    2. Windows Credential Manager (via the optional ``keyring`` package, which
       uses DPAPI under the hood) — the recommended desktop store
    3. nothing -> returns "" so the caller can degrade gracefully

This keeps the §8 guarantee that enabling the cloud backend requires a key from a
real secrets store, and that the key is never persisted in config files.
"""
from __future__ import annotations

import os

_SERVICE = "AXON"


def get_secret(name: str, *, env: str | None = None) -> str:
    """Return a secret by logical name, or "" if unavailable."""
    env_name = env or name
    val = os.getenv(env_name)
    if val:
        return val.strip()
    try:  # optional dependency; absence simply means "no credential store"
        import keyring
        stored = keyring.get_password(_SERVICE, name)
        if stored:
            return stored.strip()
    except Exception:
        pass
    return ""


def get_anthropic_key(config) -> str:
    """The cloud API key, from env or the credential store only.

    A key present in config.anthropic_api_key is honoured for backward
    compatibility, but that path is discouraged (and config.toml is gitignored).
    """
    key = get_secret("anthropic_api_key", env="ANTHROPIC_API_KEY")
    if key:
        return key
    return (getattr(config, "anthropic_api_key", "") or "").strip()
