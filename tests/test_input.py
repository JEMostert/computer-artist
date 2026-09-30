import unittest

from computer_artist.input import parse_chord


class InputTest(unittest.TestCase):
    def test_physical_shortcuts_are_case_insensitive_and_support_aliases(self):
        self.assertEqual(parse_chord('Ctrl+Shift+S'), [29, 42, 31])
        self.assertEqual(parse_chord('CONTROL+shift+s'), [29, 42, 31])
        self.assertEqual(parse_chord('Shift+Insert'), [42, 110])
        self.assertEqual(parse_chord('F12'), [88])

    def test_unknown_and_duplicate_physical_keys_fail_before_dispatch(self):
        for chord in ('', 'Ctrl+', 'Ctrl+Control', 'Unknown', 'S+s'):
            with self.subTest(chord=chord), self.assertRaises(ValueError):
                parse_chord(chord)
