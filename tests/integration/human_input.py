"""Count physical keyboard/mouse events during live checks; key contents are never recorded."""

import ctypes
import threading
from ctypes import wintypes

hook_procedure = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KeyboardEvent(ctypes.Structure):
    _fields_ = [
        ('vk', wintypes.DWORD),
        ('scan', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('extra', ctypes.c_size_t),
    ]


class MouseEvent(ctypes.Structure):
    _fields_ = [
        ('point', wintypes.POINT),
        ('data', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('extra', ctypes.c_size_t),
    ]


class HumanInputMonitor:
    """Low-level hooks distinguish real devices from SendInput (LL*HF_INJECTED)."""

    def __init__(self):
        self.keyboard = self.mouse = 0
        self.thread_id = None
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.ready.wait(2)
        if self.error or not self.thread_id:
            raise RuntimeError(f'Could not observe human input: {self.error}')
        return self

    def __exit__(self, *_):
        user = ctypes.WinDLL('user32')
        user.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
        self.thread.join(timeout=2)

    def counts(self):
        return {'keyboard': self.keyboard, 'mouse': self.mouse}

    def since(self, before):
        now = self.counts()
        return {kind: now[kind] - before[kind] for kind in now}

    def run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32')
        user.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            hook_procedure,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user.CallNextHookEx.restype = wintypes.LPARAM
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]

        @hook_procedure
        def keyboard(code, wparam, lparam):
            if code >= 0 and not ctypes.cast(lparam, ctypes.POINTER(KeyboardEvent))[0].flags & 0x10:
                self.keyboard += 1
            return user.CallNextHookEx(None, code, wparam, lparam)

        @hook_procedure
        def mouse(code, wparam, lparam):
            if code >= 0 and not ctypes.cast(lparam, ctypes.POINTER(MouseEvent))[0].flags & 0x1:
                self.mouse += 1
            return user.CallNextHookEx(None, code, wparam, lparam)

        hooks = [
            user.SetWindowsHookExW(13, keyboard, None, 0),
            user.SetWindowsHookExW(14, mouse, None, 0),
        ]
        try:
            if not all(hooks):
                self.error = ctypes.WinError(ctypes.get_last_error())
                return
            message = wintypes.MSG()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Create the thread queue.
            self.thread_id = kernel.GetCurrentThreadId()
            self.ready.set()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            for hook in hooks:
                if hook:
                    user.UnhookWindowsHookEx(hook)
            self.ready.set()
