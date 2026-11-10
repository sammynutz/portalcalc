"""Independent mechanics and full-solver comparisons for 3D screening."""
import copy
from dataclasses import replace
import json
import math
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from bracing_analysis import AnalysisError, AxialMember, AxialModel3D, PortalReduction
from bracing_analysis_desktop import prepare_analysis_models
from bracing_design import DesignCriteria, auto_size, solve_selection, fast_sway_proposal
from bracing_layout import BracingLayout, LayoutGeometry
from bracing_stiffness_screen import LightweightSlsScreen, MovementResponse


def rod(d):
    return {'label': f'{d} DIA ROD', 'area_mm2': math.pi*d*d/4}


def fixture(length=24000., diaphragm=False):
    from main import Node, FrameElement2D, Structure2D, ShapeCatalog
    g = LayoutGeometry.create(12000, length, 4000, 10, 'Gable', bay_mm=8000,
                              end_wall_stations_mm=[0, 6000, 12000])
    layout = BracingLayout(g)
    for panel in layout.panels.values():
        if '/bay/0' in panel.id or panel.face in ('front_end', 'back_end'):
            layout.toggle(panel.id, 'pinned')
    s = Structure2D()
    profile = SimpleNamespace(A=3000., I_analysis=40e6, name='Fixture portal')
    points = [(0., 0.)] + [(x, g.roof_z(x)) for x in g.roof_stations] + [(g.span_mm, 0.)]
    for i, (x, z) in enumerate(points):
        s.add_node(Node(i+1, x, z))
    for i, (a, b) in enumerate(zip(s.nodes, s.nodes[1:])):
        s.add_element(FrameElement2D(i+1, a, b, 200000., profile,
                      group='TOP' if 0 < i < len(points)-2 else 'COLUMN'))
    for node in (s.nodes[0], s.nodes[-1]):
        s.add_support(node.ux)
        s.add_support(node.uy)
    portal = PortalReduction(s, [(x, g.roof_z(x)) for x in g.roof_stations])
    cases = [{'name': f'{level} {direction}', 'level': level,
              'front_kpa': (.4 if direction == 1 else -.1)*scale,
              'back_kpa': (.1 if direction == 1 else -.4)*scale}
             for level, scale in (('ULS', 1.), ('SLS', .6)) for direction in (1, -1)]
    context = {'layout': layout, 'nominal_layout': layout.to_dict(), 'portal': portal,
               'cases': cases, 'source_hash': 'screen-fixture', 'source_preset': {},
               'front_clad': True, 'back_clad': True,
               'face_cladding': {'left_wall': True, 'right_wall': True},
               **{g: rod(16) for g in ('roof_brace', 'wall_brace', 'end_brace')}}
    if diaphragm:
        context['stressed_skin'] = {'enabled': True, 'side_walls_enabled': True,
                                    'end_walls_enabled': True}
    p = ShapeCatalog('Shape_Catalog.xlsx').profile_by_name['150*5 SHS']
    chosen = {'strut': p, 'end_post': p, **{g: rod(16) for g in ('roof_brace', 'wall_brace', 'end_brace')}}
    candidates = {g: [rod(d) for d in (16, 20, 24, 30, 36)]
                  for g in ('roof_brace', 'wall_brace', 'end_brace')}
    candidates.update(strut=[p], end_post=[p])
    return context, chosen, candidates


RATES = {'shs': 3., 'ub': 4., 'wb': 5.}


class ScreenTests(unittest.TestCase):
    def test_analytical_spring_derivative_and_reversal(self):
        model = AxialModel3D()
        model.add_node((0, 0, 0), fixed=True)
        model.add_node((1000, 0, 0))
        model.fixed.update((4, 5))
        model.add_member(AxialMember('spring', 0, 1, 100, kind='roof_brace'))
        cases = []
        for force in (1000., -1000.):
            f = np.array([0, 0, 0, force, 0, 0])
            solved = model.solve(f)
            cases.append({**solved, 'case': {}, 'nodal_loads_n': f.reshape(-1, 3).tolist()})
        screen = LightweightSlsScreen(model, cases, [MovementResponse('extension', ((3, 1.),), .1)])
        self.assertAlmostEqual(screen.evaluate('roof_brace', 2.), .25)
        step = 1e-5
        self.assertAlmostEqual((screen.predict('roof_brace', 1+step)-.5)/step, -.5, places=7)
        self.assertEqual(model.members[0].area_mm2, 100)

    def test_relative_response_preserves_sign(self):
        response = MovementResponse('adjacent', ((0, -1.), (3, 1.)), 10.)
        self.assertEqual(response.ratio([40, 0, 0, 40, 0, 0]), 0.)
        self.assertEqual(response.ratio([40, 0, 0, -40, 0, 0]), 8.)

    def test_lightweight_resolves_a_brace_becoming_slack(self):
        model = AxialModel3D()
        model.add_node((-1000, 0, 0), fixed=True)
        model.add_node((2000, 0, 0), fixed=True)
        model.add_node((0, 0, 0))
        model.add_node((1000, 0, 0))
        model.fixed.update((7, 8, 10, 11))
        model.add_member(AxialMember('left', 0, 2, 100, kind='roof_brace'))
        model.add_member(AxialMember('right', 3, 1, 100, kind='wall_brace'))
        model.add_member(AxialMember('tie', 2, 3, 100, tension_only=True, kind='end_brace'))
        force = np.zeros(12)
        force[6], force[9] = 1000., 2000.
        solved = model.solve(force)
        self.assertEqual(solved['members'][2]['state'], 'tension')
        captured = {**solved, 'case': {}, 'nodal_loads_n': force.reshape(-1, 3).tolist()}
        screen = LightweightSlsScreen(model, [captured], [MovementResponse('relative', ((9, 1.), (6, -1.)), .1)])
        # Right support four times stiffer: uA=0.05, uB=0.025; tie is slack.
        self.assertAlmostEqual(screen.evaluate('wall_brace', 4.), .25)

    def test_declared_wire_ea_matches_full_solver(self):
        from bracing_components import wire_options
        context, chosen, _ = fixture()
        wire = copy.deepcopy(wire_options()[-1])
        wire['rating'].update(ea_kn=12000., ea_source='Regression fixture', stiffness_basis='declared_ea')
        chosen['roof_brace'] = wire
        baseline = solve_selection(context, chosen, DesignCriteria(), RATES)
        local = {**context, **{g: chosen[g] for g in ('roof_brace', 'wall_brace', 'end_brace')}}
        _, model, _, _, _, _ = prepare_analysis_models(local, chosen['strut'], chosen['end_post'])
        responses = [MovementResponse(str(i), ((3*i+1, 1.),), p[2]/150)
                     for i, p in enumerate(model.nodes) if p[2] > 0]
        screen = LightweightSlsScreen(model, [c for c in baseline['cases'] if c['case']['level'] == 'SLS'], responses)
        upgraded = copy.deepcopy(wire)
        upgraded['rating']['ea_kn'] *= 2
        full = solve_selection(context, {**chosen, 'roof_brace': upgraded}, DesignCriteria(), RATES)
        self.assertAlmostEqual(screen.evaluate('roof_brace', 2.), full['design']['global_sls']['ratio'], places=8)

    def test_lightweight_matches_full_recovery_with_and_without_diaphragms(self):
        for diaphragm in (False, True):
            context, chosen, _ = fixture(diaphragm=diaphragm)
            criteria = DesignCriteria()
            baseline = solve_selection(context, chosen, criteria, RATES)
            local = {**context, **{g: chosen[g] for g in ('roof_brace', 'wall_brace', 'end_brace')}}
            _, model, _, _, _, _ = prepare_analysis_models(local, chosen['strut'], chosen['end_post'])
            responses = [MovementResponse(str(i), ((3*i+1, 1.),), p[2]/150)
                         for i, p in enumerate(model.nodes) if p[2] > 0]
            cases = [c for c in baseline['cases'] if c['case']['level'] == 'SLS']
            screen = LightweightSlsScreen(model, cases, responses)
            for group in ('roof_brace', 'wall_brace', 'end_brace'):
                full = solve_selection(context, {**chosen, group: rod(24)}, criteria, RATES)
                actual = screen.evaluate(group, (24/16)**2)
                self.assertAlmostEqual(actual, full['design']['global_sls']['ratio'], places=8)
                # First derivative agrees with a small independently solved change.
                step = 1e-5
                numerical = (screen.evaluate(group, 1+step)-baseline['design']['global_sls']['ratio'])/step
                analytical = (screen.predict(group, 1+step)-baseline['design']['global_sls']['ratio'])/step
                self.assertAlmostEqual(numerical, analytical, delta=2e-4)

    def test_neutral_baseline_falls_back(self):
        context, chosen, candidates = fixture()
        result = solve_selection(context, chosen, DesignCriteria(), RATES)
        for case in result['cases']:
            if case['case']['level'] == 'SLS':
                case['tangent_status'] = 'neutral_rods_required_stability_unverified'
        proposal = fast_sway_proposal(context, result, chosen, candidates, DesignCriteria(), RATES, set())
        self.assertIsNotNone(proposal)
        self.assertIn('verified taut-rod', result['stiffness_screening_trial']['fallback'])

    def test_captured_portal_loads_and_stale_baseline(self):
        context, chosen, candidates = fixture()
        portal = context['portal']
        for case in context['cases']:
            loads = np.zeros(len(portal.k))
            loads[portal.structure.nodes[2].uy] = -12000.
            case['portal_loads'] = [{'loads_n': loads.tolist()} for _ in context['layout'].geometry.bay_stations_mm]
        result = solve_selection(context, chosen, DesignCriteria(), RATES)
        proposal = fast_sway_proposal(context, result, chosen, candidates, DesignCriteria(), RATES, set())
        self.assertIsNotNone(proposal)
        self.assertNotIn('fallback', result['stiffness_screening_trial'])
        selected, _ = proposal
        full = solve_selection(context, selected, DesignCriteria(), RATES)
        self.assertAlmostEqual(full['design']['global_sls']['ratio'],
            result['stiffness_screening_trial']['selected']['physical_sls_ratio'], places=8)
        for case in result['cases']:
            if case['case']['level'] == 'SLS':
                case['displacements_mm'][0][1] += 1.
        fast_sway_proposal(context, result, chosen, candidates, DesignCriteria(), RATES, set())
        self.assertIn('baseline equilibrium', result['stiffness_screening_trial']['fallback'])

    def test_grouped_sizing_reanalyses_and_strength_only_skips_screen(self):
        context, chosen, candidates = fixture()
        initial = solve_selection(context, chosen, DesignCriteria(), RATES)
        criteria = DesignCriteria(global_sls_divisor=150*1.5/initial['design']['global_sls']['ratio'])
        result, selected = auto_size(context, chosen, candidates, criteria, RATES, max_solves=12)
        self.assertTrue(result['design']['passes'])
        self.assertTrue(result['sizing_search']['stiffness_screening'])
        self.assertTrue(solve_selection(context, selected, criteria, RATES)['design']['passes'])
        with patch('bracing_stiffness_screen.sway_proposal', side_effect=AssertionError('SLS disabled')):
            result, _ = auto_size(context, chosen, candidates, replace(criteria, include_serviceability=False), RATES)
        self.assertEqual(result['sizing_search']['stiffness_screening'], [])

    def test_per_location_sizing_uses_screen_and_preserves_member_areas(self):
        from bracing_components import StrutAssembly, StrutPlan
        context, chosen, candidates = fixture()
        profile = chosen['strut']
        g = context['layout'].geometry
        members = {}
        for bay in range(len(g.bay_stations_mm)-1):
            for line in range(len(g.roof_stations)):
                key = f'strut/bay/{bay}/line/{line}'
                members[key] = StrutAssembly(profile.name,
                    ({'type': 'steel', 'existing': False, 'profile': profile},), 'additional_steel',
                    {'id': key, 'line': line, 'bay': bay, 'length_mm': 8000})
        chosen['strut'] = StrutPlan(members, profile)
        initial = solve_selection(context, chosen, DesignCriteria(), RATES)
        criteria = DesignCriteria(global_sls_divisor=150*1.1/initial['design']['global_sls']['ratio'])
        result, selected = auto_size(context, chosen, candidates, criteria, RATES,
            max_solves=8, fast=True, retain_brace_stiffness=True)
        self.assertTrue(result['design']['passes'])
        self.assertTrue(result['sizing_search']['stiffness_screening'])
        for member in result['cases'][0]['members']:
            if member['kind'] == 'longitudinal_strut':
                self.assertAlmostEqual(member['area_mm2'], members[member['id']].A)
        self.assertTrue(solve_selection(context, selected, criteria, RATES)['design']['passes'])

    def test_screen_never_checks_or_recovers_portals_and_does_not_mutate_inputs(self):
        context, chosen, candidates = fixture()
        result = solve_selection(context, chosen, DesignCriteria(), RATES)
        original_cases = copy.deepcopy(context['cases'])
        original = dict(chosen)
        with patch.object(PortalReduction, 'recover', side_effect=AssertionError('unexpected portal recovery')):
            proposal = fast_sway_proposal(context, result, chosen, candidates, DesignCriteria(), RATES, set())
        self.assertIsNotNone(proposal)
        self.assertEqual(chosen, original)
        self.assertEqual(context['cases'], original_cases)
        self.assertLessEqual(len(result['stiffness_screening_trial']['lightweight_trials']), 4)
        json.dumps(result['stiffness_screening_trial'], allow_nan=False)


def benchmark():
    from statistics import median
    from bracing_analysis_desktop import end_wind_cases
    from wind import WindInputs
    context, chosen, candidates = fixture(length=40000.)
    context['cases'] = end_wind_cases(WindInputs(building_width_m=12., building_length_m=40.,
        eave_height_m=4., roof_pitch_deg=10., front_wall_clad=True, back_wall_clad=True,
        left_wall_clad=True, right_wall_clad=True))
    base = solve_selection(context, chosen, DesignCriteria(), RATES)
    divisor = 150*1.5/base['design']['global_sls']['ratio']
    criteria = DesignCriteria(global_sls_divisor=divisor)
    rows = []
    for enabled in (False, True):
        local = {**context, 'stiffness_screening': enabled}
        timings = []
        try:
            for _ in range(7):
                start = perf_counter()
                result, selected = auto_size(local, chosen, candidates, criteria, RATES,
                                          max_solves=12, fast=True, retain_brace_stiffness=True)
                timings.append(perf_counter()-start)
        except AnalysisError as error:
            rows.append({'screening': enabled, 'error': str(error), 'seconds': perf_counter()-start})
            continue
        elapsed = median(timings)
        rerun = solve_selection(local, selected, criteria, RATES)
        assert rerun['design']['passes']
        assert abs(rerun['design']['global_sls']['ratio']-result['design']['global_sls']['ratio']) < 1e-8
        rows.append({'screening': enabled, 'seconds': elapsed,
                     'timing_basis': 'Median of seven runs; fixed 12 x 40 x 4 m gable, 5 bays',
                     'load_cases': len(context['cases']), 'timings_seconds': timings,
                     'passes': result['design']['passes'], 'cost': result['design']['estimated_cost'],
                     'sls_ratio': result['design']['global_sls']['ratio'],
                     'sections': {g: selected[g]['label'] for g in ('roof_brace', 'wall_brace', 'end_brace')},
                     'exact_solves': result['sizing_search']['exact_solves'],
                     'screen_trials': result['sizing_search']['stiffness_screening']})
    return rows


if __name__ == '__main__':
    import sys
    if '--benchmark' in sys.argv:
        print(json.dumps(benchmark(), indent=2, allow_nan=False))
    else:
        unittest.main()
