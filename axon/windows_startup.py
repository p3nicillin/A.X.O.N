"""Current-user Windows startup registration; never requires elevation."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "AXON"


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    launcher = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{Path(sys.executable).resolve()}" "{launcher.resolve()}"'


def startup_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, _VALUE_NAME)
        return bool(value)
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> bool:
    if os.name != "nt":
        raise OSError("Startup registration is only available on Windows")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ,
                              startup_command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass
    return startup_enabled() == enabled
