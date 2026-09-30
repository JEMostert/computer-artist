"""Draw in a selected canvas: ca execute --window APP < examples/agent_demo.py."""

import math


def run(ctx):
    window = ctx.window
    cx, cy = window['width'] / 2, window['height'] * 0.58
    rx, ry = window['width'] * 0.32, window['height'] * 0.24
    points = [
        (cx + rx * math.sin(i / 360 * math.tau), cy + ry * math.sin(i / 360 * math.tau * 2))
        for i in range(361)
    ]
    ctx.path(points, interval=0.018)
    return ctx.observe()
