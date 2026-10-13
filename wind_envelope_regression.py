"""Verify the all-A and orthogonal wind envelopes, including rotated sectors."""
import unittest
from dataclasses import replace
from types import SimpleNamespace

from wind import (A_ENVELOPE_REGION, A_SUBREGIONS, DIRECTION_ORDER,
                  DIRECTION_MULTIPLIERS, WindInputs, calculate_wind,
                  design_direction_multiplier, interpolated_direction_multiplier,
                  symmetric_end_source_direction)


class WindEnvelopeTests(unittest.TestCase):
    def test_a_region_table_matches_standard_table_3_2_a(self):
        expected = {
            "N": (.90, .90, .85, .90, .85, .95),
            "NE": (.85, .85, .75, .75, .75, .80),
            "E": (.85, .85, .85, .75, .75, .80),
            "SE": (.90, .80, .95, .90, .80, .80),
            "S": (.90, .80, .95, .90, .80, .80),
            "SW": (.95, .95, .95, .95, .90, .95),
            "W": (1., 1., 1., 1., 1., 1.),
            "NW": (.95, .95, .95, .95, 1., .95),
        }
        for direction, values in expected.items():
            self.assertEqual(tuple(DIRECTION_MULTIPLIERS[direction][r] for r in A_SUBREGIONS), values)

    def test_all_a_dominates_each_actual_subregion(self):
        for level in range(1, 5):
            for direction in [*DIRECTION_ORDER, "Any"]:
                inputs = WindInputs(wind_region=A_ENVELOPE_REGION,
                                    importance_level=level, orientation=direction)
                envelope = calculate_wind(inputs)
                regions = [calculate_wind(replace(inputs, wind_region=r)) for r in A_SUBREGIONS]
                self.assertAlmostEqual(envelope.vdes_b_m_s, max(r.vdes_b_m_s for r in regions))
                self.assertAlmostEqual(envelope.vdes_sls_m_s, max(r.vdes_sls_m_s for r in regions))

    def test_compass_windows_match_existing_three_point_rule(self):
        for region in (*A_SUBREGIONS, A_ENVELOPE_REGION):
            for i, direction in enumerate(DIRECTION_ORDER):
                expected = max(DIRECTION_MULTIPLIERS[DIRECTION_ORDER[(i + j) % 8]][region]
                               for j in (-1, 0, 1))
                self.assertAlmostEqual(design_direction_multiplier(region, direction), expected)

    def test_rotated_sector_checks_interpolated_boundaries_and_interior(self):
        # ENE's ±45° sector includes NE and E plus interpolated boundaries.
        self.assertAlmostEqual(design_direction_multiplier(A_ENVELOPE_REGION, 63.0), 0.89)
        self.assertAlmostEqual(design_direction_multiplier(A_ENVELOPE_REGION, "E"), 0.95)
        # NW is inside a wrapped north-facing sector and must not be omitted.
        self.assertAlmostEqual(design_direction_multiplier("A4", 350.0), 1.0)
        self.assertAlmostEqual(design_direction_multiplier("A4", -10.0), 1.0)

    def test_regional_envelope_is_applied_after_interpolation(self):
        for bearing in (0, 22.5, 63, 112.5, 247.5, 359):
            expected = max(interpolated_direction_multiplier(r, bearing) for r in A_SUBREGIONS)
            self.assertAlmostEqual(interpolated_direction_multiplier(A_ENVELOPE_REGION, bearing), expected)
            expected_sector = max(design_direction_multiplier(r, bearing) for r in A_SUBREGIONS)
            self.assertAlmostEqual(design_direction_multiplier(A_ENVELOPE_REGION, bearing), expected_sector)
        # Interpolating cardinal maxima would give .90 here; the regional max is .875.
        self.assertAlmostEqual(interpolated_direction_multiplier(A_ENVELOPE_REGION, 22.5), 0.875)

    def test_closed_end_mapping_uses_larger_of_opposite_windows(self):
        from main import MainWindow
        for region in (*A_SUBREGIONS, A_ENVELOPE_REGION):
            for north in DIRECTION_ORDER:
                stub = SimpleNamespace(
                    envelope_plan_selector=SimpleNamespace(wall_states=lambda: {"front": True, "back": True}),
                    wind_region_combo=SimpleNamespace(currentText=lambda: region))
                actual = MainWindow.wind_source_direction_for_case(stub, north, "End +")
                screen_north_source = DIRECTION_ORDER[(-DIRECTION_ORDER.index(north)) % 8]
                self.assertEqual(actual, symmetric_end_source_direction(region, screen_north_source))

    def test_exact_rotation_minima(self):
        from hay_shed_orientation import exact_minimum
        self.assertEqual(exact_minimum(offsets=(0,)), {"md": .89, "bearings_deg": [63.]})
        self.assertEqual(exact_minimum(offsets=(90, 270)),
                         {"md": .9625, "bearings_deg": [101.25, 281.25]})
        self.assertEqual(exact_minimum(offsets=(0, 180)),
                         {"md": .9625, "bearings_deg": [11.25, 191.25]})

    def test_linear_sector_representation_matches_wind_windows(self):
        from hay_shed_orientation import sector_lines
        for start in range(0, 360, 45):
            for offset in (0, 90, 180, 270):
                for t in (0, .19, .5, .81, 1):
                    represented = max(a + slope * t for a, slope in sector_lines(A_SUBREGIONS, start, offset))
                    direct = max(design_direction_multiplier(r, start + 45 * t + offset)
                                 for r in A_SUBREGIONS)
                    self.assertAlmostEqual(represented, direct, places=12)

    def test_rotated_candidate_checks_every_actual_region_and_all_axes(self):
        from hay_shed_orientation import directional_factors
        rotated = directional_factors(63)
        baseline = directional_factors(90)
        self.assertEqual(rotated, {"open": .89, "closed": 1., "end_1": .95,
                                   "end_2": 1., "governing_end": 1.})
        self.assertLess(rotated["open"], baseline["open"])
        for name in ("closed", "end_1", "end_2"):
            self.assertEqual(rotated[name], baseline[name])
        for region in A_SUBREGIONS:
            for name, offset in (("open", 0), ("closed", 180), ("end_1", 90), ("end_2", -90)):
                self.assertLessEqual(design_direction_multiplier(region, 63 + offset), rotated[name] + 1e-12)

    def test_public_wind_calculation_accepts_degrees_and_envelopes_regions(self):
        for bearing in (63., "63°", 101.25, "101.25°", 333.):
            inputs = WindInputs(wind_region=A_ENVELOPE_REGION, orientation=bearing)
            envelope = calculate_wind(inputs)
            actuals = [calculate_wind(replace(inputs, wind_region=r)) for r in A_SUBREGIONS]
            self.assertAlmostEqual(envelope.vdes_b_m_s, max(a.vdes_b_m_s for a in actuals))
            self.assertAlmostEqual(envelope.vdes_sls_m_s, max(a.vdes_sls_m_s for a in actuals))
        for bad in (True, "nan°", float("inf"), "unknown"):
            with self.assertRaises(ValueError):
                calculate_wind(WindInputs(orientation=bad))

    def test_custom_bearing_maps_to_physical_sides_and_worse_end(self):
        from main import MainWindow
        from hay_shed_search import north_selector
        stub = SimpleNamespace(
            envelope_plan_selector=SimpleNamespace(wall_states=lambda: {"front": True, "back": True}),
            wind_region_combo=SimpleNamespace(currentText=lambda: A_ENVELOPE_REGION))
        north = north_selector("63")
        self.assertEqual(north, "27°")
        self.assertEqual(MainWindow.wind_source_direction_for_case(stub, north, "Right +"), 63.)
        self.assertEqual(MainWindow.wind_source_direction_for_case(stub, north, "Left +"), 243.)
        self.assertEqual(MainWindow.wind_source_direction_for_case(stub, north, "End +"), 333.)


if __name__ == "__main__":
    unittest.main()
