"""Test-harness view of the package snapshot: an unpreservable clipboard skips the check."""

import unittest

from computer_artist import windows_clipboard


class ClipboardSnapshot(windows_clipboard.ClipboardSnapshot):
    def __enter__(self):
        try:
            return super().__enter__()
        except windows_clipboard.NotPreservable as reason:
            raise unittest.SkipTest(str(reason)) from None
