"""Physical Linux key/button definitions and framework-independent chord parsing."""

LETTERS = dict(
    zip(
        'qwertyuiopasdfghjklzxcvbnm',
        [
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
            25,
            30,
            31,
            32,
            33,
            34,
            35,
            36,
            37,
            38,
            44,
            45,
            46,
            47,
            48,
            49,
            50,
        ],
    )
)
PLAIN = {
    **LETTERS,
    **dict(zip('1234567890', range(2, 12))),
    ' ': 57,
    '\n': 28,
    '\t': 15,
    '-': 12,
    '=': 13,
    '[': 26,
    ']': 27,
    ';': 39,
    "'": 40,
    '`': 41,
    '\\': 43,
    ',': 51,
    '.': 52,
    '/': 53,
}
BUTTONS = {'left': 272, 'right': 273, 'middle': 274}
KEYS = {
    **PLAIN,
    'ctrl': 29,
    'control': 29,
    'shift': 42,
    'alt': 56,
    'super': 125,
    'meta': 125,
    'enter': 28,
    'return': 28,
    'tab': 15,
    'escape': 1,
    'esc': 1,
    'backspace': 14,
    'delete': 111,
    'insert': 110,
    'space': 57,
    'left': 105,
    'right': 106,
    'up': 103,
    'down': 108,
    'home': 102,
    'end': 107,
    'pageup': 104,
    'pagedown': 109,
    **{f'f{i}': 58 + i for i in range(1, 11)},
    'f11': 87,
    'f12': 88,
}


def parse_chord(value):
    """Parse a named physical chord; never depends on argparse or active layout."""
    try:
        codes = [KEYS[name.lower()] for name in value.split('+')]
    except KeyError as error:
        raise ValueError(f'unknown key: {error.args[0]}') from None
    if len(codes) != len(set(codes)):
        raise ValueError('a chord cannot hold the same key twice')
    return codes
