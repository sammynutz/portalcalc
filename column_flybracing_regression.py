"""Physical segment, load-case and search regressions for automatic flybraces."""
import copy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PORTALCALC_LICENSE_ENFORCED'] = '0'
_appdata = tempfile.TemporaryDirectory(prefix='portalcalc_flybraces_')
os.environ['APPDATA'] = _appdata.name

from column_flybracing import FlybraceColumn, capture_case, extrema, girt_levels, segment_demand
from member_checks import MemberActions, check_member, reference_buckling_moment_knm
from main import MainWindow, Node, FrameElement2D, Structure2D
from PySide6.QtWidgets import QApplication


def basis(height=6000, moment=200, compression=20, girts=None):
    return {'base_mm': 0, 'top_mm': height, 'roof_node_mm': height, 'fixed_levels_mm': [],
            'in_plane_length_mm': height,
            'girt_levels_mm': girts or girt_levels(0, height, 1700),
            'cases': [{'name': 'gravity', 'pieces': [{'member': 1, 'y0': 0, 'y1': height,
                        'n': [-compression*1000, 0, 0], 'v': [0, 0, 0],
                        'm': [0, moment*1e6, 0, 0]}]}]}


class FlybraceRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.defaults = json.loads((Path(__file__).parent/'presets/factory_defaults.json').read_text())
        cls.section = cls.window.catalog.get('460 UB 74.6').check_properties()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def test_exact_moment_extrema_and_quarter_factor(self):
        self.assertEqual(extrema([0, 4, -4], 0, 1), (0, 1))
        case = basis()['cases'][0]
        demand = segment_demand(case, 0, 6000)
        self.assertAlmostEqual(demand['moment_modifier'], 1.7/math.sqrt(.25**2+.5**2+.75**2))
        uniform = copy.deepcopy(case)
        uniform['pieces'][0]['m'] = [100e6, 0, 0, 0]
        self.assertAlmostEqual(segment_demand(uniform, 0, 6000)['moment_modifier'], 1.7/math.sqrt(3))
        reversed_case = copy.deepcopy(case)
        reversed_case['pieces'][0]['m'] = [0, -200e6, 0, 0]
        self.assertAlmostEqual(segment_demand(reversed_case, 0, 6000)['moment_modifier'], demand['moment_modifier'])

    def test_ltb_and_in_plane_lengths_are_independent(self):
        actions = MemberActions(effective_length_y_mm=2000, effective_length_z_mm=6000, ltb_length_mm=2000)
        changed = replace(actions, effective_length_z_mm=12000)
        self.assertEqual(reference_buckling_moment_knm(self.section, actions),
                         reference_buckling_moment_knm(self.section, changed))
        self.assertLess(check_member(self.section, changed).checks['compression'].capacity,
                        check_member(self.section, actions).checks['compression'].capacity)

    def test_tall_column_adds_braces_and_caches_results(self):
        model = FlybraceColumn(basis(height=18000, moment=300, compression=10))
        result, actions, plan = model.choose(self.section)
        self.assertGreater(len(plan['positions_mm']), 1)
        self.assertTrue(plan['passes'], plan)
        self.assertEqual(actions.effective_length_z_mm, 18000)
        self.assertTrue(set(plan['positions_mm']) <= set(model.candidates))
        count = len(model.intervals)
        with mock.patch('column_flybracing.check_member', side_effect=AssertionError('rechecked cached plan')):
            self.assertIs(model.choose(self.section)[0], result)
        self.assertEqual(count, len(model.intervals))
        impossible = FlybraceColumn(basis(height=18000, moment=900, compression=10))
        self.assertFalse(impossible.choose(self.section)[2]['passes'])
        self.assertEqual(len(impossible.choose(self.section)[2]['positions_mm']), 1)

    def test_load_cases_are_not_combined_into_a_fictitious_interaction(self):
        data = basis(moment=100, compression=0)
        axial = copy.deepcopy(data['cases'][0]); axial['name'] = 'axial'
        axial['pieces'][0].update(n=[-50000, 0, 0], m=[0, 0, 0, 0])
        data['cases'].append(axial)
        result, _, plan = FlybraceColumn(data).choose(self.section)
        source = plan['sources']['combined']
        self.assertIn(source['combo'], ('gravity', 'axial'))
        self.assertTrue(source['actions']['moment_knm'] == 0 or source['actions']['compression_kn'] == 0)

    def test_capture_matches_loaded_element_interior_forces(self):
        section = self.window.catalog.get('460 UB 74.6')
        structure = Structure2D()
        a, b = Node(1, 0, 0), Node(2, 0, 6000)
        structure.add_node(a); structure.add_node(b)
        e = FrameElement2D(1, a, b, 200000, section, 'LEFT_COLUMN')
        structure.add_element(e)
        for dof in (a.ux, a.uy, a.rz): structure.add_support(dof)
        structure.add_linear_global_line_load(e, fx_start=1, fx_end=3, fy_start=-2, fy_end=-4)
        d, _, _ = structure.solve()
        from column_flybracing import value
        case = capture_case(structure, d, {'LEFT_COLUMN'}, 'test')['LEFT_COLUMN']
        for t in (0, .137, .25, .5, .75, .923, 1):
            expected = e.section_forces_at(d, t, structure.uniform_load_for_element(e))
            for key, target in zip(('n','v','m'), expected):
                self.assertAlmostEqual(value(case['pieces'][0][key], t), target, delta=1e-6)

    def test_actual_frame_provenance_export_and_no_extra_solves(self):
        w = self.window
        inputs = copy.deepcopy(self.defaults)
        inputs['geometry'].update(span_mm='30000', eave_height_mm='6750', truss_depth_mm='1300',
                                   max_truss_depth_mm='1300', roof_pitch_deg='7.5')
        inputs['sections'].update(left_column='410 UB 53.7', right_column='460 UB 74.6',
                                  top_chord='89*3.5 SHS', bottom_chord='89*3.5 SHS', webs_posts='75*2.5 SHS')
        inputs['loads'].update(bay_size_mm='8000', bay_size_m=8, g_kpa='.15', q_kpa='.25', solar_kpa='.15')
        inputs['wind'].update(region='A5', importance_level='1', frame_type='3 Sided', building_length_m='40',
                              wall_enclosure={'left': True, 'right': False, 'front': True, 'back': True})
        w.apply_preset_data(inputs)
        report = w.capture_computation_report({'project': 'Flybrace regression'})
        self.assertEqual(len(report['column_flybraces']), 1)
        row = report['column_flybraces'][0]
        self.assertEqual(row['group'], 'LEFT_COLUMN')
        self.assertTrue(row['passes'])
        self.assertAlmostEqual(row['roof_node_mm'], 5539)
        self.assertEqual(row['positions_mm'], [3525])
        left = next(m for m in report['members'] if m['group']=='LEFT_COLUMN')
        self.assertLess(left['ratio'], 1)
        self.assertEqual(left['actions']['effective_length_z_mm'], 5539)
        for check in left['checks']:
            self.assertIn('from_mm', check['source'])
            self.assertIn(check['source']['combo'], {c['name'] for c in report['cases']})
            actions = MemberActions(**check['source']['actions'])
            reproduced = check_member(w.catalog.get(left['profile']).check_properties(), actions)
            self.assertAlmostEqual(check['ratio'], reproduced.checks[check['key']].ratio, places=10)
        frozen = copy.deepcopy(w.collect_preset_data())
        with mock.patch.object(Structure2D, 'solve', side_effect=AssertionError('extra frame solve')):
            schedule = w.column_flybrace_schedule()
            dossier = w.calculation_design_dossier()
        self.assertEqual(frozen, w.collect_preset_data())
        self.assertEqual(dossier['column_flybraces'], schedule)
        json.dumps(dossier['column_flybraces'], allow_nan=False)
        self.assertIn('Automatic girt flybraces:', w.section_checks_box.toPlainText())
        self.assertFalse(w.member_check_restraints['RIGHT_COLUMN']['clad'])


if __name__ == '__main__':
    unittest.main()
