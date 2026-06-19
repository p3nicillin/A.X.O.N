"""GPU holographic core — a ``QOpenGLWidget`` with a real multi-pass bloom +
bokeh pipeline (pure PySide6, no extra GL binding required).

Why this exists
---------------
The Tkinter renderer fakes glow by stacking dark ovals — it can never show
*true* light bleed because the Canvas has no alpha compositing. Here the orb is
drawn procedurally in a fragment shader and then put through a film-style bloom
chain so it genuinely *radiates* light, brightening as JARVIS speaks.

Pipeline (all offscreen FBOs, then composited to the widget's framebuffer)::

    scene  -> sceneFBO      (full res, procedural JARVIS core, HDR-ish)
    bright -> bloomA        (¼ res, keep only pixels above threshold)
    bokeh  -> bloomB        (¼ res, disc sampling -> circular bokeh highlights)
    blurH  -> bloomA        (separable Gaussian, horizontal)
    blurV  -> bloomB        (separable Gaussian, vertical)
    comp   -> screen        (scene + bloom, tonemap, vignette, scanline)

The public interface mirrors the Tkinter ``CoreRenderer`` so the rest of the
app doesn't care which renderer is live: ``set_state``, ``push_audio``,
``push_speak``.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QElapsedTimer
from PySide6.QtGui import QVector2D, QVector3D
from PySide6.QtOpenGL import (
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..core.states import JarvisState

# --- raw GL enum constants we need (QOpenGLFunctions exposes the calls) -------
GL_COLOR_BUFFER_BIT = 0x4000
GL_TRIANGLES = 0x0004
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_FRAMEBUFFER = 0x8D40
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_LINEAR = 0x2601
GL_CLAMP_TO_EDGE = 0x812F

# Per-state "visual DNA": base RGB, accent RGB (0..1), energy 0..1, radar sweep.
_PALETTE = {
    JarvisState.IDLE:      ((0.16, 0.66, 1.00), (0.47, 0.86, 1.00), 0.25, 0.0),
    JarvisState.LISTENING: ((0.24, 0.82, 1.00), (0.67, 0.94, 1.00), 0.60, 1.0),
    JarvisState.THINKING:  ((0.59, 0.43, 1.00), (1.00, 0.67, 0.94), 0.95, 1.0),
    JarvisState.SPEAKING:  ((0.35, 0.90, 1.00), (0.71, 1.00, 0.90), 0.70, 0.0),
    JarvisState.ERROR:     ((1.00, 0.27, 0.27), (1.00, 0.71, 0.35), 0.85, 0.0),
}

_VERT = """
#version 330 core
out vec2 v_uv;
void main() {
    vec2 verts[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
    vec2 p = verts[gl_VertexID];
    v_uv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

# --- the JARVIS core itself, drawn procedurally -----------------------------
_SCENE = """
#version 330 core
in vec2 v_uv;
out vec4 frag;
uniform vec2  u_res;
uniform float u_time;
uniform float u_audio;
uniform float u_speak;
uniform float u_energy;
uniform float u_sweep;
uniform vec3  u_base;
uniform vec3  u_accent;
#define TAU 6.28318530718

float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
    float r = length(uv);
    float ang = atan(uv.y, uv.x);
    float react = 0.20 * u_audio + 0.14 * u_speak;

    vec3 col = vec3(0.0);
    float R = 0.15 * (1.0 + 0.06 * sin(u_time * 2.0) + react);

    // bright pulsing inner core (overbright so it blooms hard)
    float core = smoothstep(R, R * 0.18, r);
    float pulse = 0.6 + 0.4 * sin(u_time * 4.0) + 0.6 * u_speak;
    col += mix(u_accent, vec3(1.0), 0.6) * core * (1.4 + pulse);

    // arc-reactor broken rings, each spinning a different way
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        float rr = R * (1.7 + fi * 0.55);
        float ring = smoothstep(0.012 + 0.004 * fi, 0.0, abs(r - rr));
        float dir = (i == 1) ? -1.0 : 1.0;
        float seg = sin(ang * (6.0 + fi * 4.0) + u_time * (1.1 + fi * 0.6) * dir);
        float mask = smoothstep(0.1, 0.6, seg);
        col += mix(u_base, u_accent, 0.4 + 0.2 * fi) * ring * mask
               * (0.9 + 0.7 * u_energy) * 1.7;
    }

    // thin outer telemetry rings
    for (int i = 0; i < 2; i++) {
        float rr = R * (3.0 + float(i) * 0.7);
        float ring = smoothstep(0.006, 0.0, abs(r - rr));
        col += u_base * ring * (0.5 + 0.4 * u_audio);
    }

    // radiating energy filaments
    float fil = pow(max(0.0, sin(ang * 18.0 + u_time * 1.5)), 8.0);
    float fall = smoothstep(R * 3.4, R * 0.8, r);
    col += u_accent * fil * fall * (0.15 + 0.5 * u_energy + 0.5 * u_speak);

    // rotating radar sweep (listening / thinking)
    if (u_sweep > 0.5) {
        float sa = mod(u_time * 1.4, TAU);
        float d = mod(ang - sa + TAU * 1.5, TAU);
        float wedge = smoothstep(0.9, 0.0, d) * smoothstep(R * 3.4, R * 0.5, r);
        col += u_accent * wedge * 0.55;
    }

    // soft volumetric halo + faint background bloom seed
    col += u_base * exp(-r * 3.4) * (0.25 + 0.55 * u_speak + 0.3 * u_audio);
    col += u_base * 0.02 * exp(-r * 1.1);

    // sparkle grain near the core
    float n = hash(floor(uv * u_res.y * 0.5) + floor(u_time * 8.0));
    col += u_accent * step(0.995, n) * fall * 0.7;

    frag = vec4(col, 1.0);
}
"""

_BRIGHT = """
#version 330 core
in vec2 v_uv;
out vec4 frag;
uniform sampler2D u_tex;
uniform float u_threshold;
void main() {
    vec3 c = texture(u_tex, v_uv).rgb;
    float l = max(c.r, max(c.g, c.b));
    float k = smoothstep(u_threshold, u_threshold + 0.4, l);
    frag = vec4(c * k, 1.0);
}
"""

# disc sampling -> circular bokeh highlights around bright points
_BOKEH = """
#version 330 core
in vec2 v_uv;
out vec4 frag;
uniform sampler2D u_tex;
uniform vec2  u_texel;
uniform float u_radius;
void main() {
    vec3 sum = vec3(0.0);
    float total = 0.0;
    const int N = 24;
    for (int i = 0; i < N; i++) {
        float a = float(i) / float(N) * 6.28318530718;
        for (int j = 1; j <= 2; j++) {
            float rad = u_radius * float(j) / 2.0;
            vec2 off = vec2(cos(a), sin(a)) * rad * u_texel;
            sum += texture(u_tex, v_uv + off).rgb;
            total += 1.0;
        }
    }
    frag = vec4(sum / total, 1.0);
}
"""

_BLUR = """
#version 330 core
in vec2 v_uv;
out vec4 frag;
uniform sampler2D u_tex;
uniform vec2 u_dir;
void main() {
    float w[5] = float[5](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 c = texture(u_tex, v_uv).rgb * w[0];
    for (int i = 1; i < 5; i++) {
        c += texture(u_tex, v_uv + u_dir * float(i)).rgb * w[i];
        c += texture(u_tex, v_uv - u_dir * float(i)).rgb * w[i];
    }
    frag = vec4(c, 1.0);
}
"""

_COMP = """
#version 330 core
in vec2 v_uv;
out vec4 frag;
uniform sampler2D u_scene;
uniform sampler2D u_bloom;
uniform float u_intensity;
uniform vec2  u_res;
uniform float u_time;
void main() {
    vec3 scene = texture(u_scene, v_uv).rgb;
    vec3 bloom = texture(u_bloom, v_uv).rgb;
    vec3 c = scene + bloom * u_intensity;
    c = c / (c + vec3(1.0));            // Reinhard tonemap
    c = pow(c, vec3(0.85));             // gentle lift
    vec2 q = v_uv - 0.5;
    c *= 0.55 + 0.45 * smoothstep(0.95, 0.32, length(q));   // vignette
    c *= 0.96 + 0.04 * sin(v_uv.y * u_res.y * 1.5 + u_time * 2.0);  // scanline
    frag = vec4(c, 1.0);
}
"""


class GLCore(QOpenGLWidget):
    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.state = JarvisState.IDLE
        self.audio = 0.0
        self.speak = 0.0
        self._base = list(_PALETTE[self.state][0])
        self._acc = list(_PALETTE[self.state][1])
        self._energy = _PALETTE[self.state][2]
        self._sweep = _PALETTE[self.state][3]
        self._ok = False
        self._w = max(1, config.window_width)
        self._h = max(1, config.window_height)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last = time.monotonic()
        self._progs: dict = {}
        self._scene_fbo = None
        self._bloom_a = None
        self._bloom_b = None
        self._vao = QOpenGLVertexArrayObject()

    # ----- public interface (mirrors the Tk CoreRenderer) -------------------
    def set_state(self, state: JarvisState) -> None:
        self.state = state

    def push_audio(self, level: float) -> None:
        self.audio = max(self.audio, min(1.0, float(level)))

    def push_speak(self, level: float) -> None:
        self.speak = max(self.speak, min(1.0, float(level)))

    # ----- GL lifecycle ------------------------------------------------------
    def initializeGL(self) -> None:
        try:
            self.f = self.context().functions()
            self._vao.create()
            for name, frag in (("scene", _SCENE), ("bright", _BRIGHT),
                               ("bokeh", _BOKEH), ("blur", _BLUR),
                               ("comp", _COMP)):
                self._progs[name] = self._program(frag)
            self._ok = True
        except Exception as exc:   # pragma: no cover - driver dependent
            print(f"[gl] initialisation failed, core disabled: {exc}")
            self._ok = False

    def _program(self, frag: str) -> QOpenGLShaderProgram:
        prog = QOpenGLShaderProgram(self)
        if not prog.addShaderFromSourceCode(QOpenGLShader.Vertex, _VERT):
            raise RuntimeError("vertex: " + prog.log())
        if not prog.addShaderFromSourceCode(QOpenGLShader.Fragment, frag):
            raise RuntimeError("fragment: " + prog.log())
        if not prog.link():
            raise RuntimeError("link: " + prog.log())
        return prog

    def resizeGL(self, w: int, h: int) -> None:
        self._w, self._h = max(1, w), max(1, h)
        self._bw, self._bh = max(1, w // 3), max(1, h // 3)
        if not self._ok:
            return
        self._scene_fbo = self._make_fbo(self._w, self._h)
        self._bloom_a = self._make_fbo(self._bw, self._bh)
        self._bloom_b = self._make_fbo(self._bw, self._bh)

    def _make_fbo(self, w: int, h: int) -> QOpenGLFramebufferObject:
        fmt = QOpenGLFramebufferObjectFormat()
        fbo = QOpenGLFramebufferObject(w, h, fmt)
        self.f.glBindTexture(GL_TEXTURE_2D, fbo.texture())
        self.f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        self.f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        self.f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        self.f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        return fbo

    # ----- per-frame ---------------------------------------------------------
    def _advance(self) -> None:
        """Ease palette toward the target state and decay live levels."""
        tgt_base, tgt_acc, energy, sweep = _PALETTE[self.state]
        for i in range(3):
            self._base[i] += (tgt_base[i] - self._base[i]) * 0.08
            self._acc[i] += (tgt_acc[i] - self._acc[i]) * 0.08
        self._energy += (energy - self._energy) * 0.08
        self._sweep += (sweep - self._sweep) * 0.12
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        # frame-rate independent decay (~per Tk renderer feel)
        self.audio *= pow(0.88, dt * 60.0)
        self.speak *= pow(0.84, dt * 60.0)

    def paintGL(self) -> None:
        f = getattr(self, "f", None)
        if not self._ok or f is None or self._scene_fbo is None:
            if f is not None:
                f.glClearColor(0.004, 0.012, 0.03, 1.0)
                f.glClear(GL_COLOR_BUFFER_BIT)
            return

        self._advance()
        t = self._clock.elapsed() / 1000.0
        self._vao.bind()

        # 1. scene -> sceneFBO
        self._scene_fbo.bind()
        f.glViewport(0, 0, self._w, self._h)
        self._draw("scene", {
            "u_res": QVector2D(self._w, self._h),
            "u_time": t, "u_audio": self.audio, "u_speak": self.speak,
            "u_energy": self._energy, "u_sweep": self._sweep,
            "u_base": QVector3D(*self._base), "u_accent": QVector3D(*self._acc),
        })
        self._scene_fbo.release()

        # 2. bright extract -> bloomA  (¼ res)
        self._bloom_a.bind()
        f.glViewport(0, 0, self._bw, self._bh)
        self._draw("bright", {"u_threshold": self.config.bloom_threshold},
                   tex=[(self._scene_fbo.texture(), "u_tex", 0)])
        self._bloom_a.release()

        # 3. bokeh disc -> bloomB
        self._bloom_b.bind()
        f.glViewport(0, 0, self._bw, self._bh)
        self._draw("bokeh", {
            "u_texel": QVector2D(1.0 / self._bw, 1.0 / self._bh),
            "u_radius": self.config.bloom_bokeh_radius,
        }, tex=[(self._bloom_a.texture(), "u_tex", 0)])
        self._bloom_b.release()

        # 4. gaussian H (bloomB -> bloomA) then V (bloomA -> bloomB)
        self._bloom_a.bind()
        f.glViewport(0, 0, self._bw, self._bh)
        self._draw("blur", {"u_dir": QVector2D(1.5 / self._bw, 0.0)},
                   tex=[(self._bloom_b.texture(), "u_tex", 0)])
        self._bloom_a.release()
        self._bloom_b.bind()
        f.glViewport(0, 0, self._bw, self._bh)
        self._draw("blur", {"u_dir": QVector2D(0.0, 1.5 / self._bh)},
                   tex=[(self._bloom_a.texture(), "u_tex", 0)])
        self._bloom_b.release()

        # 5. composite -> the widget's framebuffer
        f.glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        f.glViewport(0, 0, self._w, self._h)
        self._draw("comp", {
            "u_intensity": self.config.bloom_intensity,
            "u_res": QVector2D(self._w, self._h), "u_time": t,
        }, tex=[(self._scene_fbo.texture(), "u_scene", 0),
                (self._bloom_b.texture(), "u_bloom", 1)])

        self._vao.release()

    def _draw(self, name: str, uniforms: dict, tex: list | None = None) -> None:
        # NB: PySide6's setUniformValue has no by-*name* scalar overload, so we
        # resolve each name to an int location first — location overloads cover
        # float / int / QVector2D / QVector3D uniformly.
        f = self.f
        prog = self._progs[name]
        prog.bind()
        for texture, uname, unit in (tex or ()):
            f.glActiveTexture(GL_TEXTURE0 + unit)
            f.glBindTexture(GL_TEXTURE_2D, texture)
            prog.setUniformValue(prog.uniformLocation(uname), int(unit))
        for key, val in uniforms.items():
            prog.setUniformValue(prog.uniformLocation(key), val)
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        prog.release()
