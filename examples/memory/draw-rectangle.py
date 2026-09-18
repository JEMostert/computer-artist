"""A reusable pointer primitive for an already-selected drawing tool."""
CONTRACT = {
    'description': 'Draw one rectangle with the current canvas tool; check that pixels changed.',
    'requires': ['move', 'button', 'capture'],
    'parameters': {
        'x': {'min': 0, 'max': .99, 'unit': 'window fraction'},
        'y': {'min': 0, 'max': .99, 'unit': 'window fraction'},
        'width': {'min': .001, 'max': .99, 'unit': 'window fraction'},
        'height': {'min': .001, 'max': .99, 'unit': 'window fraction'},
    },
}


def run(ctx, x: float, y: float, width: float, height: float):
    if x+width >= 1 or y+height >= 1:
        raise ValueError('Rectangle extends beyond window content')
    before = ctx.observe()
    corners = [(x,y), (x+width,y), (x+width,y+height), (x,y+height), (x,y)]
    ctx.path(corners, relative=True, interval=.06)
    result = ctx.wait_for({'pixels_changed': {'changed_since': before['id']}}, timeout=3)
    return {'before': before['id'], 'after': result['observation']['id'], 'matched': result['matches']}


def verify(ctx, result):
    # This verifies a visual effect, not that the application produced a correct rectangle.
    return {'check': 'Canvas pixels changed after input',
            'passed': result['matched'] == 'pixels_changed', 'evidence': result}
