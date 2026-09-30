import math
import unittest

from computer_artist.gestures import svg_path


class GeometryTest(unittest.TestCase):
    def test_relative_repeated_commands_close_and_pen_up(self):
        strokes = svg_path('m10,10 10,0 v10 h-10 z M50 50 l10 0', spacing=3)
        self.assertEqual(len(strokes), 2)
        self.assertEqual(strokes[0][0], (10, 10))
        self.assertEqual(strokes[0][-1], (10, 10))
        self.assertIn((20, 20), strokes[0])
        self.assertEqual(strokes[1][0], (50, 50))
        self.assertEqual(strokes[1][-1], (60, 50))

    def test_cubic_has_analytic_midpoint_and_bounded_spatial_steps(self):
        stroke = svg_path('M0 0 C0 100 100 100 100 0', spacing=2)[0]
        self.assertIn((50, 75), stroke)
        self.assertTrue(all(math.dist(a, b) <= 2.000001 for a, b in zip(stroke, stroke[1:])))
        self.assertEqual(stroke[-1], (100, 0))

    def test_smooth_cubic_and_quadratic_reflect_previous_control(self):
        explicit = svg_path('M0 0 C0 50 50 50 50 0 C50 -50 100 -50 100 0')
        self.assertEqual(explicit, svg_path('M0 0 C0 50 50 50 50 0 S100 -50 100 0'))
        explicit = svg_path('M0 0 Q50 100 100 0 Q150 -100 200 0')
        self.assertEqual(explicit, svg_path('M0 0 Q50 100 100 0 T200 0'))

    def test_smooth_control_resets_after_a_line(self):
        self.assertEqual(
            svg_path('M0 0 Q10 20 20 0 L30 0 T40 0'), svg_path('M0 0 Q10 20 20 0 L30 0 Q30 0 40 0')
        )

    def test_circular_arcs_match_analytic_radius_and_sweep(self):
        lower = svg_path('M10 0 A10 10 0 0 1 -10 0', spacing=0.5)[0]
        upper = svg_path('M10 0 A10 10 0 0 0 -10 0', spacing=0.5)[0]
        for point in lower + upper:
            self.assertAlmostEqual(math.hypot(*point), 10, places=7)
        self.assertGreater(max(p[1] for p in lower), 9.9)
        self.assertLess(min(p[1] for p in upper), -9.9)

    def test_arc_radii_correction_zero_radius_and_packed_flags(self):
        stroke = svg_path('M0 0 A1 1 0 0 1 10 0')[0]
        self.assertEqual(stroke[-1], (10, 0))
        self.assertAlmostEqual(max(math.dist(p, (5, 0)) for p in stroke), 5)
        self.assertEqual(svg_path('M0 0 A0 1 0 0 1 10 0'), svg_path('M0 0 L10 0'))
        self.assertEqual(svg_path('M0 0 A10 10 0 0110 0'), svg_path('M0 0 A10 10 0 0 1 10 0'))

    def test_rotation_large_arc_and_transform(self):
        short = svg_path('M0 0 A20 10 45 0 1 20 10')[0]
        long = svg_path('M0 0 A20 10 45 1 1 20 10')[0]
        self.assertGreater(len(long), len(short))
        transformed = svg_path('M0 0 L10 0', origin=(30, 40), scale=2, spacing=3)[0]
        self.assertEqual(transformed[0], (30, 40))
        self.assertEqual(transformed[-1], (50, 40))
        self.assertTrue(
            all(math.dist(a, b) <= 3.000001 for a, b in zip(transformed, transformed[1:]))
        )

    def test_loops_are_not_collapsed_when_endpoints_coincide(self):
        stroke = svg_path('M0 0 C100 100 -100 100 0 0')[0]
        self.assertEqual(stroke[0], stroke[-1])
        self.assertGreater(max(p[1] for p in stroke), 70)

    def test_invalid_or_excessive_paths_are_rejected(self):
        for data in (
            '',
            'L10 10',
            'M10',
            'M0 0 X10 10',
            'M0 0 C10 10',
            'M0 0 A10 10 0 2 1 20 20',
            'M0 0 L1e309 10',
            'M0 0',
            'M0 0 L10000000 10000000',
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                svg_path(data)
        for options in (
            {'spacing': 0},
            {'scale': float('nan')},
            {'scale': -1},
            {'origin': (0, float('inf'))},
            {'spacing': 1e-308},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                svg_path('M0 0 L10 10', **options)

    def test_duplicate_rounded_samples_still_have_bounded_work(self):
        with self.assertRaisesRegex(ValueError, 'sampling work limit'):
            svg_path('M0 0 C1 0 1 0 1 0', origin=(1000000, 1000000), scale=1e-12, spacing=1e-18)
