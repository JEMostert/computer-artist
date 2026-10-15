"""Run portable behavior plus Windows native tests; no live desktop input."""

import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
suite = unittest.TestSuite()
for filename in (
    'test_windows.py',
    'test_runtime.py',
    'test_programs.py',
    'test_journal.py',
    'test_gestures.py',
    'test_agent_keyboard.py',
    'test_box.py',
    'test_macos.py',
    'test_macos_activity.py',
    'test_containers.py',
    'test_ink_cursor.py',
):
    suite.addTests(unittest.defaultTestLoader.discover(str(root / 'tests'), pattern=filename))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
