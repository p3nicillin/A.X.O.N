"""The reactive holographic core renderer (Tkinter Canvas).

A rotating wireframe sphere (meridians + latitudes) wrapped in a sparkling
particle field and a layered central glow, sitting on holographic platform
rings. Every visual property is driven by :class:`AxonState` plus live audio
levels, so the core *behaves* differently while idle, listening, thinking,
speaking or erroring.

Performance: canvas items are created ONCE and updated in place each frame via
``coords()`` / ``itemconfig()`` — never deleted and recreated — so it stays
smooth in pure Tkinter.

This class is deliberately self-contained behind a tiny interface
(``set_state``, ``push_audio``, ``push_speak``, ``step``) so a GPU renderer
(PySide6 + moderngl shaders) can replace it later without touching the pipeline.
"""
from __future__ import annotations

import math
import random

from ..core.states import AxonState

# per-state base RGB palette (core colour) and motion tuning
_PALETTE = {
    AxonState.IDLE:      ((40, 170, 255), 0.010, 0.9),
    AxonState.LISTENING: ((60, 210, 255), 0.020, 1.15),
    AxonState.THINKING:  ((150, 90, 255), 0.045, 1.0),
    AxonState.SPEAKING:  ((90, 230, 255), 0.025, 1.2),
    AxonState.ERROR:     ((255, 70, 70), 0.030, 0.85),
}

_MERIDIANS = 6
_LATITUDES = 5
_CURVE_PTS = 26
_PARTICLES = 90


def _hex(r: float, g: float, b: float) -> str:
    c = lambda v: max(0, min(255, int(v)))
    return f"#{c(r):02x}{c(g):02x}{c(b):02x}"


class CoreRenderer:
    def __init__(self, canvas, config) -> None:
        self.cv = canvas
        self.config = config
        self.state = AxonState.IDLE
        self.yaw = 0.0
        self.tilt = 0.42
        self.phase = 0.0
        self.audio = 0.0       # live mic level 0..1 (decays)
        self.speak = 0.0       # live TTS level 0..1 (decays)
        self.w = config.window_width
        self.h = config.window_height
        self._items_built = False

        # particle directions on the unit sphere
        self._particles = []
        for _ in range(_PARTICLES):
            theta = random.uniform(0, 2 * math.pi)
            phi = math.acos(random.uniform(-1, 1))
            self._particles.append((theta, phi, random.uniform(0.6, 1.0)))

        self._mer_ids: list[int] = []
        self._lat_ids: list[int] = []
        self._part_ids: list[int] = []
        self._glow_ids: list[int] = []
        self._ring_ids: list[int] = []
        self._wave_id = None

    # -- external inputs -----------------------------------------------------
    def set_state(self, state: AxonState) -> None:
        self.state = state

    def push_audio(self, level: float) -> None:
        self.audio = max(self.audio, min(1.0, level))

    def push_speak(self, level: float) -> None:
        self.speak = max(self.speak, min(1.0, level))

    def resize(self, w: int, h: int) -> None:
        self.w, self.h = w, h

    # -- geometry ------------------------------------------------------------
    @property
    def _center(self):
        return self.w * 0.5, self.h * 0.46

    @property
    def _radius(self):
        base = min(self.w, self.h) * 0.26
        pulse = 1.0 + 0.05 * math.sin(self.phase * 2)
        react = 1.0 + 0.18 * self.audio + 0.10 * self.speak
        return base * pulse * react

    def _project(self, theta: float, phi: float):
        x = math.sin(phi) * math.cos(theta)
        y = math.cos(phi)
        z = math.sin(phi) * math.sin(theta)
        # rotate around Y (yaw)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        x, z = x * cy + z * sy, -x * sy + z * cy
        # rotate around X (tilt)
        ct, st = math.cos(self.tilt), math.sin(self.tilt)
        y, z = y * ct - z * st, y * st + z * ct
        cx, cyp = self._center
        r = self._radius
        return cx + x * r, cyp + y * r, z  # z in [-1,1], +front

    # -- build canvas items once --------------------------------------------
    def _build(self) -> None:
        self.cv.configure(bg="#03070f")
        # platform rings (drawn first, underneath)
        for _ in range(4):
            self._ring_ids.append(self.cv.create_oval(0, 0, 0, 0, outline="#0a2a44"))
        # central glow layers (largest first)
        for _ in range(6):
            self._glow_ids.append(self.cv.create_oval(0, 0, 0, 0, outline=""))
        # sphere lattice
        for _ in range(_MERIDIANS):
            self._mer_ids.append(self.cv.create_line(0, 0, 0, 0, smooth=True))
        for _ in range(_LATITUDES):
            self._lat_ids.append(self.cv.create_line(0, 0, 0, 0, smooth=True))
        # particles
        for _ in range(_PARTICLES):
            self._part_ids.append(self.cv.create_oval(0, 0, 0, 0, outline=""))
        # waveform overlay
        self._wave_id = self.cv.create_line(0, 0, 0, 0, fill="", width=2, smooth=True)
        self._items_built = True

    # -- per-frame update ----------------------------------------------------
    def step(self) -> None:
        if not self._items_built:
            self._build()

        base_rgb, spin, glow_gain = _PALETTE[self.state]
        # thinking spins faster the more it "thinks"
        self.yaw += spin * (2 if self.state == AxonState.THINKING else 1)
        self.phase += 0.05
        self.audio *= 0.85      # decay live levels
        self.speak *= 0.80

        flicker = (random.uniform(0.7, 1.0)
                   if self.state == AxonState.THINKING else 1.0)
        jitter = (lambda: random.uniform(-3, 3)) if self.state == AxonState.ERROR \
            else (lambda: 0.0)

        self._draw_rings(base_rgb)
        self._draw_glow(base_rgb, glow_gain)
        self._draw_lattice(base_rgb, flicker, jitter)
        self._draw_particles(base_rgb, flicker, jitter)
        self._draw_waveform(base_rgb)

    def _draw_rings(self, rgb) -> None:
        cx, cy = self._center
        r = self._radius
        col = _hex(rgb[0] * 0.35, rgb[1] * 0.35, rgb[2] * 0.45)
        for i, rid in enumerate(self._ring_ids):
            rr = r * (1.35 + i * 0.28)
            yy = cy + r * 0.95
            sq = 0.16  # platform perspective squash
            self.cv.coords(rid, cx - rr, yy - rr * sq, cx + rr, yy + rr * sq)
            self.cv.itemconfig(rid, outline=col)

    def _draw_glow(self, rgb, gain) -> None:
        cx, cy = self._center
        r = self._radius
        for i, gid in enumerate(self._glow_ids):
            t = i / len(self._glow_ids)
            rr = r * (0.05 + t * 0.55) * (1 + 0.4 * self.speak)
            f = (1 - t) * gain
            col = _hex(rgb[0] * f + 20, rgb[1] * f + 30, rgb[2] * f + 40)
            self.cv.coords(gid, cx - rr, cy - rr, cx + rr, cy + rr)
            self.cv.itemconfig(gid, fill=col)

    def _draw_lattice(self, rgb, flicker, jitter) -> None:
        # meridians: great circles at evenly spaced yaw offsets
        for m, mid in enumerate(self._mer_ids):
            off = math.pi * m / _MERIDIANS
            pts = []
            depth = 0.0
            for k in range(_CURVE_PTS + 1):
                phi = math.pi * k / _CURVE_PTS
                x, y, z = self._project(off, phi)
                pts += [x + jitter(), y + jitter()]
                depth += z
            depth /= (_CURVE_PTS + 1)
            self._config_curve(mid, pts, rgb, depth, flicker, 1)
        # latitudes: horizontal circles
        for l, lid in enumerate(self._lat_ids):
            phi = math.pi * (l + 1) / (_LATITUDES + 1)
            pts = []
            depth = 0.0
            for k in range(_CURVE_PTS + 1):
                theta = 2 * math.pi * k / _CURVE_PTS
                x, y, z = self._project(theta, phi)
                pts += [x + jitter(), y + jitter()]
                depth += z
            depth /= (_CURVE_PTS + 1)
            self._config_curve(lid, pts, rgb, depth, flicker, 1)

    def _config_curve(self, item, pts, rgb, depth, flicker, width) -> None:
        b = (0.45 + 0.55 * (depth + 1) / 2) * flicker
        col = _hex(rgb[0] * b, rgb[1] * b, rgb[2] * b)
        self.cv.coords(item, *pts)
        self.cv.itemconfig(item, fill=col, width=width)

    def _draw_particles(self, rgb, flicker, jitter) -> None:
        for (theta, phi, scale), pid in zip(self._particles, self._part_ids):
            x, y, z = self._project(theta + self.phase * 0.1, phi)
            b = (0.3 + 0.7 * (z + 1) / 2) * flicker
            size = (1.0 + 2.2 * (z + 1) / 2) * scale * (1 + 0.5 * self.audio)
            col = _hex(rgb[0] * b + 30, rgb[1] * b + 40, rgb[2] * b + 50)
            x += jitter(); y += jitter()
            self.cv.coords(pid, x - size, y - size, x + size, y + size)
            self.cv.itemconfig(pid, fill=col)

    def _draw_waveform(self, rgb) -> None:
        # only visible while listening / speaking; an audio-reactive ribbon
        active = self.state in (AxonState.LISTENING, AxonState.SPEAKING)
        if not active:
            self.cv.itemconfig(self._wave_id, fill="")
            return
        amp = (self.speak if self.state == AxonState.SPEAKING else self.audio)
        amp = 6 + amp * (self.h * 0.10)
        cx, _ = self._center
        y0 = self.h * 0.86
        span = self.w * 0.5
        pts = []
        n = 60
        for i in range(n + 1):
            fx = cx - span / 2 + span * i / n
            env = math.sin(math.pi * i / n)  # taper ends
            fy = y0 + math.sin(self.phase * 3 + i * 0.5) * amp * env
            pts += [fx, fy]
        col = _hex(rgb[0], rgb[1], rgb[2])
        self.cv.coords(self._wave_id, *pts)
        self.cv.itemconfig(self._wave_id, fill=col)
