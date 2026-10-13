"""Independent, targeted Win32 control messages; never inject into the human seat.

This lane supports standard Edit/Button/scrollbar controls. It does not pretend
that arbitrary applications accept background messages or independent physical keys.
"""

import ctypes
from ctypes import wintypes

from .input import KEYS

EDIT_CLASSES = {'edit', 'richedit20w', 'richedit50w', 'richeditd2dpt'}
BUTTON_CLASSES = {'button'}


class MessageLane:
    def __init__(self, api, hwnd):
        self.api, self.hwnd = api, hwnd
        self.control = None
        self.modifiers = set()
        self.position = None
        self.pointer = None
        self.api.user.EnumChildWindows.argtypes = [
            wintypes.HWND,
            self.api.enum_callback,
            wintypes.LPARAM,
        ]
        self.api.user.EnumChildWindows.restype = wintypes.BOOL
        self.api.user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.api.user.ScreenToClient.restype = wintypes.BOOL
        self.api.user.ChildWindowFromPointEx.argtypes = [
            wintypes.HWND,
            wintypes.POINT,
            wintypes.UINT,
        ]
        self.api.user.ChildWindowFromPointEx.restype = wintypes.HWND
        self.api.user.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.api.user.SendMessageTimeoutW.restype = wintypes.LPARAM
        self.api.user.GetParent.argtypes = [wintypes.HWND]
        self.api.user.GetParent.restype = wintypes.HWND
        self.api.user.GetDlgCtrlID.argtypes = [wintypes.HWND]
        self.api.user.GetDlgCtrlID.restype = ctypes.c_int

    def class_name(self, hwnd):
        value = ctypes.create_unicode_buffer(256)
        self.api.user.GetClassNameW(hwnd, value, len(value))
        return value.value.casefold()

    def controls(self):
        controls = []
        if self.class_name(self.hwnd) in EDIT_CLASSES | BUTTON_CLASSES:
            controls.append(self.hwnd)

        @self.api.enum_callback
        def visit(hwnd, _):
            if (
                self.api.user.IsWindowVisible(hwnd)
                and self.class_name(hwnd) in EDIT_CLASSES | BUTTON_CLASSES
            ):
                controls.append(hwnd)
            return True

        self.api.user.EnumChildWindows(self.hwnd, visit, 0)
        return controls

    def message(self, hwnd, message, wparam=0, lparam=0):
        if (
            not self.api.user.IsWindow(hwnd)
            or self.api.identity(hwnd)[1] != self.api.identity(self.hwnd)[1]
        ):
            raise ValueError('The exact target control closed or changed process')
        result = ctypes.c_size_t()
        if not self.api.user.SendMessageTimeoutW(
            hwnd, message, wparam, lparam, 0x22, 500, ctypes.byref(result)
        ):
            raise OSError('Targeted control message failed or timed out; no host fallback')
        return result.value

    def pick(self, x, y):
        hwnd = self.hwnd
        for _ in range(32):
            point = wintypes.POINT(round(x), round(y))
            if not self.api.user.ScreenToClient(hwnd, ctypes.byref(point)):
                raise ValueError('Control geometry is unavailable')
            child = self.api.user.ChildWindowFromPointEx(hwnd, point, 7)
            if not child or child == hwnd:
                break
            hwnd = child
        if self.class_name(hwnd) not in EDIT_CLASSES | BUTTON_CLASSES:
            raise ValueError(
                'Independent Windows input supports standard Edit/Button controls here; no host fallback'
            )
        self.control = hwnd
        self.position = (round(x), round(y))
        if self.pointer is None:
            from .windows_pointer import AgentPointer

            self.pointer = AgentPointer()
        self.pointer.move(round(x), round(y))

    def edit(self, *, editable=False):
        if self.control is None:
            edits = [h for h in self.controls() if self.class_name(h) in EDIT_CLASSES]
            if len(edits) != 1:
                raise ValueError(
                    'Choose one supported edit control with an independent move/click before typing'
                )
            self.control = edits[0]
        if self.class_name(self.control) not in EDIT_CLASSES:
            raise ValueError('Selected control does not support independent text entry')
        if editable and (
            not self.api.user.IsWindowEnabled(self.control)
            or self.api.user.GetWindowLongW(self.control, -16) & 0x800
        ):
            raise ValueError('The selected edit control is disabled or read-only')
        return self.control

    def write(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 4096 or '\0' in text:
            raise ValueError('Independent text requires 1–4096 characters without NUL')
        hwnd = self.edit(editable=True)
        payload = ctypes.create_unicode_buffer(
            text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')
        )
        self.message(hwnd, 0x00C2, 1, ctypes.addressof(payload))  # EM_REPLACESEL, undoable.

    def button(self, code, pressed):
        if code != 272 or type(pressed) is not bool or not self.control:
            raise ValueError('Independent controls support a selected control and the left button')
        if not pressed:
            return
        kind = self.class_name(self.control)
        if not self.api.user.IsWindowEnabled(self.control):
            raise ValueError('The selected control is disabled')
        if kind in BUTTON_CLASSES:
            if self.api.user.GetWindowLongW(self.control, -16) & 15 not in (0, 1):
                raise ValueError('Independent checkbox/radio state changes are not implemented')
            # Notify the parent directly; BM_CLICK/WM_LBUTTONDOWN may call SetFocus.
            identity = self.api.user.GetDlgCtrlID(self.control)
            self.message(self.api.user.GetParent(self.control), 0x0111, identity, self.control)
        elif kind in EDIT_CLASSES:
            if self.position is None:
                raise ValueError('Observe and move to a control before clicking')
            point = wintypes.POINT(*self.position)
            self.api.user.ScreenToClient(self.control, ctypes.byref(point))
            if kind == 'edit':
                position = (
                    self.message(
                        self.control, 0x00D7, 0, (point.y & 65535) << 16 | (point.x & 65535)
                    )
                    & 65535
                )
            else:
                position = self.message(self.control, 0x0439, 0, ctypes.addressof(point))
            self.message(self.control, 0x00B1, position, position)  # EM_SETSEL, no focus change.

    def key(self, code, pressed, keymap):
        if type(code) is not int or type(pressed) is not bool:
            raise ValueError('Physical key code and pressed state are required')
        if code in (29, 42, 56, 97, 54, 100):
            (self.modifiers.add if pressed else self.modifiers.discard)(code)
            return
        if not pressed:
            return
        hwnd = self.edit(editable=True)
        if self.modifiers & {29, 97}:
            if code == KEYS['a']:
                self.message(hwnd, 0x00B1, 0, -1)
                return
            if code == KEYS['z']:
                self.message(hwnd, 0x00C7)
                return
            raise ValueError(
                'This independent control chord is unsupported; no shared clipboard or host fallback'
            )
        if self.modifiers & {56, 100}:
            raise ValueError('Independent Alt shortcuts are unsupported')
        if code in (14, 28, 15):
            self.message(hwnd, 0x0102, {14: 8, 28: 13, 15: 9}[code], 1)
            return
        if code in (102, 103, 104, 105, 106, 107, 108, 109, 111):
            if self.modifiers:
                raise ValueError('Independent modified navigation is unsupported')
            vk = {
                102: 0x24,
                103: 0x26,
                104: 0x21,
                105: 0x25,
                106: 0x27,
                107: 0x23,
                108: 0x28,
                109: 0x22,
                111: 0x2E,
            }[code]
            self.message(hwnd, 0x0100, vk, 1)
            self.message(hwnd, 0x0101, vk, 0xC0000001)
            return
        expected = ([42] if self.modifiers & {42, 54} else []) + [code]
        character = next(
            (c for c, codes in keymap['characters'].items() if codes == expected), None
        )
        if character is None:
            raise ValueError('Independent key has no supported character mapping')
        self.write(character)

    def close(self):
        if self.pointer:
            self.pointer.close()
        self.modifiers.clear()
