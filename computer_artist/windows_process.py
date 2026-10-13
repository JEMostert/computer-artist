"""A Windows job owns the supervised worker and kills its descendants on close."""

import ctypes
from ctypes import wintypes


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ('ProcessTime', ctypes.c_int64),
        ('JobTime', ctypes.c_int64),
        ('Flags', wintypes.DWORD),
        ('MinWorkingSet', ctypes.c_size_t),
        ('MaxWorkingSet', ctypes.c_size_t),
        ('ActiveProcesses', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            'ReadOperations',
            'WriteOperations',
            'OtherOperations',
            'ReadBytes',
            'WriteBytes',
            'OtherBytes',
        )
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('Basic', BasicLimits),
        ('Io', IoCounters),
        ('ProcessMemory', ctypes.c_size_t),
        ('JobMemory', ctypes.c_size_t),
        ('PeakProcessMemory', ctypes.c_size_t),
        ('PeakJobMemory', ctypes.c_size_t),
    ]


class ProcessJob:
    def __init__(self, process):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
            if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
