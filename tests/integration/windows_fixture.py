"""A disposable standard Win32 Edit/Button application for live Windows checks."""

import ctypes
import sys
from ctypes import wintypes

user, kernel = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
callback_type = ctypes.WINFUNCTYPE(
    wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WindowClass(ctypes.Structure):
    _fields_ = [
        ('style', wintypes.UINT),
        ('procedure', callback_type),
        ('class_extra', ctypes.c_int),
        ('window_extra', ctypes.c_int),
        ('instance', wintypes.HINSTANCE),
        ('icon', wintypes.HICON),
        ('cursor', wintypes.HANDLE),
        ('background', wintypes.HBRUSH),
        ('menu', wintypes.LPCWSTR),
        ('name', wintypes.LPCWSTR),
    ]


user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.DefWindowProcW.restype = wintypes.LPARAM
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
user.SetFocus.argtypes, user.SetFocus.restype = [wintypes.HWND], wintypes.HWND
user.DestroyWindow.argtypes = [wintypes.HWND]
user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = (
    [wintypes.LPCWSTR],
    wintypes.HMODULE,
)
user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
edit, clicks = None, 0
title = sys.argv[1]


@callback_type
def procedure(hwnd, message, wparam, lparam):
    global clicks
    if message == 0x0007 and edit:  # WM_SETFOCUS: host fixture receives real keys.
        user.SetFocus(edit)
    if message == 0x0111 and (wparam & 65535) == 1002:
        clicks += 1
        user.SetWindowTextW(hwnd, f'{title} clicked={clicks}')
        return 0
    if message == 0x0010:
        user.DestroyWindow(hwnd)
        return 0
    if message == 0x0002:
        user.PostQuitMessage(0)
        return 0
    return user.DefWindowProcW(hwnd, message, wparam, lparam)


instance = kernel.GetModuleHandleW(None)
window_class = WindowClass(
    procedure=procedure, instance=instance, background=6, name='ComputerArtistWindowsFixture'
)
if not user.RegisterClassW(ctypes.byref(window_class)):
    raise ctypes.WinError()
hwnd = user.CreateWindowExW(
    0,
    window_class.name,
    title,
    0x00CF0000,
    int(sys.argv[2]),
    int(sys.argv[3]),
    480,
    220,
    None,
    None,
    instance,
    None,
)
edit = user.CreateWindowExW(
    0x200, 'EDIT', '', 0x50010004, 20, 20, 420, 45, hwnd, 1001, instance, None
)
user.CreateWindowExW(
    0, 'BUTTON', 'Test independent click', 0x50010000, 20, 90, 220, 35, hwnd, 1002, instance, None
)
user.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE; starting a fixture must not steal focus.
message = wintypes.MSG()
while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
    user.TranslateMessage(ctypes.byref(message))
    user.DispatchMessageW(ctypes.byref(message))
