"""Native Windows observation, independent standard-control input and explicit host input.

The agent lane delegates to windows_agent.MessageLane (no host fallback); the host
lane uses SendInput only with explicit --host.
"""

import ctypes
import hashlib
import json
import math
import os
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from . import locking
from .files import atomic_json
from .platform_io import open_no_follow

EXTENDED = {
    96: 0x1C,
    97: 0x1D,
    98: 0x35,
    100: 0x38,
    102: 0x47,
    103: 0x48,
    104: 0x49,
    105: 0x4B,
    106: 0x4D,
    107: 0x4F,
    108: 0x50,
    109: 0x51,
    110: 0x52,
    111: 0x53,
    125: 0x5B,
    126: 0x5C,
    127: 0x5D,
}
BUTTON_FLAGS = {272: (0x0002, 0x0004), 273: (0x0008, 0x0010), 274: (0x0020, 0x0040)}
SPECIAL_VK = {99: 0x2C, 119: 0x13}


class MouseInput(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [('message', wintypes.DWORD), ('low', wintypes.WORD), ('high', wintypes.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput), ('hi', HardwareInput)]


class Input(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', wintypes.DWORD), ('data', InputUnion)]


def key_input(code, pressed):
    if type(code) is not int or type(pressed) is not bool:
        raise ValueError('A physical key requires an integer code and boolean state')
    flags = 0 if pressed else 2
    if code in SPECIAL_VK:
        return Input(type=1, ki=KeyboardInput(wVk=SPECIAL_VK[code], dwFlags=flags))
    if code in EXTENDED:
        scan, flags = EXTENDED[code], flags | 1
    elif 1 <= code <= 83 or code in (87, 88):
        scan = code
    elif 183 <= code <= 194:  # Linux F13..F24.
        return Input(type=1, ki=KeyboardInput(wVk=0x7C + code - 183, dwFlags=flags))
    else:
        raise ValueError(f'Physical key code {code} is unsupported on Windows')
    return Input(type=1, ki=KeyboardInput(wScan=scan, dwFlags=flags | 8))


def wheel_data(delta, axis, v120=None):
    """Windows wheel data for a ca scroll delta.

    ca deltas use Wayland axis units on every backend: 15 per wheel notch, positive is
    down or right. Windows uses 120 per notch and positive is up on the vertical axis.
    """
    amount = v120 or round(delta * 8)
    if axis == 'vertical':
        amount = -amount
    if not -2147483648 <= amount <= 2147483647:
        raise ValueError('Scroll exceeds native input range')
    return amount


class Win32:
    """Declare every pointer-sized ABI explicitly (64-bit HWNDs must not truncate)."""

    def __init__(self):
        if os.name != 'nt':
            raise ValueError('The win32 backend is available only on Windows')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        declarations = {
            'EnumWindows': ([self.enum_callback, wintypes.LPARAM], wintypes.BOOL),
            'IsWindow': ([wintypes.HWND], wintypes.BOOL),
            'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
            'IsWindowEnabled': ([wintypes.HWND], wintypes.BOOL),
            'GetWindowLongW': ([wintypes.HWND, ctypes.c_int], wintypes.LONG),
            'IsIconic': ([wintypes.HWND], wintypes.BOOL),
            'GetWindowTextLengthW': ([wintypes.HWND], ctypes.c_int),
            'GetWindowTextW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClientRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'ClientToScreen': ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            'GetWindowThreadProcessId': (
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            'GetForegroundWindow': ([], wintypes.HWND),
            'SetForegroundWindow': ([wintypes.HWND], wintypes.BOOL),
            'BringWindowToTop': ([wintypes.HWND], wintypes.BOOL),
            'AttachThreadInput': ([wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
            'GetCursorPos': ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            'GetAsyncKeyState': ([ctypes.c_int], ctypes.c_short),
            'GetSystemMetrics': ([ctypes.c_int], ctypes.c_int),
            'SendInput': ([wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int], wintypes.UINT),
            'GetKeyboardLayout': ([wintypes.DWORD], wintypes.HANDLE),
            'VkKeyScanExW': ([wintypes.WCHAR, wintypes.HANDLE], ctypes.c_short),
            'MapVirtualKeyExW': ([wintypes.UINT, wintypes.UINT, wintypes.HANDLE], wintypes.UINT),
            'OpenClipboard': ([wintypes.HWND], wintypes.BOOL),
            'CloseClipboard': ([], wintypes.BOOL),
            'EmptyClipboard': ([], wintypes.BOOL),
            'GetClipboardData': ([wintypes.UINT], wintypes.HANDLE),
            'SetClipboardData': ([wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
            'OpenInputDesktop': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'CloseDesktop': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        for name, args, result in (
            ('GlobalAlloc', [wintypes.UINT, ctypes.c_size_t], wintypes.HANDLE),
            ('GlobalLock', [wintypes.HANDLE], ctypes.c_void_p),
            ('GlobalUnlock', [wintypes.HANDLE], wintypes.BOOL),
            ('GlobalFree', [wintypes.HANDLE], wintypes.HANDLE),
            ('GlobalSize', [wintypes.HANDLE], ctypes.c_size_t),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        # Per-monitor physical client coordinates match ImageGrab and SendInput.
        self.user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.kernel.GetCurrentThreadId.argtypes = []
        self.kernel.GetCurrentThreadId.restype = wintypes.DWORD

    def focus_host(self, hwnd):
        """Explicit host activation only; independent input never calls this method."""
        foreground = self.user.GetForegroundWindow()
        other = self.user.GetWindowThreadProcessId(foreground, None) if foreground else 0
        current = self.kernel.GetCurrentThreadId()
        attached = other != current and other and self.user.AttachThreadInput(current, other, True)
        try:
            self.user.BringWindowToTop(hwnd)
            self.user.SetForegroundWindow(hwnd)
            until = time.monotonic() + 0.5
            while self.user.GetForegroundWindow() != hwnd and time.monotonic() < until:
                time.sleep(0.01)
            if self.user.GetForegroundWindow() != hwnd:
                raise PermissionError(
                    'Windows refused foreground activation; focus the target and retry'
                )
        finally:
            if attached:
                self.user.AttachThreadInput(current, other, False)

    def send(self, event):
        if self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise OSError('SendInput failed; the target may have a higher integrity level (UIPI)')

    def geometry(self, hwnd):
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not self.user.GetClientRect(hwnd, ctypes.byref(rect)) or not self.user.ClientToScreen(
            hwnd, ctypes.byref(origin)
        ):
            raise ValueError('Window closed or its client geometry is unavailable')
        return (origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)

    def identity(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return f'win-{int(hwnd):x}-{pid.value}', pid.value


class WindowsBackend:
    def __init__(self, lane):
        self.api, self.lane = Win32(), lane
        self.lease, self.target, self.identity, self.baseline = '', None, None, None
        self.reason, self.host_lock = None, None
        self.keys, self.buttons = set(), set()
        self.pointer = None
        self.cleanup_file = os.environ.get('CA_WIN32_CLEANUP_FILE')
        self.control_root = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'computer-artist'
        self.stop_file = self.control_root / 'host-stop.json'
        self.stop_stamp = None
        self.app_lease = None
        self.message_lane = None
        self.saved_clipboard = None
        self.monitor_selector = os.environ.get('CA_WINDOWS_MONITOR')

    def monitor_guard(self, geometry):
        if self.monitor_selector:
            from .windows_monitors import contains, select_monitor

            if not contains(select_monitor(self.api, self.monitor_selector), geometry):
                raise PermissionError(
                    'The target is outside the explicitly selected monitor; input/capture refused'
                )

    def capabilities(self):
        operations = [
            'windows',
            'capture',
            'capabilities',
            'ping',
            'session_status',
            'read_text',
            'accessible',
            'acquire',
            'release',
            'cancel',
            'move',
            'button',
            'key',
            'keymap',
            'scroll',
            'session_close',
            'takeover',
        ]
        if self.lane == 'agent':
            operations.append('write_text')
        else:
            operations += [
                'focus',
                'clipboard_get',
                'clipboard_set',
                'clipboard_save',
                'clipboard_restore',
            ]
        return {
            'ok': True,
            'backend': 'win32',
            'protocol': 3,
            'lane': self.lane,
            'host_pointer': self.lane == 'host',
            'keyboard': True,
            'clipboard': self.lane == 'host',
            'operations': operations,
            'coordinate_space': 'physical_client_pixels',
            'lanes': {
                'agent': {
                    'pointer': True,
                    'keyboard': True,
                    'delivery': ['standard_control_messages', 'ui_automation'],
                    'physical_keyboard': False,
                    'drawn_pointer': True,
                },
                'host': {'pointer': True, 'keyboard': True},
            },
            'limitations': [
                'Independent input uses control messages for standard Edit/Button controls and '
                'UI Automation patterns elsewhere; it is not a physical keyboard or second '
                'pointer, and canvases, raw-input apps and drag gestures are unsupported.',
                'Independent text into UI Automation elements appends to their value.',
                'Host input requires explicit --host and uses the real desktop pointer and keyboard.',
                'Capture renders the client area with PrintWindow; some GPU-rendered apps fall '
                'back to the visible screen region, which can include overlapping windows.',
                'Secure desktops and higher-integrity targets cannot be controlled.',
                'Private environments run in a Linux box (Docker or Podman); they test Linux apps.',
            ],
        }

    def windows(self):
        entries = []

        @self.api.enum_callback
        def visit(hwnd, _):
            if not self.api.user.IsWindowVisible(hwnd) or self.api.user.IsIconic(hwnd):
                return True
            size = self.api.user.GetWindowTextLengthW(hwnd)
            if not size:
                return True
            title, class_name = (
                ctypes.create_unicode_buffer(size + 1),
                ctypes.create_unicode_buffer(256),
            )
            self.api.user.GetWindowTextW(hwnd, title, len(title))
            self.api.user.GetClassNameW(hwnd, class_name, len(class_name))
            try:
                x, y, width, height = self.api.geometry(hwnd)
                identity, pid = self.api.identity(hwnd)
            except ValueError:
                return True
            if width <= 0 or height <= 0:
                return True
            entries.append(
                {
                    'id': identity,
                    'title': title.value,
                    'pid': pid,
                    'class': class_name.value,
                    'x': x,
                    'y': y,
                    'width': width,
                    'height': height,
                    'visible': True,
                    'native': True,
                    'observable': True,
                    'agent': bool(self.lane == 'agent' and self.lease and hwnd == self.target),
                    'host': bool(self.lane == 'host' and self.lease and hwnd == self.target),
                    'backend': 'win32',
                }
            )
            return True

        if not self.api.user.EnumWindows(visit, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        from .windows_agent import MessageLane

        foreground = self.api.user.GetForegroundWindow()
        human_pid = self.api.identity(foreground)[1] if foreground else None
        for window in entries:
            hwnd = int(window['id'].split('-')[1], 16)
            supported = bool(MessageLane(self.api, hwnd).controls())
            reasons = []
            if human_pid == window['pid']:
                reasons.append('human_focus_in_application')
            try:
                self.monitor_guard(tuple(window[key] for key in ('x', 'y', 'width', 'height')))
            except PermissionError:
                reasons.append('outside_selected_monitor')
            window['human_active'] = human_pid == window['pid']
            window['agent_restrictions'] = reasons
            window['acquirable'] = (
                not reasons if self.lane == 'agent' else 'outside_selected_monitor' not in reasons
            )
            window['independent_controls'] = supported
            window['agent_delivery'] = (
                ['standard_control_messages', 'ui_automation'] if supported else ['ui_automation']
            )
        generation = hashlib.sha256(
            json.dumps(
                [(w['id'], w['x'], w['y'], w['width'], w['height']) for w in entries]
            ).encode()
        ).hexdigest()
        return {'ok': True, 'generation': generation, 'windows': entries}

    def resolve(self, identity):
        window = next((w for w in self.windows()['windows'] if w['id'] == identity), None)
        if window is None:
            raise ValueError('Exact live window ID not found; run ca windows and rebind it')
        self.monitor_guard(tuple(window[key] for key in ('x', 'y', 'width', 'height')))
        return int(identity.split('-')[1], 16), window

    def require_host(self):
        if self.lane != 'host':
            raise PermissionError('Shared pointer/focus/clipboard access requires explicit --host')

    def secure_desktop(self):
        desktop = self.api.user.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            raise PermissionError('The interactive desktop is locked or unavailable')
        self.api.user.CloseDesktop(desktop)

    def guard(self, values):
        if not self.lease or values.get('lease') != self.lease:
            raise PermissionError("Input requires this connection's acquired window lease")
        self.secure_desktop()
        if self.stop_file.exists() and self.stop_file.read_bytes() != self.stop_stamp:
            self.revoke('explicit_stop')
            raise PermissionError('Host input was stopped by another command')
        if (
            not self.api.user.IsWindow(self.target)
            or self.api.identity(self.target)[0] != self.identity
        ):
            self.revoke('target_closed')
            raise ValueError('Target window closed or was replaced')
        if self.api.geometry(self.target) != self.baseline:
            self.revoke('geometry_changed')
            raise ValueError('Target geometry changed; observe again in a fresh run')
        self.monitor_guard(self.baseline)
        if self.lane == 'agent':
            foreground = self.api.user.GetForegroundWindow()
            if foreground and self.api.identity(foreground)[1] == self.api.identity(self.target)[1]:
                self.revoke('human_focus_in_application')
                raise PermissionError(
                    'Focus moved into the agent application (the human or an app activation); '
                    'input stopped'
                )
            return
        if self.api.user.GetForegroundWindow() != self.target:
            self.revoke('foreground_changed')
            raise PermissionError('Foreground changed; input stopped')
        if self.pointer is not None:
            point = wintypes.POINT()
            self.api.user.GetCursorPos(ctypes.byref(point))
            if (point.x, point.y) != self.pointer:
                self.revoke('human_pointer_moved')
                raise PermissionError('Human pointer moved; input stopped')

    def client_point(self, values):
        x, y = values['x'], values['y']
        left, top, width, height = self.baseline
        if any(isinstance(v, bool) or not math.isfinite(v) for v in (x, y)) or not (
            left <= x < left + width and top <= y < top + height
        ):
            raise ValueError('Point is outside the acquired client region')
        return x, y

    def save_cleanup(self):
        if self.cleanup_file:
            atomic_json(
                self.cleanup_file, {'keys': sorted(self.keys), 'buttons': sorted(self.buttons)}
            )

    def release_input(self):
        if self.lane == 'agent':
            if self.message_lane:
                self.message_lane.close()
                self.message_lane = None
            self.keys.clear()
            self.buttons.clear()
            return
        errors = []
        for code in list(self.keys):
            try:
                self.api.send(key_input(code, False))
                self.keys.remove(code)
            except OSError as error:
                errors.append(str(error))
        for code in list(self.buttons):
            try:
                self.api.send(Input(type=0, mi=MouseInput(dwFlags=BUTTON_FLAGS[code][1])))
                self.buttons.remove(code)
            except OSError as error:
                errors.append(str(error))
        self.save_cleanup()
        if errors:
            raise OSError('; '.join(errors))

    def revoke(self, reason=None):
        try:
            self.release_input()
        finally:
            self.lease, self.target, self.pointer = '', None, None
            self.reason = reason
            if self.host_lock is not None:
                os.close(self.host_lock)
                self.host_lock = None
            if self.app_lease is not None:
                os.close(self.app_lease)
                self.app_lease = None

    def acquire(self, identity):
        if self.lease:
            raise PermissionError('Release the existing lease before acquiring another target')
        self.secure_desktop()
        hwnd, window = self.resolve(identity)
        held = [
            code
            for code in (range(1, 256) if self.lane == 'host' else ())
            if self.api.user.GetAsyncKeyState(code) & 0x8000
        ]
        if held:
            # Counts only: which keys a human holds must not end up in run evidence.
            buttons = sum(code in (1, 2, 4, 5, 6) for code in held)
            raise PermissionError(
                'Release held human keys and mouse buttons before acquisition '
                f'({len(held) - buttons} keys, {buttons} mouse buttons held)'
            )
        self.control_root.mkdir(parents=True, exist_ok=True)
        if self.lane == 'host':
            # One real pointer and keyboard: one host lane at a time.
            lock = open_no_follow(self.control_root / 'host-input.lock', os.O_CREAT | os.O_RDWR)
            try:
                locking.flock(lock, locking.LOCK_EX | locking.LOCK_NB)
            except BlockingIOError:
                os.close(lock)
                raise PermissionError('The Windows host input lane is already busy') from None
            self.host_lock = lock
        try:
            self.app_lease = open_no_follow(
                self.control_root / f'app-{window["pid"]}.lock', os.O_CREAT | os.O_RDWR
            )
            try:
                locking.flock(self.app_lease, locking.LOCK_EX | locking.LOCK_NB)
            except BlockingIOError:
                raise PermissionError('Another lane already controls this application') from None
            if self.lane == 'agent':
                from .windows_agent import MessageLane

                foreground = self.api.user.GetForegroundWindow()
                if foreground and self.api.identity(foreground)[1] == window['pid']:
                    raise PermissionError('Keep human focus outside the agent application')
                self.message_lane = MessageLane(self.api, hwnd)
            elif self.api.user.GetForegroundWindow() != hwnd:
                self.api.focus_host(hwnd)
            if self.lane == 'host' and self.api.user.GetForegroundWindow() != hwnd:
                raise PermissionError(
                    'Windows refused foreground activation; focus the target and retry'
                )
            self.target, self.identity = hwnd, identity
            self.baseline = tuple(window[k] for k in ('x', 'y', 'width', 'height'))
            self.lease = uuid.uuid4().hex
            self.reason = None
            self.stop_stamp = self.stop_file.read_bytes() if self.stop_file.exists() else None
            if self.lane == 'host':
                self.save_cleanup()
        except BaseException:
            self.revoke()
            raise
        return {
            'ok': True,
            'lease': self.lease,
            'generation': self.windows()['generation'],
            'keyboard_ready': True,
        }

    def keymap(self):
        thread = self.api.user.GetWindowThreadProcessId(
            self.target or self.api.user.GetForegroundWindow(), None
        )
        layout = self.api.user.GetKeyboardLayout(thread)
        reverse = {0xE000 | scan: code for code, scan in EXTENDED.items()}
        characters = {}
        for number in range(32, 592):
            char = chr(number)
            combination = self.api.user.VkKeyScanExW(char, layout)
            if combination == -1:
                continue
            vk, modifiers = combination & 255, (combination >> 8) & 255
            if modifiers & ~7:
                continue
            scan = self.api.user.MapVirtualKeyExW(vk, 4, layout)
            code = reverse.get(scan, scan if 1 <= scan <= 83 or scan in (87, 88) else None)
            if code is not None:
                characters[char] = (
                    ([42] if modifiers & 1 else [])
                    + ([29] if modifiers & 2 else [])
                    + ([56] if modifiers & 4 else [])
                    + [code]
                )
        return {
            'ok': True,
            'characters': characters,
            'layout': str(layout),
            'layout_name': f'Windows HKL {layout:x}',
        }

    def clipboard_save(self):
        """Keep a private copy of the user's clipboard (all bounded formats) for restore."""
        self.require_host()
        from .windows_clipboard import ClipboardSnapshot, NotPreservable

        self.drop_saved_clipboard()
        snapshot = ClipboardSnapshot(self.api)
        try:
            snapshot.__enter__()
        except NotPreservable as reason:
            return {'ok': True, 'saved': False, 'reason': str(reason)}
        self.saved_clipboard = snapshot
        return {'ok': True, 'saved': True}

    def clipboard_restore(self):
        """Put the saved clipboard back unless someone copied something after our paste."""
        self.require_host()
        snapshot, self.saved_clipboard = self.saved_clipboard, None
        if snapshot is None:
            return {'ok': True, 'restored': False, 'reason': 'Nothing was saved'}
        try:
            snapshot.restore()
        except (RuntimeError, OSError) as reason:
            return {'ok': True, 'restored': False, 'reason': str(reason)}
        return {'ok': True, 'restored': True}

    def drop_saved_clipboard(self):
        if self.saved_clipboard is not None:
            self.saved_clipboard.release_graphics(self.saved_clipboard.formats)
            self.saved_clipboard = None

    def clipboard(self, text=None):
        self.require_host()
        # No owner window: the target application must not become the clipboard owner.
        if not self.api.user.OpenClipboard(None):
            raise OSError('Clipboard is busy; retry after its owner releases it')
        memory = None
        try:
            if text is None:
                handle = self.api.user.GetClipboardData(13)
                if not handle:
                    return {'ok': True, 'text': ''}
                size = self.api.kernel.GlobalSize(handle)
                if size > 32768:
                    raise ValueError('Clipboard text exceeds the bounded read size')
                pointer = self.api.kernel.GlobalLock(handle)
                if not pointer:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    value = ctypes.string_at(pointer, size).decode('utf-16-le').split('\0', 1)[0]
                    if len(value.encode('utf-8')) > 8192:
                        raise ValueError('Clipboard text exceeds 8192 UTF-8 bytes')
                    return {'ok': True, 'text': value}
                finally:
                    self.api.kernel.GlobalUnlock(handle)
            if not isinstance(text, str) or '\0' in text or len(text.encode('utf-8')) > 8192:
                raise ValueError('Clipboard text must be at most 8192 UTF-8 bytes without NUL')
            payload = (text + '\0').encode('utf-16-le')
            memory = self.api.kernel.GlobalAlloc(2, len(payload))
            if not memory:
                raise ctypes.WinError(ctypes.get_last_error())
            pointer = self.api.kernel.GlobalLock(memory)
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(pointer, payload, len(payload))
            finally:
                self.api.kernel.GlobalUnlock(memory)
            if not self.api.user.EmptyClipboard() or not self.api.user.SetClipboardData(13, memory):
                raise ctypes.WinError(ctypes.get_last_error())
            memory = None  # Clipboard now owns the allocation.
            return {'ok': True}
        finally:
            if memory:
                self.api.kernel.GlobalFree(memory)
            self.api.user.CloseClipboard()
            if text is not None and self.saved_clipboard is not None:
                # Our own write is the expected state; any later copy keeps the user's data.
                self.saved_clipboard.checkpoint()

    def request(self, op, **values):
        if op == 'capabilities':
            return self.capabilities()
        if op == 'windows':
            return self.windows()
        if op == 'read_text':
            from .windows_agent import EDIT_CLASSES, MessageLane

            hwnd, window = self.resolve(values['window'])
            lane = MessageLane(self.api, hwnd)
            if not any(lane.class_name(h) in EDIT_CLASSES for h in lane.controls()):
                return self.read_element_text(hwnd, window)
            control = lane.edit()
            size = lane.message(control, 0x000E)
            if size > 65535:
                raise ValueError('Control text exceeds the 65535-character read limit')
            buffer = ctypes.create_unicode_buffer(size + 1)
            lane.message(control, 0x000D, len(buffer), ctypes.addressof(buffer))
            return {
                'ok': True,
                'window': values['window'],
                'text': buffer.value,
                'source': 'win32_standard_edit_control',
            }
        if op == 'accessible':
            return self.accessible(values)
        if op == 'capture':
            from PIL import ImageGrab

            from .windows_capture import print_window

            self.secure_desktop()
            hwnd, window = self.resolve(values['window'])
            x, y, width, height = self.api.geometry(hwnd)
            destination = Path(values['path'])
            # The window's own rendering: no overlapping windows and no agent pointer.
            image, source = print_window(hwnd, width, height), 'win32_print_window'
            if image is None:
                image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
                source = 'win32_visible_client_region'
            with destination.open('xb') as stream:
                try:
                    image.save(stream, 'PNG')
                except BaseException:
                    stream.close()
                    destination.unlink(missing_ok=True)
                    raise
            return {
                'ok': True,
                'path': str(destination),
                'source': source,
                'window': window,
                'may_include_overlapping_windows': source == 'win32_visible_client_region',
            }
        if op == 'ping':
            if self.lease:
                self.guard({'lease': self.lease})
            return {'ok': True, 'lease': self.lease, 'keyboard_ready': bool(self.lease)}
        if op == 'session_status':
            return {
                'ok': True,
                'scope': 'connection',
                'lanes': {
                    'agent': {
                        'busy': bool(self.lease and self.lane == 'agent'),
                        'supported': True,
                        'stop_reason': self.reason if self.lane == 'agent' else None,
                    },
                    'host': {
                        'busy': bool(self.lease and self.lane == 'host'),
                        'stop_reason': self.reason if self.lane == 'host' else None,
                    },
                },
            }
        if op == 'acquire':
            return self.acquire(values['window'])
        if op in ('release', 'cancel', 'session_close', 'takeover'):
            if op in ('session_close', 'takeover'):
                self.control_root.mkdir(parents=True, exist_ok=True)
                atomic_json(self.stop_file, {'id': uuid.uuid4().hex})
            self.revoke('explicit_stop' if op in ('session_close', 'takeover') else None)
            return {'ok': True}
        if op == 'keymap':
            return self.keymap()
        if op == 'clipboard_get':
            return self.clipboard()
        if op == 'clipboard_set' and not self.lease:
            return self.clipboard(values['text'])
        if op == 'clipboard_save':
            return self.clipboard_save()
        if op == 'clipboard_restore':
            return self.clipboard_restore()
        self.guard(values)
        if self.lane == 'agent':
            try:
                reply = self.agent_operation(op, values)
            except Exception as error:
                if type(error).__name__ != 'COMError':
                    raise
                # A UI Automation element vanished or refused; report it like any refusal.
                raise ValueError(f'UI Automation call failed: {error}; no host fallback') from None
            self.guard(values)
            return {'ok': True, **reply}
        if op == 'focus':
            if values['window'] != self.identity:
                raise PermissionError('Focus requires the acquired target')
        elif op == 'move':
            x, y = self.client_point(values)
            vx, vy = self.api.user.GetSystemMetrics(76), self.api.user.GetSystemMetrics(77)
            vw, vh = self.api.user.GetSystemMetrics(78), self.api.user.GetSystemMetrics(79)
            if not (vx <= x < vx + vw and vy <= y < vy + vh):
                raise ValueError('Pointer position is outside the virtual screen')
            self.api.send(
                Input(
                    type=0,
                    mi=MouseInput(
                        dx=round((x - vx) * 65535 / max(1, vw - 1)),
                        dy=round((y - vy) * 65535 / max(1, vh - 1)),
                        dwFlags=0xC001,
                    ),
                )
            )
            point = wintypes.POINT()
            self.api.user.GetCursorPos(ctypes.byref(point))
            self.pointer = (point.x, point.y)
        elif op == 'button':
            code, pressed = values['code'], values['pressed']
            if code not in BUTTON_FLAGS or type(pressed) is not bool:
                raise ValueError('Unsupported mouse button')
            if pressed:
                self.buttons.add(code)
                self.save_cleanup()
            self.api.send(
                Input(type=0, mi=MouseInput(dwFlags=BUTTON_FLAGS[code][0 if pressed else 1]))
            )
            if not pressed:
                self.buttons.discard(code)
                self.save_cleanup()
        elif op == 'key':
            code, pressed = values['code'], values['pressed']
            event = key_input(code, pressed)
            if pressed:
                self.keys.add(code)
                self.save_cleanup()
            self.api.send(event)
            if not pressed:
                self.keys.discard(code)
                self.save_cleanup()
        elif op == 'scroll':
            axis, delta = values.get('axis', 'vertical'), values['delta']
            if (
                axis not in ('vertical', 'horizontal')
                or isinstance(delta, bool)
                or not math.isfinite(delta)
            ):
                raise ValueError('Invalid scroll')
            amount = wheel_data(delta, axis, values.get('v120'))
            self.api.send(
                Input(
                    type=0,
                    mi=MouseInput(
                        mouseData=amount & 0xFFFFFFFF,
                        dwFlags=0x0800 if axis == 'vertical' else 0x1000,
                    ),
                )
            )
        elif op == 'clipboard_set':
            return self.clipboard(values['text'])
        else:
            raise ValueError(f'Operation {op!r} is unsupported by the Windows backend')
        return {'ok': True}

    def agent_operation(self, op, values):
        lane = self.message_lane
        if op == 'move':
            return {'delivery': lane.pick(*self.client_point(values))}
        if op == 'button':
            return {'action': lane.button(values['code'], values['pressed'])}
        if op == 'key':
            return {'action': lane.key(values['code'], values['pressed'], self.keymap())}
        if op == 'write_text':
            return {'delivery': lane.write(values['text'])}
        if op == 'scroll':
            axis = values.get('axis', 'vertical')
            delta = values['delta']
            if (
                axis not in ('vertical', 'horizontal')
                or isinstance(delta, bool)
                or not (math.isfinite(delta) and delta)
            ):
                raise ValueError('Invalid scroll')
            return {'notches': lane.scroll(delta, axis), 'delivery': 'ui_automation'}
        raise ValueError(f'Independent operation {op!r} is unsupported; no host fallback')

    def accessible(self, values):
        """Read-only UI Automation elements of a window, in window-content coordinates."""
        from .windows_uia import Automation

        hwnd, _window = self.resolve(values['window'])
        x, y, _width, _height = self.api.geometry(hwnd)
        role, name = values.get('role'), values.get('name')
        max_nodes = values.get('max_nodes', 2000)
        if type(max_nodes) is not int or not 1 <= max_nodes <= 20000:
            raise ValueError('max_nodes must be 1–20000')
        automation = self.message_lane.uia() if self.message_lane else Automation()
        result = automation.elements(hwnd, (x, y), role=role, name=name, max_nodes=max_nodes)
        return {'ok': True, 'source': 'ui_automation', **result}

    def read_element_text(self, hwnd, window):
        """The value of the window's single editable UI Automation element."""
        from .windows_uia import Automation

        x, y, _w, _h = self.api.geometry(hwnd)
        found = Automation().elements(hwnd, (x, y), max_nodes=4000)['elements']
        editable = [e for e in found if 'editable' in e['states'] and 'showing' in e['states']]
        if len(editable) != 1:
            raise ValueError(
                f'Found {len(editable)} editable elements; read a specific one with ca a11y'
            )
        return {
            'ok': True,
            'window': window['id'],
            'text': editable[0]['text'] or '',
            'source': 'ui_automation_value',
        }

    def close(self):
        self.drop_saved_clipboard()
        self.revoke()


def cleanup_input(path):
    """Release only keys/buttons recorded by this supervised Windows worker."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return
    fd = open_no_follow(path, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        encoded = stream.read(4097)
    if len(encoded) > 4096:
        raise ValueError('Worker input cleanup record exceeds 4 KiB')
    state = json.loads(encoded)
    if not isinstance(state, dict) or any(
        not isinstance(state.get(field), list) or len(state[field]) > 256
        for field in ('keys', 'buttons')
    ):
        raise ValueError('Invalid worker input cleanup record')
    events = [key_input(code, False) for code in state['keys']]
    for code in state['buttons']:
        if type(code) is not int or code not in BUTTON_FLAGS:
            raise ValueError('Invalid worker mouse cleanup record')
        events.append(Input(type=0, mi=MouseInput(dwFlags=BUTTON_FLAGS[code][1])))
    api = Win32()
    for event in events:
        api.send(event)
    atomic_json(path, {'keys': [], 'buttons': []})
