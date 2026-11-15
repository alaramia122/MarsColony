import unittest

from backend.app.domain.hex_grid import (
    axial_to_pixel, hex_distance, hex_neighbors, legacy_percent_to_axial,
    normalize_footprint, pixel_to_axial,
)


class HexGridTests(unittest.TestCase):
    def test_axial_pixel_round_trip(self):
        for q in range(-8, 9):
            for r in range(-8, 9):
                self.assertEqual(pixel_to_axial(*axial_to_pixel(q, r)), (q, r))

    def test_distance_and_neighbors(self):
        self.assertEqual(hex_distance((0, 0), (2, -1)), 2)
        neighbors = hex_neighbors(0, 0)
        self.assertEqual(len(neighbors), 6)
        self.assertTrue(all(hex_distance((0, 0), cell) == 1 for cell in neighbors))

    def test_footprint_is_unique_and_stable(self):
        self.assertEqual(
            normalize_footprint([(1, 0), (0, 0), (1, 0)]),
            [[0, 0], [1, 0]],
        )

    def test_footprint_rejects_empty_and_oversized(self):
        with self.assertRaises(ValueError):
            normalize_footprint([])
        with self.assertRaises(ValueError):
            normalize_footprint((index, 0) for index in range(257))

    def test_legacy_coordinates_are_deterministic(self):
        self.assertEqual(legacy_percent_to_axial(50, 50), legacy_percent_to_axial(50, 50))
        with self.assertRaises(ValueError):
            legacy_percent_to_axial(101, 50)


if __name__ == "__main__":
    unittest.main()
