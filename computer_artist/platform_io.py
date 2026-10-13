"""Open files without following the final link, using native handles on Windows."""

import os


def open_no_follow(path, flags, mode=0o600):
    if os.name != 'nt':
        return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Attributes(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    access = 0x80000000
    if flags & (os.O_RDWR | os.O_WRONLY):
        access |= 0x40000000
    disposition = 1 if flags & os.O_EXCL else 4 if flags & os.O_CREAT else 3
    handle = kernel.CreateFileW(str(path), access, 7, None, disposition, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = Attributes()
        if not kernel.GetFileInformationByHandleEx(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10):
            raise ValueError('Links, reparse points and directories are refused')
        fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        handle = None
        return fd
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
