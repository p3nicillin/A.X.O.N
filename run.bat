@echo off
REM Launch JARVIS. Uses a local venv if present, else the system Python.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py
) else (
    python run.py
)
pause
