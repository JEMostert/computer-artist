"""The agent's ink-pen pointer on Windows: click-through and never activating a window."""

import ctypes
import threading
import time
from ctypes import wintypes


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class BlendFunction(ctypes.Structure):
    _fields_ = [
        ('BlendOp', ctypes.c_ubyte),
        ('BlendFlags', ctypes.c_ubyte),
        ('SourceConstantAlpha', ctypes.c_ubyte),
        ('AlphaFormat', ctypes.c_ubyte),
    ]


def premultiplied_bgra(image):
    """Pixel bytes for UpdateLayeredWindow: premultiplied BGRA, top row first."""
    from PIL import Image, ImageChops

    red, green, blue, alpha = image.split()
    red, green, blue = (ImageChops.multiply(channel, alpha) for channel in (red, green, blue))
    return Image.merge('RGBA', (blue, green, red, alpha)).tobytes()


class AgentPointer:
    def __init__(self):
        self.events, self.lock = [], threading.Lock()
        self.hwnd, self.error, self.position = None, None, None
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
        from .ink_cursor import InkCursor

        user = ctypes.WinDLL('user32', use_last_error=True)
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        bitmap = old = memory = screen = None
        try:
            user.CreateWindowExW.argtypes = [
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
            ]  # fmt: skip
            user.CreateWindowExW.restype = wintypes.HWND
            user.UpdateLayeredWindow.argtypes = [
                wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                wintypes.COLORREF, ctypes.POINTER(BlendFunction), wintypes.DWORD,
            ]  # fmt: skip
            user.UpdateLayeredWindow.restype = wintypes.BOOL
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], wintypes.HDC
            user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            user.GetDpiForWindow.argtypes, user.GetDpiForWindow.restype = (
                [wintypes.HWND],
                wintypes.UINT,
            )
            user.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                wintypes.UINT,
            ]  # fmt: skip
            user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user.DestroyWindow.argtypes = [wintypes.HWND]
            gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = (
                [wintypes.HDC],
                wintypes.HDC,
            )
            gdi.CreateDIBSection.argtypes = [
                wintypes.HDC, ctypes.POINTER(BitmapInfoHeader), wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
            ]  # fmt: skip
            gdi.CreateDIBSection.restype = wintypes.HBITMAP
            gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
            gdi.SelectObject.restype = wintypes.HANDLE
            gdi.DeleteObject.argtypes = [wintypes.HANDLE]
            gdi.DeleteDC.argtypes = [wintypes.HDC]
            # WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | TOPMOST.
            self.hwnd = user.CreateWindowExW(
                0x080800A8, 'STATIC', '', 0x80000000, 0, 0, 1, 1, None, None, None, None
            )
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            # Backend coordinates are physical pixels: one image pixel per unit, pen sized by DPI.
            dpi = user.GetDpiForWindow(self.hwnd) or 96
            cursor = InkCursor(scale=1, size=min(4, max(1, dpi / 96)))
            screen = user.GetDC(None)
            memory = gdi.CreateCompatibleDC(screen)
            self.ready.set()
            shown, size, pixels, next_frame = False, None, ctypes.c_void_p(), 0.0
            blend = BlendFunction(0, 0, 255, 1)  # AC_SRC_OVER with per-pixel AC_SRC_ALPHA.
            message = wintypes.MSG()
            while not self.stopped.wait(0.008):
                while user.PeekMessageW(ctypes.byref(message), self.hwnd, 0, 0, 1):
                    user.TranslateMessage(ctypes.byref(message))
                    user.DispatchMessageW(ctypes.byref(message))
                with self.lock:
                    events, self.events = self.events, []
                for kind, value in events:
                    cursor.move(*value) if kind == 'move' else cursor.press(value)
                now = time.monotonic()
                if now < next_frame and not events:
                    continue
                next_frame = now + (0.016 if cursor.busy(now) else 0.05)
                frame = cursor.render(now)
                if frame is None:
                    continue
                image, (left, top) = frame
                if image.size != size:
                    if bitmap:
                        gdi.SelectObject(memory, old)
                        gdi.DeleteObject(bitmap)
                    header = BitmapInfoHeader(
                        ctypes.sizeof(BitmapInfoHeader), image.width, -image.height, 1, 32, 0
                    )
                    bitmap = gdi.CreateDIBSection(
                        memory, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0
                    )
                    if not bitmap:
                        raise ctypes.WinError(ctypes.get_last_error())
                    old, size = gdi.SelectObject(memory, bitmap), image.size
                data = premultiplied_bgra(image)
                ctypes.memmove(pixels, data, len(data))
                origin = wintypes.POINT(round(left), round(top))
                extent, source = wintypes.SIZE(*size), wintypes.POINT(0, 0)
                user.UpdateLayeredWindow(
                    self.hwnd, screen, ctypes.byref(origin), ctypes.byref(extent), memory,
                    ctypes.byref(source), 0, ctypes.byref(blend), 2,
                )  # fmt: skip
                if not shown:
                    user.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
                    shown = True
        except BaseException as error:
            self.error = error
        finally:
            self.ready.set()
            if bitmap:
                gdi.SelectObject(memory, old)
                gdi.DeleteObject(bitmap)
            if memory:
                gdi.DeleteDC(memory)
            if screen:
                user.ReleaseDC(None, screen)
            if self.hwnd:
                user.DestroyWindow(self.hwnd)

    def _post(self, kind, value):
        if self.error:
            raise self.error
        with self.lock:
            self.events.append((kind, value))

    def move(self, x, y):
        self._post('move', (x, y))
        self.position = (x, y)

    def glide_to(self, x, y):
        """Let the pen travel visibly to a new action's position; returns when it arrives."""
        from .ink_cursor import glide

        points, delay = glide(self.position, (x, y))
        for point in points:
            self.move(*point)
            time.sleep(delay)
        self.move(x, y)

    def press(self, pressed):
        self._post('press', bool(pressed))

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
