"""Capture a screenshot to the AXON workspace, never to an arbitrary path."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ...ai.schema import Intent, SkillResult
from ...config import DATA_DIR
from ..base import Skill

SCREENSHOT_DIR = DATA_DIR / "workspace" / "screenshots"
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,100}\Z")
_RESERVED_STEMS = {"con", "prn", "aux", "nul",
                   *(f"com{i}" for i in range(1, 10)),
                   *(f"lpt{i}" for i in range(1, 10))}


class ScreenshotSkill(Skill):
    def __init__(self) -> None:
        self.config = None

    def configure(self, config) -> None:
        self.config = config

    def vision_status(self) -> dict:
        enabled = bool(getattr(self.config, "vision_enabled", False))
        return {"enabled": enabled,
                "model": getattr(self.config, "vision_model", "") if enabled else "",
                "endpoint": getattr(self.config, "vision_endpoint", "") if enabled else ""}

    def execute(self, intent: Intent) -> SkillResult:
        if intent.type == "inspect_screen":
            return self._inspect(intent)
        if intent.type != "capture_screenshot":
            return self.fail(f"Unsupported screenshot action '{intent.type}'.")
        unknown = set(intent.parameters) - {"filename"}
        if unknown:
            return self.fail("Unsupported screenshot parameter(s): "
                             + ", ".join(sorted(unknown)))

        raw = intent.get("filename")
        if raw is not None and not str(raw).strip():
            return self.fail("Screenshot filename cannot be empty.")
        if raw is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            filename = f"axon-{stamp}.png"
        else:
            filename = str(raw).strip()
            candidate = Path(filename)
            if (candidate.is_absolute() or candidate.name != filename
                    or ".." in candidate.parts):
                return self.fail("Screenshot filename must not contain a path.")
            if (not _SAFE_FILENAME.fullmatch(filename)
                    or filename.endswith((".", " "))
                    or candidate.stem.lower() in _RESERVED_STEMS):
                return self.fail("Screenshot filename contains invalid characters.")
            if candidate.suffix and candidate.suffix.lower() != ".png":
                return self.fail("Screenshots must use the .png extension.")
            if not candidate.suffix:
                filename += ".png"

        try:
            from PIL import ImageGrab
        except ImportError:
            return self.fail("Screenshot capture requires Pillow.",
                             speak="Screenshot capture isn't available, sir.")

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        destination = SCREENSHOT_DIR / filename
        if destination.exists():
            return self.fail("A screenshot with that filename already exists.")
        try:
            image = ImageGrab.grab(all_screens=True)
            temporary = destination.with_suffix(".png.tmp")
            image.save(temporary, format="PNG")
            temporary.replace(destination)
        except Exception as exc:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            return self.fail(f"Screenshot capture failed: {exc}",
                             speak="I couldn't capture the screen, sir.")
        relative = destination.relative_to(DATA_DIR / "workspace")
        return self.ok(f"Screenshot saved to {relative}.",
                       speak="Screenshot captured, sir.", path=str(relative))

    def _inspect(self, intent: Intent) -> SkillResult:
        try:
            from PIL import ImageGrab
        except ImportError:
            return self.fail("Screen inspection requires Pillow.")
        try:
            image = ImageGrab.grab(all_screens=True)
            width, height = image.size
        except Exception as exc:
            return self.fail(f"Screen inspection failed: {exc}",
                             speak="I couldn't inspect the screen, sir.")
        try:
            from ..window_control.handler import _active_window_title
            active_window = _active_window_title()
        except Exception:
            active_window = ""
        vision = {"ok": False, "error": "local vision is disabled"}
        if bool(getattr(self.config, "vision_enabled", False)):
            from ...perception.vision import LocalVisionClient
            client = LocalVisionClient(
                getattr(self.config, "vision_endpoint", ""),
                getattr(self.config, "vision_model", ""),
                getattr(self.config, "vision_timeout", 30.0))
            vision = client.analyze(image, str(intent.get("prompt", "")))
        text = ""
        ocr_available = False
        try:
            import pytesseract
            text = " ".join(pytesseract.image_to_string(
                image, timeout=8).split())[:4000]
            ocr_available = True
        except Exception:
            pass
        analysis = str(vision.get("analysis", "")) if vision.get("ok") else ""
        summary = (f"Screen {width}×{height}"
                   + (f" | active: {active_window}" if active_window else "")
                   + (f" | analysis: {analysis[:1000]}" if analysis else "")
                   + (f" | text: {text[:500]}" if text else ""))
        if analysis:
            spoken = f"{analysis[:900]}, sir."
        elif text:
            spoken = f"The active screen shows: {text[:600]}, sir."
        elif active_window:
            spoken = (f"The active window is {active_window}. Local OCR is not "
                      "available for deeper reading, sir.")
        else:
            spoken = "I captured the screen, but local OCR is unavailable, sir."
        return self.ok(summary, speak=spoken, width=width, height=height,
                       active_window=active_window, text=text,
                       analysis=analysis, ocr_available=ocr_available,
                       vision_model=vision.get("model", ""),
                       vision_error="" if vision.get("ok") else vision.get("error", ""),
                       persisted=False)


SKILL = ScreenshotSkill()
