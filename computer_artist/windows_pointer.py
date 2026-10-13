"""A small animated, click-through agent pointer that never activates a window."""

import ctypes
import threading
from ctypes import wintypes


class AgentPointer:
    def __init__(self):
        self.position, self.hwnd, self.error = (0, 0), None, None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='ca-agent-pointer', daemon=True)
        self.thread.start()
        if not self.ready.wait(2):
            self.stopped.set()
            raise TimeoutError('Independent pointer creation timed out')
        if self.error:
            raise self.error

    def _run(self):
        user, gdi = (
            ctypes.WinDLL('user32', use_last_error=True),
            ctypes.WinDLL('gdi32', use_last_error=True),
        )
        bitmap = None
        try:
            user.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                ctypes.c_void_p,
            ]
            user.CreateWindowExW.restype = wintypes.HWND
            user.SetLayeredWindowAttributes.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                wintypes.BYTE,
                wintypes.DWORD,
            ]
            user.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], wintypes.HDC
            user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            user.SendMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user.SendMessageW.restype = wintypes.LPARAM
            user.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user.DestroyWindow.argtypes = [wintypes.HWND]
            for name, args, result in (
                ('CreateCompatibleDC', [wintypes.HDC], wintypes.HDC),
                (
                    'CreateCompatibleBitmap',
                    [wintypes.HDC, ctypes.c_int, ctypes.c_int],
                    wintypes.HBITMAP,
                ),
                ('CreateSolidBrush', [wintypes.DWORD], wintypes.HBRUSH),
                ('SelectObject', [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
                (
                    'PatBlt',
                    [
                        wintypes.HDC,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        wintypes.DWORD,
                    ],
                    wintypes.BOOL,
                ),
                (
                    'Ellipse',
                    [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
                    wintypes.BOOL,
                ),
                ('DeleteObject', [wintypes.HANDLE], wintypes.BOOL),
                ('DeleteDC', [wintypes.HDC], wintypes.BOOL),
            ):
                function = getattr(gdi, name)
                function.argtypes, function.restype = args, result
            # WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW.
            self.hwnd = user.CreateWindowExW(
                0x080800A0, 'STATIC', '', 0x8000000E, 0, 0, 20, 20, None, None, None, None
            )
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            user.SetLayeredWindowAttributes(self.hwnd, 0xFF00FF, 255, 1)
            self.ready.set()
            previous, frame = None, 0
            message = wintypes.MSG()
            while not self.stopped.wait(0.12):
                while user.PeekMessageW(ctypes.byref(message), self.hwnd, 0, 0, 1):
                    user.TranslateMessage(ctypes.byref(message))
                    user.DispatchMessageW(ctypes.byref(message))
                dc = user.GetDC(self.hwnd)
                memory = gdi.CreateCompatibleDC(dc)
                next_bitmap = gdi.CreateCompatibleBitmap(dc, 20, 20)
                selected = gdi.SelectObject(memory, next_bitmap)
                background, color = gdi.CreateSolidBrush(0xFF00FF), gdi.CreateSolidBrush(0xC7BD10)
                old_brush = gdi.SelectObject(memory, background)
                gdi.PatBlt(memory, 0, 0, 20, 20, 0x00F00021)
                gdi.SelectObject(memory, color)
                margin = 2 + frame % 3
                gdi.Ellipse(memory, margin, margin, 20 - margin, 20 - margin)
                gdi.SelectObject(memory, old_brush)
                gdi.SelectObject(memory, selected)
                gdi.DeleteObject(background)
                gdi.DeleteObject(color)
                gdi.DeleteDC(memory)
                user.ReleaseDC(self.hwnd, dc)
                user.SendMessageW(self.hwnd, 0x0172, 0, next_bitmap)
                if bitmap:
                    gdi.DeleteObject(bitmap)
                bitmap, frame = next_bitmap, frame + 1
                if self.position != previous:
                    x, y = self.position
                    user.SetWindowPos(
                        self.hwnd, ctypes.c_void_p(-1), x - 10, y - 10, 20, 20, 0x0050
                    )
                    previous = self.position
        except BaseException as error:
            self.error = error
        finally:
            self.ready.set()
            if self.hwnd:
                user.DestroyWindow(self.hwnd)
            if bitmap:
                gdi.DeleteObject(bitmap)

    def move(self, x, y):
        if self.error:
            raise self.error
        self.position = (x, y)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
