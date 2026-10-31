"""Independent single-span manufacturer-table and added-strut checks."""
import json
from pathlib import Path
import unittest

from bracing_analysis import AnalysisError
from purlin_strut_tables import snapshot, table_row, single_span_capacities
from purlin_shared_design import location_data, solve_shared, shared_check, ARRANGEMENT
from purlin_combined import properties
from bracing_components import location_options
from bracing_design import DesignCriteria


def fixture(count=1, active=(0,)):
    bundle = snapshot(json.loads(Path(__file__).with_name('metroll_megaspan_capacity_tables.json').read_text()))
    assemblies = []
    for bay in range(2):
        data = {'section': 'C/Z 250 15', 'area_mm2': 638., 'analysis_span_mm': 8000.,
                'basis_key': 'TEST', 'axial_rating': {'purlin_shape': 'C'},
                'bending_snapshot': {'bridging_rows': 1}, 'beam_inputs': {
                    'span_type': 'Double span', 'outward_kn_m': 1., 'inward_strength_kn_m': 1.,
                    'service_kn_m': .8, 'service_outward_kn_m': .8, 'service_inward_kn_m': .1,
                    'permanent_kn_m': .2, 'gravity_service_kn_m': .45}}
        refs, added = location_data({'purlin_strut_tables': bundle}, data, 8000.)
        loc = {'id': f'strut/bay/{bay}/line/0', 'bay': bay, 'line': 0, 'length_mm': 8000.,
               'purlin': data, 'existing_count': count, 'shared_reference_caps': refs, 'added_purlins': added}
        options = location_options(loc, [])
        assemblies.append(next(o for o in options if o.arrangement == ARRANGEMENT and
                          o.components[-1]['data']['section'] == data['section']) if bay in active else options[0])
    return assemblies


class StrutTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = json.loads(Path(__file__).with_name('metroll_megaspan_capacity_tables.json').read_text())
        cls.bundle = snapshot(cls.tables)

    def test_actual_single_span_rows_and_bridging(self):
        c = single_span_capacities(self.bundle, 'C/Z 250 15', 8000., 1)
        self.assertEqual(c['outward_capacity_kn_m'], 1.57)
        self.assertEqual(c['inward_capacity_kn_m'], 1.62)
        self.assertEqual(c['service_capacity_kn_m'], 1.2)
        self.assertEqual(c['tables'], '1A / 1B')
        self.assertEqual(c['source_sha256'], self.bundle['sha256'])
        a = single_span_capacities(self.bundle, 'C 200 19', 8000., 1)
        b = single_span_capacities(self.bundle, 'C 200 19', 8000., 2)
        self.assertEqual(a['outward_capacity_kn_m'], 1.67)
        self.assertEqual(b['outward_capacity_kn_m'], 1.79)
        self.assertEqual(a['table_evidence']['1A']['bridging'], 'B1')

    def test_interpolation_uses_common_bridging_columns_and_rejects_extrapolation(self):
        table = self.bundle['tables']['1A']
        a = table_row(table, 'C/Z 200 19', 7500.)
        b = table_row(table, 'C/Z 200 19', 8000.)
        mid = table_row(table, 'C/Z 200 19', 7750.)
        self.assertAlmostEqual(mid['strength_by_bridging_kn_m']['B1'], (a['strength_by_bridging_kn_m']['B1']+b['strength_by_bridging_kn_m']['B1'])/2)
        self.assertTrue(mid['interpolated'])
        for span in (2000., 25000.):
            with self.assertRaises(AnalysisError):
                single_span_capacities(self.bundle, 'C 250 15', span, 10)
        with self.assertRaisesRegex(AnalysisError, 'at least 2 bridging'):
            single_span_capacities(self.bundle, 'C 150 19', 8000., 1)
        for rows in (-1, True, 1.5):
            with self.assertRaises(AnalysisError):
                single_span_capacities(self.bundle, 'C 250 15', 8000., rows)


class SharedMechanicsTests(unittest.TestCase):
    def test_half_load_changes_continuous_support_and_adjacent_span_moments(self):
        run = solve_shared(fixture(), [8000., 8000.], 0)
        c = next(c for c in run['cases'] if c['name'] == 'ULS outward')
        a, b = [v['roof'] for v in c['bays']]
        # Two-span theorem: interior moment = -(q1+q2)L²/16.
        self.assertAlmostEqual(a['positive_moment_knm'], 6., places=7)
        self.assertAlmostEqual(a['negative_moment_knm'], 1.5625, places=7)
        self.assertAlmostEqual(b['negative_moment_knm'], 5.28125, places=7)
        self.assertGreater(b['negative_moment_knm'], 4.5)  # adjoining bay can increase
        added = c['bays'][0]['added']
        self.assertAlmostEqual(added['negative_moment_knm'], .5*8000**2/8/1e6)
        self.assertNotAlmostEqual(added['negative_moment_knm'], a['negative_moment_knm'])
        self.assertAlmostEqual(c['applied_load_n'], -16000.)
        self.assertLess(abs(c['applied_load_n']+c['support_reaction_n']), 1e-6)

    def test_linked_count_load_conservation_and_added_selfweight(self):
        r = solve_shared(fixture(count=2), [8000., 8000.], 0)
        out = r['cases'][0]
        self.assertAlmostEqual(out['applied_load_n'], -32000.)
        self.assertEqual(out['loads']['roof_n_mm_by_bay'], [-.5, -1.])
        self.assertEqual(out['loads']['added_n_mm_by_bay'], [-1., 0.])
        own = 638.*7.85e-6*9.80665
        inward = r['cases'][1]
        self.assertAlmostEqual(inward['applied_load_n'], 32000+1.2*own*8000)
        for c in r['cases']:
            self.assertLess(abs(c['applied_load_n']+c['support_reaction_n']), 1e-5)

    def test_reference_workings_and_sls_retain_table_and_elastic_limits(self):
        assemblies = fixture()
        r = solve_shared(assemblies, [8000., 8000.], 0)
        entry = {'line': '0', 'bay': 0, 'error': None}
        added = assemblies[0].components[-1]
        checked = shared_check(r, entry, added, -5., 'test', 'ULS', 150., DesignCriteria())
        ns = 903*638*(96.41/8000)**2
        self.assertTrue(checked['preliminary'])
        self.assertAlmostEqual(checked['compression_capacity_kn'], ns)
        self.assertAlmostEqual(checked['ratio'], 5/ns+checked['reference_envelope_ratio'])
        self.assertEqual(checked['table_reference']['capacity']['tables'], '1A / 1B')
        self.assertAlmostEqual(checked['reference_comparisons'][0]['reference_moment_knm'], 1.57*8000**2/8/1e6)
        sls = shared_check(r, entry, added, 0., 'test', 'SLS', 150., DesignCriteria())
        self.assertGreaterEqual(sls['deflection_mm'], sls['elastic_deflection_mm'])
        self.assertGreaterEqual(sls['deflection_mm'], sls['table_adjusted_deflection_mm'])
        self.assertAlmostEqual(sls['ratio'], sls['deflection_mm']/(8000/150))

    def test_unsupported_restraint_and_reference_configurations_cannot_pass(self):
        a = fixture()
        a[0].components[-1]['data']['shared_cladding_fixed'] = False
        with self.assertRaisesRegex(AnalysisError, 'screw-fixed'):
            solve_shared(a, [8000., 8000.], 0)
        with self.assertRaisesRegex(AnalysisError, 'Unequal'):
            solve_shared(fixture(), [8000., 7000.], 0)
        a = fixture()
        a[0].location['purlin']['axial_rating']['beam_roof_releases'] = '1'
        with self.assertRaisesRegex(AnalysisError, 'Nonstandard'):
            solve_shared(a, [8000., 8000.], 0)


class SharedWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bracing_components_regression import ComponentWorkspaceTests
        ComponentWorkspaceTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from bracing_components_regression import ComponentWorkspaceTests
        ComponentWorkspaceTests.tearDownClass.__func__(cls)

    def dialog(self, saved=None):
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        return BracingAnalysisDialog(prepare_context(self.window), self.window.catalog, saved)

    def test_current_layout_sizing_competes_with_steel_reanalyses_and_roundtrips(self):
        from bracing_design import solve_selection
        d = self.dialog()
        try:
            d.run_auto_size()
            self.assertIsNotNone(d.result, d.status_label.text())
            self.assertTrue(d.result['design']['passes'], d.status_label.text())
            self.assertTrue(d.result['design']['preliminary_reference_checks'])
            self.assertIn('PRELIMINARY', d.status_label.text())
            plan = d.selections()['strut']
            added = [a for a in plan.members.values() if a.arrangement == ARRANGEMENT]
            self.assertTrue(added)
            self.assertTrue(any(a.arrangement == 'existing_purlins' for a in plan.members.values()))
            self.assertTrue(all(d.component_editor.added_purlin_combos[a.location['id']].currentData() == a.components[-1]['data']['section'] for a in added))
            rerun = solve_selection(d.current_context(), d.selections(), d.criteria(), d.context['cost_rates'])
            self.assertTrue(rerun['design']['passes'])
            self.assertEqual({k:v['ratio'] for k,v in rerun['section_checks'].items()}, {k:v['ratio'] for k,v in d.result['section_checks'].items()})
            self.assertIn('PRELIMINARY PURLIN TABLE-REFERENCE WORKINGS', d.report_text)
            self.assertTrue(d.beam_results.runs)
            d.show()
            self.app.processEvents()
            d.tabs.setCurrentWidget(d.beam_results)
            for mode in range(d.beam_results.mode.count()):
                d.beam_results.mode.setCurrentIndex(mode)
                self.assertFalse(d.beam_results.grab().isNull())
            saved = d.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(saved, restored.snapshot())
                restored.run_analysis()
                self.assertTrue(restored.result['design']['passes'], restored.status_label.text())
            finally:
                restored.reject()
            json.dumps(d.result, allow_nan=False)
            from PySide6.QtCore import Qt
            d.component_editor.shared_table.item(0, 1).setCheckState(Qt.Unchecked)
            self.assertIsNone(d.result)
            self.assertFalse(d.export_json_button.isEnabled())
        finally:
            d.reject()

    def test_fixed_selection_missing_cladding_and_insufficient_bridging_do_not_pass(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        d = self.dialog()
        try:
            editor = d.component_editor
            loc, arrangement, _ = editor.strut_rows[1]
            arrangement.setCurrentIndex(arrangement.findData(ARRANGEMENT))
            key = loc['purlin']['basis_key']
            row = editor.rating_keys.index(key)
            editor.shared_table.item(row, 1).setCheckState(Qt.Unchecked)
            d.run_analysis()
            self.assertIsNotNone(d.result)
            self.assertIsNone(d.result['section_checks'][loc['id']]['ratio'])
            editor.shared_table.item(row, 1).setCheckState(Qt.Checked)
            editor.shared_table.setItem(row, 2, QTableWidgetItem('0'))
            d.run_analysis()
            self.assertIsNone(d.result)
            self.assertIn('unavailable', d.status_label.text())
        finally:
            d.reject()


if __name__ == '__main__':
    unittest.main(verbosity=2)
