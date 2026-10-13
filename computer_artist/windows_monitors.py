"""Physical Windows monitor geometry and display-device identities."""

import ctypes
from ctypes import wintypes


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ('size', wintypes.DWORD),
        ('bounds', wintypes.RECT),
        ('work', wintypes.RECT),
        ('flags', wintypes.DWORD),
        ('device', wintypes.WCHAR * 32),
    ]


class DisplayDevice(ctypes.Structure):
    _fields_ = [
        ('size', wintypes.DWORD),
        ('name', wintypes.WCHAR * 32),
        ('description', wintypes.WCHAR * 128),
        ('flags', wintypes.DWORD),
        ('identity', wintypes.WCHAR * 128),
        ('key', wintypes.WCHAR * 128),
    ]


def monitors(api):
    user = api.user
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
    user.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        callback_type,
        wintypes.LPARAM,
    ]
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user.EnumDisplayDevicesW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(DisplayDevice),
        wintypes.DWORD,
    ]
    result = []

    @callback_type
    def visit(handle, _dc, _rect, _data):
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not user.GetMonitorInfoW(handle, ctypes.byref(info)):
            return True
        display = DisplayDevice(size=ctypes.sizeof(DisplayDevice))
        user.EnumDisplayDevicesW(info.device, 0, ctypes.byref(display), 1)
        result.append(
            {
                'device': info.device,
                'description': display.description,
                'identity': display.identity,
                'primary': bool(info.flags & 1),
                'bounds': [
                    info.bounds.left,
                    info.bounds.top,
                    info.bounds.right,
                    info.bounds.bottom,
                ],
                'work': [info.work.left, info.work.top, info.work.right, info.work.bottom],
            }
        )
        return True

    if not user.EnumDisplayMonitors(None, None, visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def select_monitor(api, selector):
    matches = [
        monitor
        for monitor in monitors(api)
        if selector.casefold() in (' '.join(str(value) for value in monitor.values())).casefold()
    ]
    if len(matches) != 1:
        raise ValueError(
            f'Monitor {selector!r} matched {len(matches)} displays; select an exact device or EDID ID'
        )
    return matches[0]


def contains(monitor, geometry):
    x, y, width, height = geometry
    left, top, right, bottom = monitor['bounds']
    return left <= x and top <= y and x + width <= right and y + height <= bottom
