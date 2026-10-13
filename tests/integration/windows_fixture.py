"""A disposable standard Win32 Edit/Button application for live Windows checks."""

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

user, kernel = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
gdi = ctypes.WinDLL('gdi32')
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
user.SetCapture.argtypes, user.SetCapture.restype = [wintypes.HWND], wintypes.HWND
user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], wintypes.HDC
user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
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
event_file = Path(sys.argv[4]) if len(sys.argv) > 4 else None
events = []
last_point = None
allowed_bounds = (
    json.loads(os.environ['CA_TEST_MONITOR_BOUNDS'])
    if os.environ.get('CA_TEST_MONITOR_BOUNDS')
    else None
)
creation = [int(sys.argv[2]), int(sys.argv[3]), 480, 480]
user.IsIconic.argtypes = [wintypes.HWND]


class WindowPosition(ctypes.Structure):
    _fields_ = [
        ('hwnd', wintypes.HWND),
        ('insert_after', wintypes.HWND),
        ('x', ctypes.c_int),
        ('y', ctypes.c_int),
        ('width', ctypes.c_int),
        ('height', ctypes.c_int),
        ('flags', wintypes.UINT),
    ]


def record(kind, **values):
    """Append one sequenced event; never raise inside a window procedure."""
    if event_file is None:
        return
    events.append({'seq': len(events), 'kind': kind, **values})
    try:
        with event_file.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(events[-1]) + '\n')
    except OSError:
        pass  # The harness treats a missing event as a failed expectation.


def inside(rect):
    left, top, right, bottom = allowed_bounds
    x, y, width, height = rect
    return left <= x and top <= y and x + width <= right and y + height <= bottom


@callback_type
def canvas_procedure(hwnd, message, wparam, lparam):
    global last_point
    point = (ctypes.c_short(lparam & 65535).value, ctypes.c_short((lparam >> 16) & 65535).value)
    kinds = {
        0x201: 'left_down',
        0x202: 'left_up',
        0x203: 'left_double',
        0x204: 'right_down',
        0x205: 'right_up',
        0x207: 'middle_down',
        0x208: 'middle_up',
    }
    if message in kinds:
        record(kinds[message], point=list(point))
        if message == 0x201:
            user.SetFocus(hwnd)
            user.SetCapture(hwnd)
            last_point = point
        elif message == 0x202:
            user.ReleaseCapture()
            last_point = None
        return 0
    if message == 0x200:
        record('move', point=list(point), dragging=bool(wparam & 1))
        if wparam & 1 and last_point:
            dc = user.GetDC(hwnd)
            gdi.MoveToEx(dc, *last_point, None)
            gdi.LineTo(dc, *point)
            user.ReleaseDC(hwnd, dc)
            last_point = point
        return 0
    if message in (0x20A, 0x20E):
        record(
            'vertical_wheel' if message == 0x20A else 'horizontal_wheel',
            amount=ctypes.c_short((wparam >> 16) & 65535).value,
        )
        return 0
    return user.DefWindowProcW(hwnd, message, wparam, lparam)


@callback_type
def procedure(hwnd, message, wparam, lparam):
    global clicks
    # WM_WINDOWPOSCHANGING: refuse moves of this fixture off the selected display.
    if message == 0x0046 and allowed_bounds and not user.IsIconic(hwnd):
        position = ctypes.cast(lparam, ctypes.POINTER(WindowPosition)).contents
        previous = wintypes.RECT()
        user.GetWindowRect(hwnd, ctypes.byref(previous))
        current = [
            previous.left,
            previous.top,
            previous.right - previous.left,
            previous.bottom - previous.top,
        ]
        requested = [
            current[0] if position.flags & 2 else position.x,  # SWP_NOMOVE
            current[1] if position.flags & 2 else position.y,
            current[2] if position.flags & 1 else position.width,  # SWP_NOSIZE
            current[3] if position.flags & 1 else position.height,
        ]
        if requested[0] != -32000 and not inside(requested):
            # Keep our own in-bounds rect instead of clamping, so fixtures never stack.
            kept = current if inside(current) else creation
            position.x, position.y, position.width, position.height = kept
            position.flags &= ~3
            record('fixture_boundary_guard', requested=requested, kept=list(kept))
    if message == 0x0007 and edit:  # WM_SETFOCUS: host fixture receives real keys.
        user.SetFocus(edit)
    if message == 0x0111 and (wparam & 65535) == 1002:
        clicks += 1
        user.SetWindowTextW(hwnd, f'{title} clicked={clicks}')
        return 0
    if message in (0x20A, 0x20E):
        record(
            'vertical_wheel' if message == 0x20A else 'horizontal_wheel',
            amount=ctypes.c_short((wparam >> 16) & 65535).value,
        )
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
    *creation,
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
canvas_class = WindowClass(
    style=8,
    procedure=canvas_procedure,
    instance=instance,
    background=6,
    name='ComputerArtistTestCanvas',
)
if not user.RegisterClassW(ctypes.byref(canvas_class)):
    raise ctypes.WinError()
user.CreateWindowExW(
    0x200, canvas_class.name, '', 0x50010000, 20, 150, 420, 240, hwnd, 1003, instance, None
)
if os.environ.get('CA_TEST_PLACEMENT_ONLY'):
    placement = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(placement))
    print(
        json.dumps(
            {
                'hwnd': int(hwnd),
                'geometry': [
                    placement.left,
                    placement.top,
                    placement.right - placement.left,
                    placement.bottom - placement.top,
                ],
            }
        ),
        flush=True,
    )
else:
    user.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE; starting a fixture must not steal focus.
    shown = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(shown))
    # Proves the event log works, so an empty input slice is meaningful evidence.
    record(
        'fixture_ready',
        geometry=[shown.left, shown.top, shown.right - shown.left, shown.bottom - shown.top],
    )
message = wintypes.MSG()
while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
    user.TranslateMessage(ctypes.byref(message))
    user.DispatchMessageW(ctypes.byref(message))
