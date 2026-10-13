"""Capture a window's own rendering with PrintWindow instead of copying the screen.

The result excludes overlapping windows and the agent's drawn pointer. Some GPU or
protected content renders black; then the caller falls back to the screen region.
"""

import ctypes
from ctypes import wintypes

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ('size', wintypes.DWORD),
        ('width', wintypes.LONG),
        ('height', wintypes.LONG),
        ('planes', wintypes.WORD),
        ('bit_count', wintypes.WORD),
        ('compression', wintypes.DWORD),
        ('size_image', wintypes.DWORD),
        ('x_per_meter', wintypes.LONG),
        ('y_per_meter', wintypes.LONG),
        ('used', wintypes.DWORD),
        ('important', wintypes.DWORD),
    ]


def _libraries():
    user = ctypes.WinDLL('user32', use_last_error=True)
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.PrintWindow.restype = wintypes.BOOL
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi.GetDIBits.restype = ctypes.c_int
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    return user, gdi


def print_window(hwnd, width, height):
    """The client area as a PIL image, or None when the window could not render itself."""
    from PIL import Image

    if width <= 0 or height <= 0:
        return None
    user, gdi = _libraries()
    window_dc = user.GetDC(hwnd)
    if not window_dc:
        return None
    memory_dc = bitmap = previous = None
    try:
        memory_dc = gdi.CreateCompatibleDC(window_dc)
        bitmap = gdi.CreateCompatibleBitmap(window_dc, width, height)
        if not memory_dc or not bitmap:
            return None
        previous = gdi.SelectObject(memory_dc, bitmap)
        if not user.PrintWindow(hwnd, memory_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        header = BitmapInfoHeader(
            size=ctypes.sizeof(BitmapInfoHeader), width=width, height=-height, planes=1,
            bit_count=32, compression=0,
        )  # fmt: skip
        # BITMAPINFO is the header followed by an (unused for 32 bpp) color table.
        info = (ctypes.c_byte * (ctypes.sizeof(BitmapInfoHeader) + 16))()
        ctypes.memmove(info, ctypes.byref(header), ctypes.sizeof(header))
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi.SelectObject(memory_dc, previous)
        previous = None
        if gdi.GetDIBits(memory_dc, bitmap, 0, height, pixels, info, 0) != height:
            return None
        image = Image.frombuffer('RGB', (width, height), pixels.raw, 'raw', 'BGRX', 0, 1)
        if image.getextrema() == ((0, 0), (0, 0), (0, 0)):
            return None  # Not rendered (GPU surface or protected content).
        return image.copy()
    finally:
        if previous is not None:
            gdi.SelectObject(memory_dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory_dc:
            gdi.DeleteDC(memory_dc)
        user.ReleaseDC(hwnd, window_dc)
