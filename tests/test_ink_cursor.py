"""The ink pen cursor shared by the Windows and macOS overlays; no desktop needed."""

import unittest

from PIL import Image

from computer_artist import ink_cursor
from computer_artist.ink_cursor import InkCursor
from computer_artist.windows_pointer import premultiplied_bgra


def alpha_at(cursor, x, y, now):
    image, (left, top) = cursor.render(now)
    pixels = cursor.scale  # Image pixels per input unit.
    return image.getpixel((round((x - left) * pixels), round((y - top) * pixels)))[3]


class InkCursorTest(unittest.TestCase):
    def test_nib_tip_is_the_hotspot_at_every_scale(self):
        # macOS passes logical points with a Retina scale; Windows physical pixels with a size.
        for scale, size in ((1, 1), (2, 1), (1, 1.5)):
            with self.subTest(scale=scale, size=size):
                cursor = InkCursor(scale=scale, size=size, seed=1)
                cursor.move(300, 200, now=0)
                self.assertGreater(alpha_at(cursor, 300, 200, 0.05), 100)
                # The pen leans up and to the right of the tip, never below it.
                self.assertGreater(alpha_at(cursor, 300 + 14 * size, 200 - 18 * size, 0.05), 100)
                self.assertLess(alpha_at(cursor, 300 + 14 * size, 200 + 18 * size, 0.05), 40)

    def test_stroke_fades_and_a_held_button_keeps_the_brush_longer(self):
        cursor = InkCursor(seed=1)
        for i in range(10):
            cursor.move(100 + 10 * i, 100, now=i * 0.01)
        cursor.render(now=0.1 + ink_cursor.TRAIL_SECONDS)
        self.assertLessEqual(len(cursor.samples), 1)
        cursor.press(True, now=1)
        for i in range(10):
            cursor.move(200 + 10 * i, 100, now=1 + i * 0.01)
        cursor.render(now=1.1 + ink_cursor.TRAIL_SECONDS)
        self.assertGreater(len(cursor.samples), 5)
        cursor.render(now=1.2 + ink_cursor.BRUSH_SECONDS)
        self.assertLessEqual(len(cursor.samples), 1)

    def test_press_splashes_once_and_settles(self):
        cursor = InkCursor(seed=1)
        cursor.move(50, 50, now=0)
        cursor.press(True, now=1)
        cursor.press(True, now=1.01)  # A held button does not splash again.
        self.assertEqual(len(cursor.splashes), 1)
        self.assertEqual(len(cursor.drops), 6)
        self.assertTrue(cursor.busy(now=1.1))
        cursor.press(False, now=1.2)
        cursor.render(now=1 + max(ink_cursor.SPLASH_SECONDS, ink_cursor.DROP_SECONDS))
        self.assertEqual((cursor.splashes, cursor.drops), ([], []))
        self.assertFalse(cursor.busy(now=1 + ink_cursor.BRUSH_SECONDS + 0.1))

    def test_a_warp_never_sweeps_across_the_desktop(self):
        cursor = InkCursor(seed=1)
        cursor.move(10, 10, now=0)
        cursor.move(3000, 2000, now=0.01)
        image, _ = cursor.render(now=0.02)
        self.assertLessEqual(max(image.size), ink_cursor.MAX_EXTENT)
        self.assertIsNone(InkCursor().render(now=0))
        with self.assertRaises(ValueError):
            cursor.move(float('nan'), 1)

    def test_windows_pixels_are_premultiplied_bgra(self):
        pixel = Image.new('RGBA', (1, 1), (200, 100, 50, 128))
        self.assertEqual(premultiplied_bgra(pixel), bytes((25, 50, 100, 128)))
