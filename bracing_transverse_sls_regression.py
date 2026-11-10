"""Transverse SLS cases and per-frame sway evidence on the factory-default building.

Checks serviceability scaling of the ULS side-wind patterns, the reported
frame sway against an independent 2D portal solve, and that the existing
longitudinal acceptance is unchanged by the added cases.

Run:  $env:QT_QPA_PLATFORM='offscreen'; python bracing_transverse_sls_regression.py
"""
import copy
import json
import os
import unittest

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')

RATES = {'shs': 3., 'ub': 4., 'wb': 5., 'purlin': 3.}
WINDOW = None


def setUpModule():
    global WINDOW, APP
    from PySide6.QtWidgets import QApplication
    from main import MainWindow
    APP = QApplication.instance() or QApplication([])
    WINDOW = MainWindow()
    WINDOW.confirm_on_close = False
    with open('presets/factory_defaults.json', encoding='utf-8') as handle:
        WINDOW.apply_preset_data(json.load(handle))
    WINDOW.generate_and_solve()


def tearDownModule():
    WINDOW.close()


def named(cases, name):
    for case in cases:
        if case['name'] == name or name in case.get('equivalent_case_names', []):
            return case
    raise KeyError(name)


def building():
    """Context with an end-bay layout plus end-wall twist restraint."""
    from bracing_analysis_desktop import prepare_context
    from bracing_layout import BracingLayout
    from bracing_layout_search import practical_seeds
    context = prepare_context(WINDOW, allow_unconfigured=True)
    selected = dict(practical_seeds(context, set()))['End roof/wall bays with end-wall twist restraint']
    g = context['layout'].geometry
    context['layout'] = BracingLayout(g, sorted(context['layout'].blocked), sorted(selected))
    catalog = WINDOW.catalog
    strut = min([*catalog.shs_profiles(), *catalog.rhs_profiles()], key=lambda p: (abs(p.A-1500), p.name))
    post = catalog.profile_by_name[context['default_end_post']]
    chosen = {'strut': strut, 'end_post': post, **{k: context[k] for k in ('roof_brace', 'wall_brace', 'end_brace')}}
    return context, chosen, selected


class RoofNormalPressureTests(unittest.TestCase):
    """Main 2D roof wind acts normal to each panel and matches the 3D frame loads."""

    def test_panel_resultant_is_normal_and_conserved(self):
        from types import SimpleNamespace
        from wind import add_roof_panel_pressure

        class Loads:
            def __init__(self):
                self.loads = {}

            def add_load(self, dof, value):
                self.loads[dof] = self.loads.get(dof, 0.) + value

        node = lambda i, x, y: SimpleNamespace(x=x, y=y, ux=3*i, uy=3*i+1)
        for a, b in ((node(0, 0., 0.), node(1, 4000., 1000.)), (node(0, 4000., 1000.), node(1, 0., 0.)),
                     (node(0, 0., 1000.), node(1, 4000., 0.))):
            loads = Loads()
            nx, ny = add_roof_panel_pressure(loads, a, b, 1000.)
            fx = loads.loads.get(0, 0.)+loads.loads.get(3, 0.)
            fy = loads.loads[1]+loads.loads[4]
            self.assertAlmostEqual(np.hypot(fx, fy), 1000., places=9)
            self.assertGreater(fy, 0.)  # positive is outward/uplift
            # Normal: perpendicular to the panel. Vertical part is pressure x plan length.
            self.assertAlmostEqual(fx*(b.x-a.x)+fy*(b.y-a.y), 0., places=6)
            self.assertAlmostEqual(fy, 1000.*abs(b.x-a.x)/np.hypot(b.x-a.x, b.y-a.y), places=9)

    def test_skillion_high_side_wind_uses_banded_table_5_3a(self):
        import math
        from wind import calculate_wind, low_pitch_cpe_values
        try:
            WINDOW.roof_shape_combo.setCurrentText('Skillion')
            for pitch in (5., 12.):
                WINDOW.pitch_input.setText(str(pitch))
                inputs = WINDOW.wind_inputs_from_ui(float(WINDOW.span_input.text())*1000, pitch,
                                                    float(WINDOW.eave_input.text())*1000)
                result = calculate_wind(inputs)
                total = result.roof_slope_length_m
                rows = low_pitch_cpe_values(result.h_over_d)
                for case, index in (('Right +', 3), ('Right -', 4)):
                    strips = result.case_roof_strips(case)
                    self.assertGreater(len(strips), 1, f'{case} at {pitch} deg is banded, not uniform')
                    self.assertAlmostEqual(strips[-1][1], total, places=9)
                    # Windward band at the high (right) edge, measured horizontally.
                    first = rows[0]
                    self.assertAlmostEqual(strips[-1][0], total - min(first[2]*result.h_m/math.cos(math.radians(pitch)), total), places=9)
                    self.assertEqual(np.sign(strips[-1][2]), np.sign(-first[index]) or 0.)
                    self.assertAlmostEqual(strips[0][0], 0., places=9)
        finally:
            with open('presets/factory_defaults.json', encoding='utf-8') as handle:
                WINDOW.apply_preset_data(json.load(handle))
            WINDOW.generate_and_solve()

    def test_main_ws_sway_matches_3d_portal_alone(self):
        import re
        from bracing_analysis_desktop import portal_eave_nodes, prepare_context
        try:
            for shape, pitch in (('Gable', '7.5'), ('Gable', '22.5'), ('Skillion', '5'), ('Skillion', '12')):
                WINDOW.roof_shape_combo.setCurrentText(shape)
                WINDOW.pitch_input.setText(pitch)
                WINDOW.generate_and_solve()
                item = WINDOW.design_envelope_actions['sls_column_top_deflection']
                side, sign, cpi = re.match(r'SLS: Ws \((Left|Right) ([+-]), (Cpi [+-])\)', item['combo']).groups()
                context = prepare_context(WINDOW, allow_unconfigured=True)
                portal = context['portal']
                name = f"SLS / {'Right to left' if side == 'Right' else 'Left to right'} / {side} {sign} / {cpi} / Ws"
                loads = named(context['cases'], name)['portal_loads']
                frame = np.asarray(loads[len(loads)//2]['loads_n'])
                d = np.zeros(len(portal.k))
                d[portal.free] = np.linalg.solve(portal.k[np.ix_(portal.free, portal.free)], frame[portal.free])
                left, right = portal_eave_nodes(portal.structure)
                sway = max(abs(d[left.ux]), abs(d[right.ux]))
                # Same frame, same pressures, both normal to the roof: within 1%.
                self.assertAlmostEqual(sway/item['value'], 1., delta=.01, msg=f'{shape} {pitch} deg')
        finally:
            with open('presets/factory_defaults.json', encoding='utf-8') as handle:
                WINDOW.apply_preset_data(json.load(handle))
            WINDOW.generate_and_solve()


class TransverseSlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bracing_analysis_desktop import analyse_context
        cls.context, cls.chosen, cls.selected = building()
        cls.result = analyse_context(cls.context, cls.chosen['strut'], cls.chosen['end_post'])

    def test_sls_cases_scale_the_uls_patterns(self):
        from wind import CPI_CASE_OPTIONS
        cases = self.context['cases']
        gravity = np.asarray(named(cases, 'SLS / G')['portal_loads'][0]['loads_n'])
        transverse = [c for c in cases if c.get('wind_family') == 'transverse']
        self.assertTrue(any(c['level'] == 'SLS' for c in transverse))
        checked = 0
        for direction in ('Left to right', 'Right to left'):
            for cpe in ('Left +', 'Left -') if direction == 'Left to right' else ('Right +', 'Right -'):
                for cpi in CPI_CASE_OPTIONS:
                    stem = f'{direction} / {cpe} / {cpi}'
                    uls = named(cases, f'ULS / {stem} / 0.9G + Wu')
                    ws = named(cases, f'SLS / {stem} / Ws')
                    gws = named(cases, f'SLS / {stem} / G + Ws')
                    scale = ws['wind_pressure_scale']
                    self.assertTrue(0 < scale < 1)
                    self.assertEqual((ws['g_factor'], gws['g_factor']), (0., 1.))
                    self.assertAlmostEqual(ws['front_kpa'], scale*uls['front_kpa'], places=9)
                    self.assertAlmostEqual(ws['back_kpa'], scale*uls['back_kpa'], places=9)
                    for frame, (u, s, g) in enumerate(zip(uls['portal_loads'], ws['portal_loads'], gws['portal_loads'])):
                        # End frames carry half the tributary gravity; frame 0 G applies to the first only.
                        wind_uls = np.asarray(u['loads_n']) - .9*np.asarray(named(cases, 'SLS / G')['portal_loads'][frame]['loads_n'])
                        np.testing.assert_allclose(np.asarray(s['loads_n']), scale*wind_uls, atol=1e-6*max(1., np.abs(wind_uls).max()))
                        np.testing.assert_allclose(np.asarray(g['loads_n'])-np.asarray(s['loads_n']),
                                                   np.asarray(named(cases, 'SLS / G')['portal_loads'][frame]['loads_n']), atol=1e-6)
                    checked += 1
        self.assertEqual(checked, 4*len(CPI_CASE_OPTIONS))
        self.assertGreater(np.abs(gravity).max(), 0)

    def test_frame_sway_matches_recovery_and_independent_2d_solve(self):
        from bracing_analysis_desktop import portal_eave_nodes
        summary = self.result['transverse_sls']
        portal = self.context['portal']
        g = self.context['layout'].geometry
        self.assertEqual(summary['status'], 'REPORT_ONLY')
        self.assertEqual(len(summary['frames']), len(g.bay_stations_mm))
        self.assertEqual(summary['divisor'], self.context['global_sls_basis']['divisor'])
        left, right = portal_eave_nodes(portal.structure)
        structure = copy.deepcopy(portal.structure)
        stiffness = structure.elastic_stiffness_matrix()
        for frame in summary['frames']:
            row = frame['governing_ws']
            self.assertEqual(row['combination'], 'Ws')
            solved = next(c for c in self.result['cases'] if c['case']['name'] == row['case'])
            recovered = solved['portals'][frame['index']]
            ux = {n['node']: n['ux_mm'] for n in recovered['node_displacements']}
            self.assertAlmostEqual(row['left_eave_mm'], ux[left.id], places=9)
            self.assertAlmostEqual(row['right_eave_mm'], ux[right.id], places=9)
            self.assertAlmostEqual(row['ratio'], max(abs(ux[left.id])/(left.y/summary['divisor']),
                                                     abs(ux[right.id])/(right.y/summary['divisor'])), places=9)
            # Independent unrestrained solve through the production 2D solver path.
            loads = np.asarray(solved['case']['portal_loads'][frame['index']]['loads_n'])
            alone = structure.solve_with_stiffness(stiffness, loads)
            self.assertAlmostEqual(row['portal_alone_mm'], max(abs(alone[left.ux]), abs(alone[right.ux])), places=6)
            interface = sum(f[0] for f in recovered['interface_forces_n'])/1000
            self.assertAlmostEqual(row['secant_restraint_kn_mm'], -interface/(.5*(ux[left.id]+ux[right.id])), places=9)
        # Struts carry no X force: frames outside roof-braced bays get no restraint.
        braced = {int(p.split('/')[2]) for p in self.selected if p.startswith('roof/')}
        lines = braced | {b+1 for b in braced}
        for frame in summary['frames']:
            row = frame['governing_ws']
            if frame['index'] not in lines:
                self.assertAlmostEqual(row['movement_mm'], row['portal_alone_mm'], places=6)
            else:
                self.assertGreater(row['secant_restraint_kn_mm'], 0)
                self.assertLess(row['movement_mm'], row['portal_alone_mm'])

    def test_longitudinal_acceptance_and_reports_exclude_transverse_sls(self):
        from bracing_design import DesignCriteria, solve_selection
        from bracing_analysis_desktop import text_report, transverse_sls
        criteria = DesignCriteria()
        full = solve_selection(self.context, self.chosen, criteria, RATES)
        without = {**self.context, 'cases': [c for c in self.context['cases'] if not transverse_sls(c)]}
        reference = solve_selection(without, self.chosen, criteria, RATES)
        self.assertIsNone(reference['transverse_sls'])
        for key in ('ratio', 'case', 'node', 'movement_mm'):
            self.assertEqual(full['design']['global_sls'][key], reference['design']['global_sls'][key])
        self.assertEqual(full['governing_sls_displacement']['case'], reference['governing_sls_displacement']['case'])
        self.assertNotIn('/ Ws', full['design']['global_sls']['case'])
        self.assertEqual(full['design']['passes'], reference['design']['passes'])
        text = text_report(full)
        self.assertIn('TRANSVERSE SLS FRAME SWAY (REPORT ONLY)', text)
        self.assertIn('SLS TRANSVERSE PRESSURE PATTERN', text)

    def test_transverse_sls_uses_bare_network_with_diaphragms(self):
        from bracing_analysis_desktop import analyse_context
        context = {**self.context, 'stressed_skin': {'enabled': True, 'side_walls_enabled': True, 'end_walls_enabled': True}}
        result = analyse_context(context, self.chosen['strut'], self.chosen['end_post'])
        for case in result['cases']:
            if case['case'].get('wind_family') == 'transverse' and case['case']['level'] == 'SLS':
                self.assertNotIn('bare_sls', case)
                self.assertFalse(case.get('diaphragm_members'))
        for mine, bare in zip(result['transverse_sls']['frames'], self.result['transverse_sls']['frames']):
            self.assertAlmostEqual(mine['governing_ws']['movement_mm'], bare['governing_ws']['movement_mm'], places=9)


if __name__ == '__main__':
    unittest.main()
