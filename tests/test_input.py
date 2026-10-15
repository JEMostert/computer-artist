import unittest

from computer_artist.input import parse_chord


class InputTest(unittest.TestCase):
    def test_unknown_and_duplicate_physical_keys_fail_before_dispatch(self):
        for chord in ('', 'Ctrl+', 'Ctrl+Control', 'Unknown', 'S+s'):
            with self.subTest(chord=chord), self.assertRaises(ValueError):
                parse_chord(chord)
