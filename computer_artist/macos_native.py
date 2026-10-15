"""Native macOS event transport; background delivery never uses the HID event tap."""

import ctypes
import sys
import time
import unicodedata

# Public input remains Linux evdev codes; values are macOS virtual key codes.
KEYCODES = {
    1: 53,
    2: 18,
    3: 19,
    4: 20,
    5: 21,
    6: 23,
    7: 22,
    8: 26,
    9: 28,
    10: 25,
    11: 29,
    12: 27,
    13: 24,
    14: 51,
    15: 48,
    16: 12,
    17: 13,
    18: 14,
    19: 15,
    20: 17,
    21: 16,
    22: 32,
    23: 34,
    24: 31,
    25: 35,
    26: 33,
    27: 30,
    28: 36,
    29: 59,
    30: 0,
    31: 1,
    32: 2,
    33: 3,
    34: 5,
    35: 4,
    36: 38,
    37: 40,
    38: 37,
    39: 41,
    40: 39,
    41: 50,
    42: 56,
    43: 42,
    44: 6,
    45: 7,
    46: 8,
    47: 9,
    48: 11,
    49: 45,
    50: 46,
    51: 43,
    52: 47,
    53: 44,
    54: 60,
    55: 67,
    56: 58,
    57: 49,
    58: 57,
    59: 122,
    60: 120,
    61: 99,
    62: 118,
    63: 96,
    64: 97,
    65: 98,
    66: 100,
    67: 101,
    68: 109,
    69: 71,
    71: 89,
    72: 91,
    73: 92,
    74: 78,
    75: 86,
    76: 87,
    77: 88,
    78: 69,
    79: 83,
    80: 84,
    81: 85,
    82: 82,
    83: 65,
    87: 103,
    88: 111,
    96: 76,
    97: 62,
    98: 75,
    100: 61,
    102: 115,
    103: 126,
    104: 116,
    105: 123,
    106: 124,
    107: 119,
    108: 125,
    109: 121,
    110: 114,
    111: 117,
    113: 74,
    114: 73,
    115: 72,
    125: 55,
    126: 54,
    183: 105,
    184: 107,
    185: 113,
    186: 106,
    187: 64,
    188: 79,
    189: 80,
    190: 90,
}
MODIFIERS = {
    29: 1 << 18,
    97: 1 << 18,
    42: 1 << 17,
    54: 1 << 17,
    56: 1 << 19,
    100: 1 << 19,
    125: 1 << 20,
    126: 1 << 20,
}


def _function(library, name, arguments, result):
    try:
        function = getattr(library, name)
    except AttributeError:
        raise ValueError(f'Required macOS native symbol {name} is unavailable') from None
    function.argtypes, function.restype = arguments, result
    return function


class Native:
    def __init__(self):
        if sys.platform != 'darwin':
            raise ValueError('The macOS native backend is available only on macOS')
        try:
            import AppKit
            import Quartz
        except ImportError as error:
            raise ValueError(
                'Install pyobjc-framework-Cocoa and pyobjc-framework-Quartz'
            ) from error
        self.q, self.appkit = Quartz, AppKit
        try:
            sky = ctypes.CDLL('/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight')
            application = ctypes.CDLL(
                '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
            )
            core = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        except OSError as error:
            raise ValueError(f'Cannot load macOS event frameworks: {error}') from error
        pointer = ctypes.c_void_p
        self._post = _function(sky, 'SLEventPostToPid', [ctypes.c_int, pointer], None)
        self._field = _function(
            sky, 'SLEventSetIntegerValueField', [pointer, ctypes.c_uint32, ctypes.c_int64], None
        )
        location_library = core if hasattr(core, 'CGEventSetWindowLocation') else sky
        self._location = _function(
            location_library,
            'CGEventSetWindowLocation',
            [pointer, ctypes.c_double, ctypes.c_double],
            None,
        )
        self._process = _function(
            application, 'GetProcessForPID', [ctypes.c_int, pointer], ctypes.c_int
        )
        self._record = _function(sky, 'SLPSPostEventRecordTo', [pointer, pointer], ctypes.c_int)
        state = getattr(Quartz, 'kCGEventSourceStatePrivate', None)
        if state is None:
            raise ValueError(
                'Private macOS event-source state is unavailable; refusing shared state'
            )
        self.source = Quartz.CGEventSourceCreate(state)
        if self.source is None:
            raise ValueError('Cannot create private macOS event source')
        self.group = time.time_ns() % 1_000_000_000

    def front_pid(self):
        application = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if application is None:
            raise ValueError('Cannot identify the foreground application')
        return int(application.processIdentifier())

    def cursor(self):
        event = self.q.CGEventCreate(None)
        if event is None:
            raise ValueError('Cannot read the desktop cursor location')
        point = self.q.CGEventGetLocation(event)
        return float(point.x), float(point.y)

    def session_ready(self):
        session = self.q.CGSessionCopyCurrentDictionary()
        if not session or session.get('CGSSessionScreenIsLocked', False):
            raise PermissionError('The macOS desktop is locked or unavailable')
        if not session.get(self.q.kCGSessionOnConsoleKey, False):
            raise PermissionError('The macOS desktop is not the active console session')
        if not session.get(self.q.kCGSessionLoginDoneKey, False):
            raise PermissionError('The macOS login session is not ready')
        return True

    def process_identity(self, pid):
        application = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        launched = application.launchDate() if application is not None else None
        if launched is None:
            raise ValueError('Cannot establish the target process launch identity')
        return f'{int(pid)}-{launched.timeIntervalSince1970():.6f}'

    def windows(self):
        q = self.q
        return list(
            q.CGWindowListCopyWindowInfo(
                q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
                q.kCGNullWindowID,
            )
            or []
        )

    def prepare(self, pid, wid):
        # A target-only focus record; never deactivate or activate the human application.
        process = (ctypes.c_uint8 * 8)()
        if self._process(pid, process) != 0:
            raise ValueError('Cannot resolve target process for background focus preparation')
        record = (ctypes.c_uint8 * 0xF8)()
        record[0x04], record[0x08], record[0x8A] = 0xF8, 0x0D, 1
        for offset, byte in enumerate(int(wid).to_bytes(4, 'little')):
            record[0x3C + offset] = byte
        if self._record(process, record) != 0:
            raise ValueError('macOS refused target-only background focus preparation')

    def _stamp(self, event, pid, wid, origin, point, clicks=0, button=0, subtype=3):
        pointer = event.__c_void_p__().value
        for field, value in (
            (1, clicks),
            (3, button),
            (7, subtype),
            (40, pid),
            (51, wid),
            (91, wid),
            (92, wid),
            (58, self.group),
        ):
            self._field(pointer, field, value)
        self._location(pointer, point[0] - origin[0], point[1] - origin[1])
        return pointer

    def mouse(self, pid, wid, origin, point, kind, button=0, clicks=0, flags=0, host=False):
        from .macos_activity import EVENT_TAG

        q = self.q
        names = {
            0: ('LeftMouseDown', 'LeftMouseUp', 'LeftMouseDragged'),
            1: ('RightMouseDown', 'RightMouseUp', 'RightMouseDragged'),
            2: ('OtherMouseDown', 'OtherMouseUp', 'OtherMouseDragged'),
        }
        if button not in names or kind not in ('move', 'down', 'up', 'drag'):
            raise ValueError('Unsupported macOS mouse event')
        suffix = (
            'MouseMoved' if kind == 'move' else names[button][('down', 'up', 'drag').index(kind)]
        )
        event = q.CGEventCreateMouseEvent(
            self.source, getattr(q, f'kCGEvent{suffix}'), point, button
        )
        if event is None:
            raise ValueError('Cannot create macOS mouse event')
        q.CGEventSetFlags(event, flags)
        q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, EVENT_TAG)
        if host:
            q.CGEventSetIntegerValueField(event, q.kCGMouseEventClickState, clicks)
            q.CGEventPost(q.kCGHIDEventTap, event)
        else:
            pointer = self._stamp(
                event, pid, wid, origin, point, clicks, button, 3 if kind == 'move' else 0
            )
            self._post(pid, pointer)

    def key(self, pid, code, pressed, flags, host=False):
        from .macos_activity import EVENT_TAG

        if code not in KEYCODES:
            raise ValueError(f'Linux key code {code} has no supported macOS mapping')
        q = self.q
        event = q.CGEventCreateKeyboardEvent(self.source, KEYCODES[code], pressed)
        if event is None:
            raise ValueError('Cannot create macOS keyboard event')
        q.CGEventSetFlags(event, flags)
        q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, EVENT_TAG)
        if host:
            q.CGEventPost(q.kCGHIDEventTap, event)
        else:
            q.CGEventPostToPid(pid, event)

    def scroll(self, pid, wid, origin, point, delta, axis, flags=0, host=False):
        from .macos_activity import EVENT_TAG

        if axis not in ('vertical', 'horizontal'):
            raise ValueError('Scroll axis must be vertical or horizontal')
        q = self.q
        amount = round(-delta)
        if not amount:
            raise ValueError('macOS pixel scrolling requires a nonzero rounded delta')
        event = q.CGEventCreateScrollWheelEvent(
            self.source,
            q.kCGScrollEventUnitPixel,
            2,
            amount if axis == 'vertical' else 0,
            amount if axis == 'horizontal' else 0,
        )
        if event is None:
            raise ValueError('Cannot create macOS scroll event')
        q.CGEventSetLocation(event, point)
        q.CGEventSetFlags(event, flags)
        q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, EVENT_TAG)
        if host:
            q.CGEventPost(q.kCGHIDEventTap, event)
        else:
            self._post(pid, self._stamp(event, pid, wid, origin, point))

    def keymap(self):
        carbon = ctypes.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        pointer, integer = ctypes.c_void_p, ctypes.c_uint32
        copy = _function(carbon, 'TISCopyCurrentKeyboardLayoutInputSource', [], pointer)
        get = _function(carbon, 'TISGetInputSourceProperty', [pointer, pointer], pointer)
        release = _function(core, 'CFRelease', [pointer], None)
        data_bytes = _function(core, 'CFDataGetBytePtr', [pointer], pointer)
        string = _function(
            core, 'CFStringGetCString', [pointer, pointer, ctypes.c_long, integer], ctypes.c_bool
        )
        keyboard_type = _function(carbon, 'LMGetKbdType', [], ctypes.c_uint8)
        translate = _function(
            carbon,
            'UCKeyTranslate',
            [
                pointer,
                ctypes.c_uint16,
                ctypes.c_uint16,
                integer,
                integer,
                integer,
                ctypes.POINTER(integer),
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_uint16),
            ],
            ctypes.c_int32,
        )
        source = copy()
        if not source:
            raise ValueError('Cannot read the macOS keyboard layout')
        try:

            def property_value(name):
                try:
                    token = pointer.in_dll(carbon, name)
                except ValueError:
                    raise ValueError(f'macOS keyboard layout property {name} unavailable') from None
                return get(source, token)

            layout = property_value('kTISPropertyUnicodeKeyLayoutData')
            if not layout:
                raise ValueError('Current macOS keyboard layout has no Unicode key translation')
            layout_bytes = data_bytes(layout)
            if not layout_bytes:
                raise ValueError('Current macOS Unicode keyboard layout is unavailable')

            def label(name):
                value = property_value(name)
                buffer = ctypes.create_string_buffer(4096)
                return (
                    buffer.value.decode('utf-8')
                    if value and string(value, buffer, len(buffer), 0x08000100)
                    else None
                )

            characters = {}
            # Carbon modifier bits are shifted by eight for UCKeyTranslate.
            for modifiers, chord in ((0, []), (2, [42]), (8, [56]), (10, [42, 56])):
                for linux, virtual in KEYCODES.items():
                    if linux in MODIFIERS or linux == 58:
                        continue
                    state, length = integer(), ctypes.c_ulong()
                    output = (ctypes.c_uint16 * 8)()
                    status = translate(
                        layout_bytes,
                        virtual,
                        0,
                        modifiers,
                        keyboard_type(),
                        0,
                        ctypes.byref(state),
                        len(output),
                        ctypes.byref(length),
                        output,
                    )
                    if status or state.value:
                        continue
                    status = translate(
                        layout_bytes,
                        virtual,
                        0,
                        modifiers,
                        keyboard_type(),
                        1,
                        ctypes.byref(state),
                        len(output),
                        ctypes.byref(length),
                        output,
                    )
                    if status or state.value or not 0 < length.value <= len(output):
                        continue
                    raw = bytes(memoryview(output).cast('B')[: length.value * 2])
                    character = raw.decode('utf-16-le', errors='ignore')
                    if (
                        len(character) == 1
                        and character.isprintable()
                        and not unicodedata.category(character).startswith('C')
                    ):
                        characters.setdefault(character, [*chord, linux])
            return {
                'ok': True,
                'characters': characters,
                'layout': label('kTISPropertyInputSourceID'),
                'layout_name': label('kTISPropertyLocalizedName'),
            }
        finally:
            release(source)
