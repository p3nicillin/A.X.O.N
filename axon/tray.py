"""Optional system-tray restore/quit surface for the packaged web UI."""
from __future__ import annotations


class TrayController:
    def __init__(self, window) -> None:
        self.window = window
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new("RGBA", (64, 64), (4, 3, 11, 255))
            draw = ImageDraw.Draw(image)
            draw.ellipse((8, 8, 56, 56), outline=(95, 230, 255, 255), width=4)
            draw.line((20, 44, 32, 16, 44, 44), fill=(157, 123, 255, 255),
                      width=5)
            self.icon = pystray.Icon("AXON", image, "AXON", pystray.Menu(
                pystray.MenuItem("Show AXON", self._show, default=True),
                pystray.MenuItem("Quit", self._quit)))
            self.icon.run_detached()
            return True
        except Exception:
            self.icon = None
            return False

    def _show(self, _icon=None, _item=None) -> None:
        try:
            self.window.show()
        except Exception:
            pass

    def _quit(self, _icon=None, _item=None) -> None:
        try:
            self.window.destroy()
        except Exception:
            pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        self.icon = None
