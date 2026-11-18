"""Optimise's purlin row trial: rows added on chord-governed trusses, kept only if cheaper.

Asserts state and costs, not report wording. The 21 m factory shed is chord
governed and, when this was written, saved money with one extra row; the test
does not pin that outcome (purlin selection and costing may move it) but does
pin that the adopted run is the cheapest verified one and is never below the
user's own rows.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import copy
import json
import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PORTALCALC_LICENSE_ENFORCED", "0")

from portalcalc.resources import data_path
from portalcalc.frame.engine.headless import HeadlessFrameEngine
from portalcalc.frame.engine.optimiser import OPTIMISER_PURLIN_ROW_TRIALS


def preset(span_mm="21000", user_rows=0):
    data = json.loads(data_path("presets/factory_defaults.json").read_text(encoding="utf-8"))
    data["geometry"]["span_mm"] = span_mm
    data["sections"]["extra_purlin_rows"] = user_rows
    data["sections"].pop("optimiser_purlin_rows", None)
    return data


def engine_for(data):
    engine = HeadlessFrameEngine()
    engine.apply_preset_data(copy.deepcopy(data))
    engine.generate_and_solve()
    return engine


def trial_costs(report):
    """{added rows: cost} from the trial section of the Optimise report."""
    section = report.split("PURLIN ROW TRIAL", 1)[1] if "PURLIN ROW TRIAL" in report else ""
    return {int(m.group(1)): float(m.group(2).replace(",", ""))
            for m in re.finditer(r"\+(\d+)\D[^$\n]*\$([\d,]+)", section)}


class PurlinRowStateTests(unittest.TestCase):
    def test_preset_round_trip_and_manual_change(self):
        engine = engine_for(preset(user_rows=1))
        self.assertEqual((engine.extra_purlin_rows, engine.optimiser_purlin_rows), (1, 0))
        self.assertNotIn("optimiser_purlin_rows", engine.collect_preset_data()["sections"])

        data = preset(user_rows=0)
        data["sections"].update(extra_purlin_rows=2, optimiser_purlin_rows=1)
        engine.apply_preset_data(copy.deepcopy(data))
        self.assertEqual((engine.extra_purlin_rows, engine.optimiser_purlin_rows), (2, 1))
        saved = engine.collect_preset_data()["sections"]
        self.assertEqual((saved["extra_purlin_rows"], saved["optimiser_purlin_rows"]), (2, 1))

        # Optimise rows can never exceed the total.
        data["sections"].update(extra_purlin_rows=1, optimiser_purlin_rows=3)
        engine.apply_preset_data(copy.deepcopy(data))
        self.assertEqual((engine.extra_purlin_rows, engine.optimiser_purlin_rows), (1, 1))

        # A manual change makes all rows the user's.
        engine.adjust_extra_purlin_rows(1)
        self.assertEqual((engine.extra_purlin_rows, engine.optimiser_purlin_rows), (2, 0))

    def test_capture_and_restore_carry_rows(self):
        engine = engine_for(preset())
        specs = engine.active_auto_size_group_specs()
        engine.extra_purlin_rows, engine.optimiser_purlin_rows = 2, 1
        state = engine.auto_size_capture_state(specs, ratio=0.0, cost=0.0)
        engine.extra_purlin_rows, engine.optimiser_purlin_rows = 0, 0
        engine.auto_size_restore_state(state)
        self.assertEqual((engine.extra_purlin_rows, engine.optimiser_purlin_rows), (2, 1))


class PurlinRowTrialTests(unittest.TestCase):
    def check_trial(self, user_rows):
        engine = engine_for(preset(user_rows=user_rows))
        result = engine.auto_size_current_preset(show_completion=False)
        self.assertTrue(result["verified"])
        costs = trial_costs(result["report"])
        self.assertIn(0, costs, "21 m factory shed is chord governed, so the trial must run")
        self.assertIn(OPTIMISER_PURLIN_ROW_TRIALS[0], costs)
        adopted = engine.optimiser_purlin_rows
        self.assertEqual(result["optimiser_purlin_rows"], adopted)
        self.assertEqual(engine.extra_purlin_rows, user_rows + adopted)
        # The adopted run is the cheapest one tried, and is the design left in place.
        self.assertAlmostEqual(costs[adopted], min(costs.values()), delta=1.0)
        self.assertAlmostEqual(float(result["cost"]), costs[adopted], delta=1.0)
        self.assertAlmostEqual(float(engine.costing_summary["total_cost"]), costs[adopted], delta=1.0)
        # +2 is only tried when +1 saved money.
        if 2 in costs:
            self.assertLess(costs[1], costs[0])
        saved = engine.collect_preset_data()["sections"]
        self.assertEqual(saved["extra_purlin_rows"], user_rows + adopted)
        self.assertEqual(saved.get("optimiser_purlin_rows", 0), adopted)
        return engine, costs

    def test_trial_from_no_user_rows_and_rerun_starts_from_user_rows(self):
        engine, _ = self.check_trial(user_rows=0)
        # A second Optimise starts again from the user's rows (here 0): rows
        # it added before are taken off, so they never build up. Started from
        # the optimised sections it may not be chord governed, and then it
        # must not add rows at all.
        result = engine.auto_size_current_preset(show_completion=False)
        self.assertTrue(result["verified"])
        costs = trial_costs(result["report"])
        self.assertEqual(engine.extra_purlin_rows, engine.optimiser_purlin_rows)
        if costs:
            self.assertAlmostEqual(costs[engine.optimiser_purlin_rows], min(costs.values()), delta=1.0)
        else:
            self.assertEqual(engine.extra_purlin_rows, 0)

    def test_user_rows_are_a_minimum(self):
        engine, _ = self.check_trial(user_rows=1)
        self.assertGreaterEqual(engine.extra_purlin_rows, 1)


if __name__ == "__main__":
    unittest.main()
