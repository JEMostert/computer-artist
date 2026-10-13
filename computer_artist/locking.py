"""Advisory file leases on POSIX and Windows, including shared read-only leases."""

import os

if os.name != 'nt':
    from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock  # noqa: F401
else:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ('Internal', ctypes.c_size_t),
            ('InternalHigh', ctypes.c_size_t),
            ('Offset', wintypes.DWORD),
            ('OffsetHigh', wintypes.DWORD),
            ('hEvent', wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    kernel.UnlockFileEx.restype = wintypes.BOOL

    def flock(file, operation):
        fd = file if isinstance(file, int) else file.fileno()
        handle = msvcrt.get_osfhandle(fd)
        overlap = Overlapped()
        if operation & LOCK_UN:
            passed = kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
        else:
            flags = (2 if operation & LOCK_EX else 0) | (1 if operation & LOCK_NB else 0)
            passed = kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlap))
        if not passed:
            error = ctypes.get_last_error()
            if error in (33, 158):
                raise BlockingIOError(error, 'File lease is already held')
            raise ctypes.WinError(error)
