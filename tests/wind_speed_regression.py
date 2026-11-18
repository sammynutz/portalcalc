"""Regional wind speeds and Mc against AS/NZS 1170.2:2021 Table 3.1(A) / Table 3.3."""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import unittest

from portalcalc.wind import (REGIONAL_WIND_SPEEDS, SERVICEABILITY_WIND_SPEEDS,
                             climate_change_multiplier)


class RegionalWindSpeedTests(unittest.TestCase):
    def test_region_b1_matches_spacegass_26_csb3287(self):
        # SpaceGass 14.50 wind output: Region B1, ARI 500 / 25, Mc 1.00.
        self.assertAlmostEqual(REGIONAL_WIND_SPEEDS[2]["B1"], 56.581, places=3)
        self.assertAlmostEqual(SERVICEABILITY_WIND_SPEEDS["B1"], 39.320, places=3)
        self.assertEqual(climate_change_multiplier("B1"), 1.0)

    def test_table_3_1_a_rounded_values(self):
        # Table 3.1(A) prints V_R rounded to whole m/s.
        expected = {
            # IL: R = 100 (200 cyclonic), 500, 1000, 2500
            "A0": (41, 45, 46, 48),
            "B1": (48, 57, 60, 64),
            "B2": (52, 57, 60, 64),
            "C": (61, 66, 70, 74),
            "D": (72, 80, 85, 91),
        }
        for region, speeds in expected.items():
            for level, speed in zip((1, 2, 3, 4), speeds):
                with self.subTest(region=region, level=level):
                    self.assertEqual(round(REGIONAL_WIND_SPEEDS[level][region]), speed)
        self.assertEqual(round(SERVICEABILITY_WIND_SPEEDS["A0"]), 37)

    def test_climate_change_multiplier_table_3_3(self):
        for region in ("A0", "A5", "A-ALL", "B1"):
            self.assertEqual(climate_change_multiplier(region), 1.0)
        for region in ("B2", "C", "D"):
            self.assertEqual(climate_change_multiplier(region), 1.05)


if __name__ == "__main__":
    unittest.main()
