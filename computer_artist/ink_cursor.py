"""The agent's ink cursor, rendered for the Windows and macOS overlays.

A fountain pen whose nib tip is the hotspot. Moving leaves a tapered ink stroke
flowing from the nib that thins and fades behind it, and fast moves fling small
droplets. A press splashes an ink ring with droplets and pushes the pen into the page;
while a button is held the stroke lasts longer, like a brush. At rest a drop gathers
on the nib, swells and falls.

The KWin plugin draws the same design in plugin/main.cpp (Artist::drawCursor). Keep
the palette, geometry and timings below in step with it.
"""

import math
import random
import time
from collections import deque

from PIL import Image, ImageDraw, ImageFilter

INK = (64, 88, 255)
INK_DEEP = (22, 20, 86)
SHEEN = (132, 210, 255)
PAPER = (252, 252, 255)
PAPER_SHADE = (214, 222, 250)
BAND = (200, 212, 240)
# Pen parts in logical pixels as (along, across) the pen from the nib tip at (0, 0).
# The pen points from the tip up and to the right at PEN_ANGLE degrees.
PEN_ANGLE = -50
PEN_SIZE = 1.15
NIB = ((0, 0), (3, -1.2), (6.5, -2.6), (10, -3.8), (12.5, -4.2),
       (12.5, 4.2), (10, 3.8), (6.5, 2.6), (3, 1.2))  # fmt: skip
SECTION = ((12.5, -4.4), (17, -4.7), (17, 4.7), (12.5, 4.4))
BAND_RING = ((17, -5.1), (18.6, -5.1), (18.6, 5.1), (17, 5.1))
BARREL = ((18.6, -5.2), (36, -5.2), (38, -3.9), (38.8, 0), (38, 3.9), (36, 5.2), (18.6, 5.2))
BARREL_SHADE = ((18.6, 0.8), (36.4, 0.8), (38.3, 2.6), (38, 3.9), (36, 5.2), (18.6, 5.2))
BARREL_SHEEN = ((19.6, -3.7), (34.6, -3.7), (34.6, -2.2), (19.6, -2.2))
CLIP = ((22, -6.1), (33.5, -6.1))
NIB_SHADE = ((0, 0), (12.5, 0), (12.5, 4.2), (10, 3.8), (6.5, 2.6), (3, 1.2))
PEN = ((0, 0), (3, -1.2), (6.5, -2.6), (10, -3.8), (12.5, -4.2), (12.5, -4.4), (17, -4.7),
       (17, -5.1), (18.6, -5.2), (36, -5.2), (38, -3.9), (38.8, 0), (38, 3.9), (36, 5.2),
       (18.6, 5.2), (17, 5.1), (17, 4.7), (12.5, 4.4), (12.5, 4.2), (10, 3.8), (6.5, 2.6),
       (3, 1.2))  # fmt: skip
TRAIL_SECONDS = 0.45
BRUSH_SECONDS = 1.1
SPLASH_SECONDS = 0.55
DROP_SECONDS = 0.6
DRIP_SECONDS = 2.6
MAX_EXTENT = 520  # Logical pixels; a longer warp drops the old trail.
SUPERSAMPLE = 2
GLIDE_FROM = 24  # Input units; nearer moves are already continuous.
GLIDE_STEP = 0.016


def glide(start, end):
    """Visible travel between two actions: eased points and the delay between them.

    The pen glides quickly to a new position instead of jumping, so the human can
    follow it; callers send the real input once it has arrived.
    """
    if start is None:
        return [], 0.0
    (sx, sy), (ex, ey) = start, end
    distance = math.hypot(ex - sx, ey - sy)
    if distance < GLIDE_FROM:
        return [], 0.0
    duration = min(0.28, 0.08 + distance * 0.00035)
    steps = max(2, round(duration / GLIDE_STEP))
    points = []
    for i in range(1, steps):
        eased = 1 - (1 - i / steps) ** 3
        points.append((sx + (ex - sx) * eased, sy + (ey - sy) * eased))
    return points, duration / steps


class InkCursor:
    def __init__(self, scale=1.0, size=1.0, seed=None):
        """`scale`: image pixels per input coordinate unit. `size`: how many input units
        one design pixel spans (Windows passes physical pixels, so size = DPI / 96)."""
        for value in (scale, size):
            if not (isinstance(value, (int, float)) and 0.5 <= value <= 4):
                raise ValueError('Cursor scale and size must be 0.5–4')
        self.scale, self.size = float(scale), float(size)
        self.position = None
        self.pressed = False
        self.samples = deque()  # (time, x, y, pressed)
        self.drops = []  # [born, x, y, vx, vy, radius]
        self.splashes = []  # (born, x, y, lobes, phase)
        self.lean = 0.0
        self.last_move = -10.0
        self.random = random.Random(seed)

    def move(self, x, y, now=None):
        now = time.monotonic() if now is None else now
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError('Cursor coordinates must be finite')
        x, y = x / self.size, y / self.size  # Work in design units.
        if self.position is not None:
            px, py = self.position
            distance = math.hypot(x - px, y - py)
            if distance > MAX_EXTENT:
                self.samples.clear()  # A warp is not a visible sweep across the desktop.
            elif distance > 9:
                self._fling(px, py, x, y, distance, now)
        self.position = (float(x), float(y))
        self.samples.append((now, float(x), float(y), self.pressed))
        self.last_move = now

    def press(self, pressed, now=None):
        now = time.monotonic() if now is None else now
        if pressed and not self.pressed and self.position is not None:
            x, y = self.position
            self.splashes.append(
                (now, x, y, self.random.choice((5, 6, 7)), self.random.random() * 6.3)
            )
            for _ in range(6):
                angle = self.random.random() * 2 * math.pi
                speed = 70 + self.random.random() * 90
                radius = 1.4 + self.random.random() * 1.8
                self.drops.append(
                    [now, x, y, math.cos(angle) * speed, math.sin(angle) * speed, radius]
                )
        self.pressed = bool(pressed)
        if self.position is not None:
            self.samples.append((now, *self.position, self.pressed))

    def _fling(self, px, py, x, y, distance, now):
        """Fast strokes shed droplets sideways and slightly behind the nib."""
        if self.random.random() > min(0.85, distance / 40):
            return
        dx, dy = (x - px) / distance, (y - py) / distance
        side = self.random.choice((-1, 1))
        speed = 25 + self.random.random() * 45
        self.drops.append([
            now, x - dx * 6, y - dy * 6,
            (-dy * side - dx * 0.4) * speed, (dx * side - dy * 0.4) * speed,
            0.9 + self.random.random() * 1.6,
        ])  # fmt: skip

    def busy(self, now=None):
        """Whether the next frames animate quickly (moving, splashing or dripping)."""
        now = time.monotonic() if now is None else now
        return bool(
            now - self.last_move < BRUSH_SECONDS or self.splashes or self.drops or self.pressed
        )

    def _expire(self, now):
        while self.samples and now - self.samples[0][0] > (
            BRUSH_SECONDS if self.samples[0][3] else TRAIL_SECONDS
        ):
            self.samples.popleft()
        self.splashes = [s for s in self.splashes if now - s[0] < SPLASH_SECONDS]
        self.drops = [d for d in self.drops if now - d[0] < DROP_SECONDS]

    def render(self, now=None):
        """One frame: (RGBA image, (left, top)) with left/top in input coordinates.

        The image has `scale` pixels per input unit. Returns None before the first move.
        """
        now = time.monotonic() if now is None else now
        if self.position is None:
            return None
        self._expire(now)
        x0, y0 = self.position
        drops = [self._drop_at(d, now) for d in self.drops]
        points = [(x0 - 24, y0 - 46), (x0 + 46, y0 + 30)]
        points += [(s[1], s[2]) for s in self.samples]
        points += [
            (s[1] + dx, s[2] + dy) for s in self.splashes for dx, dy in ((-34, -34), (34, 34))
        ]
        points += [(dx, dy) for dx, dy, _ in drops]
        left = math.floor(min(p[0] for p in points) - 12)
        top = math.floor(min(p[1] for p in points) - 12)
        right = math.ceil(max(p[0] for p in points) + 12)
        bottom = math.ceil(max(p[1] for p in points) + 12)
        if right - left > MAX_EXTENT or bottom - top > MAX_EXTENT:
            self.samples.clear()
            self.drops.clear()
            left, top, right, bottom = x0 - 36, y0 - 58, x0 + 58, y0 + 42
        pixels = self.scale * self.size  # Image pixels per design unit.
        factor = pixels * SUPERSAMPLE
        size = (max(1, round((right - left) * factor)), max(1, round((bottom - top) * factor)))

        def device(px, py):
            return ((px - left) * factor, (py - top) * factor)

        ink = Image.new('RGBA', size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(ink)
        glow = Image.new('RGBA', size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        self._stroke(draw, glow_draw, device, factor, now)
        for dx, dy, radius in drops:
            cx, cy = device(dx, dy)
            draw.ellipse((cx - radius * factor, cy - radius * factor,
                          cx + radius * factor, cy + radius * factor), fill=INK + (215,))  # fmt: skip
        for splash in self.splashes:
            self._splash(draw, device, factor, splash, now)

        target = 0.0
        if len(self.samples) > 1:
            target = max(-8.0, min(8.0, (x0 - self.samples[0][1]) * 0.1))
        self.lean += (target - self.lean) * 0.25
        pen = self._pen(device)
        self._drip(draw, device, factor, now)

        energy = 1.0 if now - self.last_move < 0.2 or self.pressed else 0.7
        glow_draw.polygon(pen(PEN), fill=INK + (int(150 * energy),))
        glow = glow.filter(ImageFilter.GaussianBlur(4 * factor))
        frame = Image.alpha_composite(glow, ink)
        frame = Image.alpha_composite(frame, self._pen_body(size, pen, factor))
        return frame.resize(
            (max(1, round((right - left) * pixels)), max(1, round((bottom - top) * pixels))),
            Image.LANCZOS,
        ), (left * self.size, top * self.size)

    def _drop_at(self, drop, now):
        born, x, y, vx, vy, radius = drop
        age = (now - born) / DROP_SECONDS
        travel = (1 - (1 - age) ** 2) * 0.35  # Decelerating flight.
        return x + vx * travel, y + vy * travel, radius * (1 - age) ** 0.6

    def _stroke(self, draw, glow, device, factor, now):
        """Tapered ink with a light core; its bloom keeps it readable on dark apps."""
        samples = list(self.samples)
        for (t1, x1, y1, p1), (_t2, x2, y2, _p2) in zip(samples, samples[1:]):
            span = BRUSH_SECONDS if p1 else TRAIL_SECONDS
            fade = max(0.0, 1 - (now - t1) / span)
            if fade <= 0 or (x1, y1) == (x2, y2):
                continue
            width = (0.7 + (8.5 if p1 else 6.5) * fade**0.8) * factor
            alpha = int(225 * fade**1.2)
            a, b = device(x1, y1), device(x2, y2)
            glow.line((a, b), fill=INK + (int(110 * fade),), width=max(1, round(width * 2.2)))
            draw.line((a, b), fill=INK + (alpha,), width=max(1, round(width)))
            r = width / 2  # Round joints keep the stroke continuous through turns.
            draw.ellipse((b[0] - r, b[1] - r, b[0] + r, b[1] + r), fill=INK + (alpha,))
            draw.line((a, b), fill=SHEEN + (int(200 * fade**1.5),),
                      width=max(1, round(width * 0.28)))  # fmt: skip

    def _splash(self, draw, device, factor, splash, now):
        born, x, y, lobes, phase = splash
        q = (now - born) / SPLASH_SECONDS
        radius = 4 + 26 * (1 - (1 - q) ** 3)
        wobble = 0.16 * (1 - q)
        ring = [
            device(
                x + math.cos(a) * radius * (1 + wobble * math.sin(lobes * a + phase)),
                y + math.sin(a) * radius * (1 + wobble * math.sin(lobes * a + phase)),
            )
            for a in (i * 2 * math.pi / 64 for i in range(65))
        ]
        ring_width = max(1, round((0.8 + 2.6 * (1 - q)) * factor))
        draw.line(ring, fill=INK + (int(235 * (1 - q) ** 1.6),), width=ring_width, joint='curve')
        draw.line(ring, fill=SHEEN + (int(160 * (1 - q) ** 2),),
                  width=max(1, ring_width // 3), joint='curve')  # fmt: skip
        blot = 5.5 * (1 - q) ** 0.7 * factor
        cx, cy = device(x, y)
        draw.ellipse((cx - blot, cy - blot, cx + blot, cy + blot), fill=INK + (int(190 * (1 - q)),))

    def _drip(self, draw, device, factor, now):
        """At rest a drop gathers on the nib, swells and falls."""
        rest = now - self.last_move
        if rest < 0.4 or self.pressed:
            return
        q = (rest % DRIP_SECONDS) / DRIP_SECONDS
        swell = math.sin(min(q / 0.8, 1) * math.pi / 2)
        fall = max(0.0, (q - 0.8) / 0.2)
        cx, cy = device(self.position[0], self.position[1] + 1.5 + 10 * fall**2)
        r = (1.2 + 1.8 * swell) * (1 - fall) * factor
        if r > 0.2:
            draw.ellipse(
                (cx - r, cy - r * 1.15, cx + r, cy + r), fill=INK + (int(230 * (1 - fall)),)
            )

    def _pen(self, device):
        """Map (along, across) pen coordinates to device pixels for this frame."""
        angle = math.radians(PEN_ANGLE + self.lean)
        ax, ay = math.cos(angle), math.sin(angle)
        press = PEN_SIZE * (0.9 if self.pressed else 1.0)  # The pen pushes into the page.
        px, py = self.position

        def place(points):
            return [device(px + (u * ax - v * ay) * press, py + (u * ay + v * ax) * press)
                    for u, v in points]  # fmt: skip

        return place

    def _pen_body(self, size, pen, factor):
        """Silver nib with its slit, dark grip, silver band and a glossy ink barrel.

        ImageDraw replaces pixels rather than blending, so shading uses opaque mixes.
        """
        layer = Image.new('RGBA', size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        line = max(1, round(1.1 * factor))
        draw.polygon(pen(BARREL), fill=INK + (255,))
        draw.polygon(pen(BARREL_SHADE), fill=_mix(INK, INK_DEEP, 0.45) + (255,))
        draw.polygon(pen(BARREL_SHEEN), fill=_mix(INK, (255, 255, 255), 0.55) + (255,))
        draw.polygon(pen(SECTION), fill=INK_DEEP + (255,))
        draw.polygon(pen(BAND_RING), fill=BAND + (255,))
        draw.polygon(pen(NIB), fill=PAPER + (255,))
        draw.polygon(pen(NIB_SHADE), fill=PAPER_SHADE + (255,))
        draw.line(pen(PEN) + pen(PEN[:1]), fill=INK_DEEP + (235,), width=line, joint='curve')
        draw.line(pen(((1.4, 0), (8.2, 0))), fill=INK_DEEP + (230,), width=line)
        bx, by = pen(((8.9, 0),))[0]
        hole = 0.9 * factor
        draw.ellipse((bx - hole, by - hole, bx + hole, by + hole), fill=INK_DEEP + (230,))
        draw.line(pen(CLIP), fill=BAND + (255,), width=max(1, round(1.6 * factor)))
        cx, cy = pen(CLIP[1:])[0]
        knob = 1.2 * factor
        draw.ellipse((cx - knob, cy - knob, cx + knob, cy + knob), fill=BAND + (255,))
        draw.line(pen(((19, -5.2), (36, -5.2))), fill=_mix(INK, SHEEN, 0.7) + (255,), width=line)
        return layer


def _mix(a, b, amount):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
