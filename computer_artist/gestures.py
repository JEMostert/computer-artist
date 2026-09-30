"""Bounded SVG path geometry, sampled in logical content pixels.

Implements the SVG moveto, line, Bézier, elliptical arc and closepath commands.
This produces pen strokes, not SVG rendering (fills/styles/transforms are absent).
"""

import math
import re

NUMBER = re.compile(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')
COMMANDS = set('MmLlHhVvCcSsQqTtAaZz')
MAX_POINTS = 20000
MAX_COORDINATE = 10_000_000


def svg_path(data, *, origin=(0, 0), scale=1, spacing=2):
    """Return independent sampled strokes; reject invalid/excessive geometry."""
    if not isinstance(data, str) or not data.strip() or len(data) > 262144:
        raise ValueError('SVG path must be nonempty text of at most 262144 characters')
    if len(origin) != 2 or any(not math.isfinite(v) for v in origin):
        raise ValueError('origin must be two finite coordinates')
    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(spacing) or spacing <= 0:
        raise ValueError('scale and spacing must be positive finite numbers')
    distance = spacing / scale
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError('scale/spacing ratio is outside supported range')
    index = 0
    strokes = []
    stroke = None
    point = (0.0, 0.0)
    start = None
    command = None
    previous = None
    cubic = quadratic = None
    count = 0
    work = 0

    def consume_work():
        nonlocal work
        work += 1
        if work > 3 * MAX_POINTS:
            raise ValueError(
                'SVG path exceeds sampling work limit; increase spacing or reduce scale'
            )

    def skip():
        nonlocal index
        while index < len(data) and data[index] in ' \t\r\n,':
            index += 1

    def number(flag=False):
        nonlocal index
        skip()
        if flag:
            if index >= len(data) or data[index] not in '01':
                raise ValueError('Arc flags must be 0 or 1')
            value = int(data[index])
            index += 1
            return value
        match = NUMBER.match(data, index)
        if not match:
            raise ValueError(f'Expected SVG number at character {index}')
        index = match.end()
        value = float(match[0])
        if not math.isfinite(value) or abs(value) > MAX_COORDINATE:
            raise ValueError('SVG coordinates must be finite and at most 10000000 in magnitude')
        return value

    def pair(relative):
        x, y = number(), number()
        return (x + point[0], y + point[1]) if relative else (x, y)

    def add(value):
        nonlocal count, stroke
        consume_work()  # Duplicate coordinates still consume generation work.
        transformed = (origin[0] + scale * value[0], origin[1] + scale * value[1])
        if any(not math.isfinite(v) or abs(v) > MAX_COORDINATE for v in transformed):
            raise ValueError('Transformed path exceeds coordinate limits')
        if stroke is None:
            stroke = []
            strokes.append(stroke)
        if not stroke or stroke[-1] != transformed:
            count += 1
            if count > MAX_POINTS:
                raise ValueError(
                    'SVG path exceeds 20000 sampled points; increase spacing or reduce scale'
                )
            stroke.append(transformed)

    def line(a, b):
        length = math.dist(a, b)
        ratio = length / distance
        if not math.isfinite(ratio) or ratio > MAX_POINTS:
            raise ValueError('SVG segment exceeds sampling limit')
        steps = max(1, math.ceil(ratio))
        for step in range(1, steps + 1):
            t = step / steps
            add((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))

    def curve(control, depth=0):
        consume_work()
        # A Bézier lies inside its control polygon. Subdivision limits both
        # spatial steps and approximation error, including loops/cusps.
        length = sum(math.dist(a, b) for a, b in zip(control, control[1:]))
        if length <= distance:
            add(control[-1])
            return
        if depth >= 24:
            raise ValueError('Curve exceeds subdivision limit')
        levels = [control]
        while len(levels[-1]) > 1:
            levels.append(
                [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(levels[-1], levels[-1][1:])]
            )
        curve([level[0] for level in levels], depth + 1)
        curve([level[-1] for level in reversed(levels)], depth + 1)

    def arc(a, b, rx, ry, rotation, large, sweep):
        # SVG endpoint-to-center conversion, including mandatory radii correction.
        if a == b:
            return
        rx, ry = abs(rx), abs(ry)
        if rx == 0 or ry == 0:
            line(a, b)
            return
        phi = math.radians(rotation % 360)
        cosine, sine = math.cos(phi), math.sin(phi)
        dx, dy = (a[0] - b[0]) / 2, (a[1] - b[1]) / 2
        xp, yp = cosine * dx + sine * dy, -sine * dx + cosine * dy
        radii = math.hypot(xp / rx, yp / ry)
        if not math.isfinite(radii):
            raise ValueError('Arc radii are outside supported range')
        if radii > 1:
            rx, ry = rx * radii, ry * radii
        denominator = (xp / rx) ** 2 + (yp / ry) ** 2
        if denominator == 0:
            raise ValueError('Arc radii are outside supported range')
        coefficient = math.sqrt(max(0.0, (1 - denominator) / denominator)) * (
            -1 if large == sweep else 1
        )
        cxp, cyp = coefficient * rx * yp / ry, -coefficient * ry * xp / rx
        cx = cosine * cxp - sine * cyp + (a[0] + b[0]) / 2
        cy = sine * cxp + cosine * cyp + (a[1] + b[1]) / 2
        u = ((xp - cxp) / rx, (yp - cyp) / ry)
        v = ((-xp - cxp) / rx, (-yp - cyp) / ry)
        angle = math.atan2(u[1], u[0])
        delta = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        if sweep and delta < 0:
            delta += 2 * math.pi
        elif not sweep and delta > 0:
            delta -= 2 * math.pi
        ratio = abs(delta) * max(rx, ry) / distance
        if not math.isfinite(ratio) or ratio > MAX_POINTS:
            raise ValueError('Arc exceeds sampling limit')
        steps = max(1, math.ceil(ratio))
        for step in range(1, steps):
            theta = angle + delta * step / steps
            ex, ey = rx * math.cos(theta), ry * math.sin(theta)
            add((cx + cosine * ex - sine * ey, cy + sine * ex + cosine * ey))
        add(b)

    while True:
        skip()
        if index >= len(data):
            break
        if data[index] in COMMANDS:
            command = data[index]
            index += 1
        elif command is None or data[index].isalpha():
            raise ValueError(f'Unknown SVG command at character {index}')
        op, relative = command.upper(), command.islower()
        if start is None and op != 'M':
            raise ValueError('SVG path must begin with moveto (M)')
        if op == 'M':
            point = pair(relative)
            start = point
            stroke = None
            add(point)
            command = 'l' if relative else 'L'
        elif op == 'Z':
            if stroke is None:
                add(point)
            line(point, start)
            point = start
            stroke = None  # Subsequent drawing begins a new pen stroke.
            command = None
        else:
            if stroke is None:
                add(point)
            if op in ('L', 'H', 'V'):
                if op == 'L':
                    end = pair(relative)
                else:
                    value = number()
                    end = (
                        (value + (point[0] if relative else 0), point[1])
                        if op == 'H'
                        else (point[0], value + (point[1] if relative else 0))
                    )
                line(point, end)
            elif op in ('C', 'S'):
                first = (
                    pair(relative)
                    if op == 'C'
                    else (
                        (2 * point[0] - cubic[0], 2 * point[1] - cubic[1])
                        if previous in ('C', 'S')
                        else point
                    )
                )
                second, end = pair(relative), pair(relative)
                curve([point, first, second, end])
                cubic = second
            elif op in ('Q', 'T'):
                control = (
                    pair(relative)
                    if op == 'Q'
                    else (
                        (2 * point[0] - quadratic[0], 2 * point[1] - quadratic[1])
                        if previous in ('Q', 'T')
                        else point
                    )
                )
                end = pair(relative)
                curve([point, control, end])
                quadratic = control
            elif op == 'A':
                rx, ry, rotation = number(), number(), number()
                large, sweep = number(True), number(True)
                end = pair(relative)
                arc(point, end, rx, ry, rotation, large, sweep)
            point = end
        previous = op
    result = [s for s in strokes if len(s) > 1]
    if not result:
        raise ValueError('SVG path has no drawable segments')
    return result
