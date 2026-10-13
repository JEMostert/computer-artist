"""Explicit native window placement, preserving the human foreground and pointer."""

import ctypes
from ctypes import wintypes

from .config import socket_path
from .windows import WindowsBackend
from .windows_monitors import monitors
from .workspace import Workspace


def dispatch(args):
    if socket_path(getattr(args, 'socket', None)) != 'win32':
        raise ValueError('Monitor/window placement commands require the native win32 backend')
    backend = WindowsBackend('agent')
    try:
        if args.command == 'monitors':
            return {'ok': True, 'monitors': monitors(backend.api)}
        identity = Workspace(args.window_dir, args.output_dir).resolve_window(args.window)
        hwnd, window = backend.resolve(identity)
        backend.api.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        backend.api.user.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        rect = wintypes.RECT()
        if not backend.api.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        previous = [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]
        if args.action == 'info':
            return {'ok': True, 'window': window, 'outer_geometry': previous}
        if args.x is None or args.y is None:
            raise ValueError('Window move requires --x and --y')
        width = args.width if args.width is not None else previous[2]
        height = args.height if args.height is not None else previous[3]
        if width <= 0 or height <= 0:
            raise ValueError('Window size must be positive')
        backend.monitor_guard((args.x, args.y, width, height))
        foreground = backend.api.user.GetForegroundWindow()
        if not backend.api.user.SetWindowPos(hwnd, None, args.x, args.y, width, height, 0x0014):
            raise ctypes.WinError(ctypes.get_last_error())
        backend.api.user.GetWindowRect(hwnd, ctypes.byref(rect))
        actual = [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]
        if actual != [args.x, args.y, width, height]:
            raise ValueError(f'The application did not accept the requested geometry: {actual}')
        return {
            'ok': True,
            'window': identity,
            'previous_geometry': previous,
            'geometry': actual,
            'foreground_preserved': backend.api.user.GetForegroundWindow() == foreground,
        }
    finally:
        backend.close()
