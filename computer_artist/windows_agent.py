"""Independent input on Windows without the human's pointer, keyboard or foreground.

Two deliveries, chosen per element under the agent pointer:

- Standard Edit/RichEdit controls and push buttons get targeted Win32 messages
  (caret-position text insertion, undo, navigation keys, button notification).
- Every other element goes through UI Automation patterns (invoke, toggle,
  select, expand/collapse, value, scroll), which WPF, WinUI, UWP, Qt, Chromium and
  Electron applications implement to varying degrees.

Neither is a physical keyboard or a real second pointer: applications that read raw
input, the cursor position or their own canvas cannot be driven this way. Anything
unsupported is refused; there is never a host fallback. The animated pointer is
drawn by this process and never receives input.
"""

import ctypes
from ctypes import wintypes

from .input import KEYS

EDIT_CLASSES = {'edit', 'richedit20w', 'richedit50w', 'richeditd2dpt'}
BUTTON_CLASSES = {'button'}
MODIFIERS = (29, 42, 56, 97, 54, 100)
NAVIGATION = {102: 0x24, 103: 0x26, 104: 0x21, 105: 0x25, 106: 0x27, 107: 0x23,
              108: 0x28, 109: 0x22, 111: 0x2E}  # fmt: skip


class MessageLane:
    def __init__(self, api, hwnd):
        self.api, self.hwnd = api, hwnd
        self.control = None  # A standard control driven by messages.
        self.element = None  # A UI Automation element driven by patterns.
        self.automation = None
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

    def uia(self):
        """The UI Automation client, created on first use (raises Unavailable)."""
        if getattr(self, 'automation', None) is None:
            from .windows_uia import Automation

            self.automation = Automation()
        return self.automation

    def class_name(self, hwnd):
        value = ctypes.create_unicode_buffer(256)
        self.api.user.GetClassNameW(hwnd, value, len(value))
        return value.value.casefold()

    def supported(self, hwnd):
        """Edit/RichEdit, or a push button; checkboxes, radios and group boxes are not."""
        kind = self.class_name(hwnd)
        if kind in BUTTON_CLASSES:
            return self.api.user.GetWindowLongW(hwnd, -16) & 15 in (0, 1)
        return kind in EDIT_CLASSES

    def controls(self):
        controls = []
        if self.supported(self.hwnd):
            controls.append(self.hwnd)

        @self.api.enum_callback
        def visit(hwnd, _):
            if self.api.user.IsWindowVisible(hwnd) and self.supported(hwnd):
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

    def child_at(self, x, y):
        hwnd = self.hwnd
        for _ in range(32):
            point = wintypes.POINT(round(x), round(y))
            if not self.api.user.ScreenToClient(hwnd, ctypes.byref(point)):
                raise ValueError('Control geometry is unavailable')
            child = self.api.user.ChildWindowFromPointEx(hwnd, point, 7)
            if not child or child == hwnd:
                break
            hwnd = child
        return hwnd

    def pick(self, x, y):
        """Select what lies under the agent pointer; nothing is sent to the application."""
        hwnd = self.child_at(x, y)
        self.control = self.element = None
        if self.supported(hwnd):
            self.control = hwnd
        else:
            from .windows_uia import Unavailable

            try:
                element = self.uia().from_point(x, y)
            except Unavailable as error:
                raise ValueError(
                    f'{error}. Without it only standard Edit controls and push buttons are '
                    'supported; no host fallback'
                ) from None
            if not element or element.CurrentProcessId != self.api.identity(self.hwnd)[1]:
                raise ValueError('The agent pointer is not over the target application')
            self.element = element
        self.position = (round(x), round(y))
        if self.pointer is None:
            from .windows_pointer import AgentPointer

            self.pointer = AgentPointer()
        self.pointer.move(round(x), round(y))
        return 'ui_automation' if self.element is not None else 'control_messages'

    def edit(self, *, editable=False):
        if self.control is None and self.element is None:
            edits = [h for h in self.controls() if self.class_name(h) in EDIT_CLASSES]
            if len(edits) != 1:
                raise ValueError('Choose a text field with an independent move/click before typing')
            self.control = edits[0]
        if self.element is not None:
            return None
        if self.class_name(self.control) not in EDIT_CLASSES:
            raise ValueError('Selected control does not support independent text entry')
        if editable and (
            not self.api.user.IsWindowEnabled(self.control)
            or self.api.user.GetWindowLongW(self.control, -16) & 0x800
        ):
            raise ValueError('The selected edit control is disabled or read-only')
        return self.control

    def write(self, text):
        """Insert the whole text in one step; returns how it was delivered."""
        if not isinstance(text, str) or not 1 <= len(text) <= 4096 or '\0' in text:
            raise ValueError('Independent text requires 1–4096 characters without NUL')
        hwnd = self.edit(editable=True)
        if hwnd is None:
            self.uia().append(self.element, text)
            return 'ui_automation_value_append'
        payload = ctypes.create_unicode_buffer(
            text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')
        )
        self.message(hwnd, 0x00C2, 1, ctypes.addressof(payload))  # EM_REPLACESEL, undoable.
        return 'control_messages'

    def button(self, code, pressed):
        """Left press activates the selected control; returns the action taken."""
        if code != 272 or type(pressed) is not bool or not (self.control or self.element):
            raise ValueError('Independent controls support a selected control and the left button')
        if self.pointer:
            self.pointer.press(pressed)
        if not pressed:
            return None
        if self.element is not None:
            return self.uia().activate(self.element)
        kind = self.class_name(self.control)
        if not self.api.user.IsWindowEnabled(self.control):
            raise ValueError('The selected control is disabled')
        if kind in BUTTON_CLASSES:
            if not self.supported(self.control):
                raise ValueError('Independent checkbox/radio state changes are not implemented')
            # Notify the parent directly; BM_CLICK/WM_LBUTTONDOWN may call SetFocus.
            identity = self.api.user.GetDlgCtrlID(self.control)
            self.message(self.api.user.GetParent(self.control), 0x0111, identity, self.control)
            return 'button_notification'
        if self.position is None:
            raise ValueError('Observe and move to a control before clicking')
        if kind != 'edit':
            # RichEdit EM_CHARFROMPOS needs a POINTL inside the target process, and a
            # pointer into this process must never be sent across. UI Automation's text
            # pattern can place the caret instead; otherwise the caret stays where it was.
            return self.richedit_caret()
        point = wintypes.POINT(*self.position)
        self.api.user.ScreenToClient(self.control, ctypes.byref(point))
        position = (
            self.message(self.control, 0x00D7, 0, (point.y & 65535) << 16 | (point.x & 65535))
            & 65535  # EM_CHARFROMPOS
        )
        self.message(self.control, 0x00B1, position, position)  # EM_SETSEL, no focus change.
        return 'caret_placed'

    def richedit_caret(self):
        from .windows_uia import Unavailable

        try:
            automation = self.uia()
            element = automation.from_handle(self.control)
            if automation.select_text_at(element, *self.position):
                return 'caret_placed'
        except (Unavailable, OSError, ValueError):
            pass
        return 'selected_caret_unchanged'

    def key(self, code, pressed, keymap):
        if type(code) is not int or type(pressed) is not bool:
            raise ValueError('Physical key code and pressed state are required')
        if code in MODIFIERS:
            (self.modifiers.add if pressed else self.modifiers.discard)(code)
            return None
        if not pressed:
            return None
        hwnd = self.edit(editable=True)
        if hwnd is None:
            return self.element_key(code, keymap)
        if self.modifiers & {29, 97}:
            if code == KEYS['a']:
                self.message(hwnd, 0x00B1, 0, -1)
                return 'select_all'
            if code == KEYS['z']:
                self.message(hwnd, 0x00C7)
                return 'undo'
            raise ValueError(
                'This independent control chord is unsupported; no shared clipboard or host fallback'
            )
        if self.modifiers & {56, 100}:
            raise ValueError('Independent Alt shortcuts are unsupported')
        if code in (14, 28, 15):
            self.message(hwnd, 0x0102, {14: 8, 28: 13, 15: 9}[code], 1)
            return 'character'
        if code in NAVIGATION:
            if self.modifiers:
                raise ValueError('Independent modified navigation is unsupported')
            self.message(hwnd, 0x0100, NAVIGATION[code], 1)
            self.message(hwnd, 0x0101, NAVIGATION[code], 0xC0000001)
            return 'navigation'
        return self.write(self.character(code, keymap))

    def element_key(self, code, keymap):
        """Keys on a UI Automation element: characters append, Backspace, Enter invokes."""
        if self.modifiers - {42, 54}:
            raise ValueError('Independent shortcuts on UI Automation elements are unsupported')
        if code == 14:
            self.uia().backspace(self.element)
            return 'backspace'
        if code == 28:
            return self.uia().activate(self.element)
        return self.write(self.character(code, keymap))

    def character(self, code, keymap):
        expected = ([42] if self.modifiers & {42, 54} else []) + [code]
        character = next(
            (c for c, codes in keymap['characters'].items() if codes == expected), None
        )
        if character is None:
            raise ValueError('Independent key has no supported character mapping')
        return character

    def scroll(self, delta, axis):
        if self.element is None and self.position is None:
            raise ValueError('Move the agent pointer over the content before scrolling')
        element = self.element or self.uia().from_point(*self.position)
        return self.uia().scroll(element, delta, axis)

    def close(self):
        if self.pointer:
            self.pointer.close()
        self.modifiers.clear()
