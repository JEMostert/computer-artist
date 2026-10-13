"""Snapshot bounded memory-backed clipboard formats and restore only our changes.

Host paste uses this so the user's clipboard (text, images, files) comes back after
the agent pastes, unless the user copied something new in the meantime.
"""

import ctypes
from ctypes import wintypes


class NotPreservable(Exception):
    """The current clipboard cannot be copied safely; it must be left untouched."""


class ClipboardSnapshot:
    def __init__(self, api, owner=None):
        self.api = api
        self.user, self.kernel = api.user, api.kernel
        self.user.EnumClipboardFormats.argtypes = [wintypes.UINT]
        self.user.EnumClipboardFormats.restype = wintypes.UINT
        self.user.GetClipboardSequenceNumber.argtypes = []
        self.user.GetClipboardSequenceNumber.restype = wintypes.DWORD
        self.formats = []
        self.sequence = None
        self.owner = owner
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.user.CopyImage.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user.CopyImage.restype = wintypes.HANDLE
        self.gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi.CopyEnhMetaFileW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]
        self.gdi.CopyEnhMetaFileW.restype = wintypes.HANDLE
        self.gdi.DeleteEnhMetaFile.argtypes = [wintypes.HANDLE]

    def release_graphics(self, entries):
        for _kind, data, style in entries:
            if style == 'bitmap':
                self.gdi.DeleteObject(data)
            elif style == 'metafile':
                self.gdi.DeleteEnhMetaFile(data)

    def __enter__(self):
        if not self.user.OpenClipboard(self.owner):
            raise NotPreservable('Clipboard is currently in use')
        try:
            self.snapshot()
            # Read while open: nobody else can change the clipboard in between.
            self.sequence = self.user.GetClipboardSequenceNumber()
        except BaseException:
            self.release_graphics(self.formats)
            self.formats = []
            raise
        finally:
            self.user.CloseClipboard()
        return self

    def snapshot(self):
        kind, total = 0, 0
        while True:
            kind = self.user.EnumClipboardFormats(kind)
            if not kind:
                return
            # Handle-carrying formats (metafile pict, palette, owner/display GDI) are skipped.
            if kind in (3, 9, 0x80, 0x82, 0x83, 0x8E) or 0x300 <= kind <= 0x3FF:
                raise NotPreservable(
                    'Existing clipboard contains GDI/owner-rendered data; preserving it'
                )
            memory = self.user.GetClipboardData(kind)
            if not memory:
                raise NotPreservable('Existing clipboard format cannot be read; preserving it')
            if kind in (2, 14):
                duplicate = (
                    self.user.CopyImage(memory, 0, 0, 0, 0x2000)
                    if kind == 2
                    else self.gdi.CopyEnhMetaFileW(memory, None)
                )
                if not duplicate:
                    raise NotPreservable('Existing clipboard graphic cannot be duplicated')
                self.formats.append((kind, duplicate, 'bitmap' if kind == 2 else 'metafile'))
                continue
            size = self.kernel.GlobalSize(memory)
            total += size
            if not size or total > 256 * 1024 * 1024 or len(self.formats) >= 64:
                raise NotPreservable(
                    'Existing clipboard cannot be copied within the bounded snapshot'
                )
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                raise NotPreservable('Existing clipboard format cannot be snapshotted')
            try:
                self.formats.append((kind, ctypes.string_at(pointer, size), 'memory'))
            finally:
                self.kernel.GlobalUnlock(memory)

    def checkpoint(self):
        """Accept the clipboard state produced by our own write just before this call."""
        self.sequence = self.user.GetClipboardSequenceNumber()

    def __exit__(self, error_type, error, _traceback):
        try:
            self.restore()
        except Exception as problem:
            if error is None:
                raise
            # Keep the original failure visible; attach why restoration did not happen.
            error.add_note(f'Clipboard not restored: {problem}')
        return False

    def restore(self):
        pending = list(self.formats)
        try:
            if not self.user.OpenClipboard(self.owner):
                raise RuntimeError('Could not reopen the clipboard for restoration')
            try:
                # Compare while open, so no other copy can slip in before EmptyClipboard.
                if self.user.GetClipboardSequenceNumber() != self.sequence:
                    raise RuntimeError(
                        'Clipboard changed in the meantime; preserving the latest clipboard'
                    )
                if not self.user.EmptyClipboard():
                    raise ctypes.WinError(ctypes.get_last_error())
                while pending:
                    kind, data, style = pending[0]
                    if style != 'memory':
                        if not self.user.SetClipboardData(kind, data):
                            raise ctypes.WinError(ctypes.get_last_error())
                        pending.pop(0)  # The clipboard owns this graphic now.
                        continue
                    self.set_memory(kind, data)
                    pending.pop(0)
            finally:
                self.user.CloseClipboard()
        finally:
            self.release_graphics(pending)
            self.formats = []

    def set_memory(self, kind, data):
        memory = self.kernel.GlobalAlloc(2, len(data))
        if not memory:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.kernel.GlobalUnlock(memory)
            if not self.user.SetClipboardData(kind, memory):
                raise ctypes.WinError(ctypes.get_last_error())
            memory = None
        finally:
            if memory:
                self.kernel.GlobalFree(memory)
