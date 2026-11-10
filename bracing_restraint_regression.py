"""Adopted 3D frame restraint in the 2D portal, deferred transverse SLS and roof bands.

Run:  $env:QT_QPA_PLATFORM='offscreen'; python bracing_restraint_regression.py
"""
import copy
import math
import unittest

import numpy as np

import bracing_transverse_sls_regression as base
from bracing_transverse_sls_regression import RATES, building, setUpModule, tearDownModule  # noqa: F401


def window():
    return base.WINDOW


class SlackPlayTests(unittest.TestCase):
    """Both diagonals of an X panel slack: bounded play, not a mechanism."""

    def panel(self, diagonals):
        from bracing_analysis import AxialMember, AxialModel3D
        model = AxialModel3D()
        for point, fixed in (((0, 0, 0), True), ((1000, 0, 0), True), ((0, 1000, 0), False), ((1000, 1000, 0), False)):
            model.add_node(point, fixed=fixed)
        model.fixed.update((8, 11))  # free nodes move in the panel plane only
        for name, a, b in (('post-left', 0, 2), ('post-right', 1, 3), ('top', 2, 3)):
            model.add_member(AxialMember(name, a, b, 500.))
        for name, a, b in diagonals:
            model.add_member(AxialMember(name, a, b, 100., tension_only=True, kind='wall_brace'))
        loads = np.zeros(12)
        loads[[7, 10]] = -10000.   # push the free nodes down: both diagonals shorten
        return model, loads

    def test_both_diagonals_slack_reports_play_equal_to_post_shortening(self):
        model, loads = self.panel((('d1', 0, 3), ('d2', 1, 2)))
        result = model.solve(loads)
        self.assertEqual(result['tangent_status'], 'slack_play_stability_unverified')
        self.assertEqual(result['slack_play']['modes'], 1)
        # Sway along X by the post shortening P L / E A takes up a diagonal's slack.
        self.assertAlmostEqual(result['slack_play']['play_mm'], 10000.*1000./(200000.*500.), places=6)
        self.assertEqual(sorted(result['slack_play']['rods']), ['d1', 'd2'])
        self.assertTrue(all(m['state'] == 'slack' for m in result['members'] if m['tension_only']))

    def test_single_diagonal_settles_neutral_not_slack(self):
        # One diagonal: the solver settles with it at zero force, as before.
        model, loads = self.panel((('d1', 0, 3),))
        result = model.solve(loads)
        self.assertEqual(result['tangent_status'], 'neutral_rods_required_stability_unverified')
        self.assertNotIn('slack_play', result)

    def test_combined_modes_are_checked_exactly(self):
        from bracing_analysis import no_escaping_direction
        # Two modes, each engaged both ways on its own axis: no combination escapes.
        square = np.array([[1., 0.], [-1., 0.], [0., 1.], [0., -1.]])
        self.assertTrue(no_escaping_direction(square))
        # Every basis direction engages a rod, but a = (1, 1) extends none:
        # rates (1,-1), (-1,1) and (-1,-1) all <= 0 along it.
        escaping = np.array([[1., -1.], [-1., 1.], [-1., -1.]])
        self.assertFalse(no_escaping_direction(escaping))
        self.assertFalse(no_escaping_direction(np.array([[1., 0.], [-1., 0.]])))   # second mode unrestrained

    def test_zero_force_x_panels_under_gravity_are_bounded_with_zero_play(self):
        # Gravity only, symmetric: both diagonals sit at zero force.
        from bracing_analysis import AxialMember, AxialModel3D
        model = AxialModel3D()
        for point, fixed in (((0, 0, 0), True), ((1000, 0, 0), True), ((0, 1000, 0), False), ((1000, 1000, 0), False)):
            model.add_node(point, fixed=fixed)
        model.fixed.update((8, 11, 7, 10))     # free in X only: only the diagonals resist
        model.add_member(AxialMember('top', 2, 3, 500.))
        for name, a, b in (('d1', 0, 3), ('d2', 1, 2)):
            model.add_member(AxialMember(name, a, b, 100., tension_only=True, kind='wall_brace'))
        result = model.solve(np.zeros(12), allow_neutral_equilibrium=True)
        self.assertEqual(result['tangent_status'], 'neutral_rods_required_stability_unverified')
        play = result['slack_play']
        self.assertEqual((play['modes'], play['play_mm'], play['zero_force_rods']), (1, 0., 2))
        self.assertTrue(play['directions_verified'])
        from bracing_design import slack_play_assessment
        case = {'case': {'name': 'G'}, 'tangent_status': result['tangent_status'], 'slack_play': play}
        self.assertTrue(slack_play_assessment({'cases': [case]}, 1.)['stable'])

    def test_mode_engaging_no_rod_one_way_is_a_mechanism(self):
        from bracing_analysis import bounded_slack_play
        tangent = np.diag([1., 0.])                 # second coordinate has no stiffness
        extension = np.array([-.2, -.3])
        active = np.array([False, False])
        both_ways = np.array([[0., 1.], [0., -1.]])  # one slack rod each way along it
        play = bounded_slack_play(tangent, both_ways, extension, active)
        self.assertEqual((play['modes'], play['play_mm']), (1, .3))
        one_way = np.array([[0., 1.], [1., 0.]])     # nothing engages moving the other way
        self.assertIsNone(bounded_slack_play(tangent, one_way, extension, active))


class SlackPlayAcceptanceTests(unittest.TestCase):
    @staticmethod
    def result(*cases):
        return {'cases': [{'case': {'name': name}, 'tangent_status': status,
                           **({'slack_play': {'modes': modes, 'play_mm': play}} if play is not None else {}),
                           **({'bare_sls': bare} if bare else {})}
                          for name, status, modes, play, bare in cases]}

    def test_single_mode_within_limit_is_accepted(self):
        from bracing_design import slack_play_assessment
        taut = ('A', 'positive_with_taut_rods', None, None, None)
        slack = ('B', 'slack_play_stability_unverified', 1, .21, None)
        assessed = slack_play_assessment(self.result(taut, slack), 1.)
        self.assertTrue(assessed['stable'])
        self.assertEqual((assessed['evidence']['accepted_cases'], assessed['evidence']['max_accepted_play_mm']), (1, .21))
        self.assertIsNone(slack_play_assessment(self.result(taut), 1.)['evidence'])

    def test_larger_play_several_modes_neutral_rods_or_zero_limit_stay_unverified(self):
        from bracing_design import slack_play_assessment
        for case, limit in ((('B', 'slack_play_stability_unverified', 1, 1.2, None), 1.),
                            (('B', 'slack_play_stability_unverified', 2, .1, None), 1.),   # combinations not verified
                            (('B', 'neutral_rods_required_stability_unverified', None, None, None), 1.),
                            (('B', 'slack_play_stability_unverified', 1, .21, None), 0.),
                            (('B', 'positive_with_taut_rods', None, None,
                              {'tangent_status': 'slack_play_stability_unverified', 'slack_play': {'modes': 1, 'play_mm': 3.}}), 1.)):
            assessed = slack_play_assessment(self.result(case), limit)
            self.assertFalse(assessed['stable'], (case, limit))
            self.assertEqual(assessed['evidence']['unverified_count'], 1)

    def test_criterion_allows_zero_but_not_negative(self):
        from bracing_design import DesignCriteria
        from bracing_analysis import AnalysisError
        self.assertEqual(DesignCriteria().slack_play_limit_mm, 1.)
        DesignCriteria(slack_play_limit_mm=0.).validate()
        with self.assertRaises(AnalysisError):
            DesignCriteria(slack_play_limit_mm=-.1).validate()


class SwaySpringTests(unittest.TestCase):
    def test_resists_sway_not_spread(self):
        from main import Node, FrameElement2D, Structure2D
        from types import SimpleNamespace
        profile = SimpleNamespace(A=3000., I_analysis=40e6, name='Fixture')
        s = Structure2D()
        for i, (x, y) in enumerate(((0, 0), (0, 4000), (8000, 4000), (8000, 0))):
            s.add_node(Node(i+1, x, y))
        for i, (a, b) in enumerate(zip(s.nodes, s.nodes[1:])):
            s.add_element(FrameElement2D(i+1, a, b, 200000., profile, group='TOP' if i == 1 else 'COLUMN'))
        for n in (s.nodes[0], s.nodes[-1]):
            s.add_support(n.ux); s.add_support(n.uy); s.add_support(n.rz)
        left, right = s.nodes[1], s.nodes[2]
        free_k = s.elastic_stiffness_matrix()
        s.add_sway_spring((left.ux, right.ux), 500.)
        k = s.elastic_stiffness_matrix()
        np.testing.assert_allclose(k, k.T)
        d = np.zeros(len(k))
        d[left.ux], d[right.ux] = 3., 3.    # sway: spring force k * 3 shared equally
        np.testing.assert_allclose(((k-free_k) @ d)[[left.ux, right.ux]], [750., 750.])
        np.testing.assert_allclose(s.spring_reaction_vector(d)[[left.ux, right.ux]], [750., 750.])
        d[right.ux] = -3.                   # spread: no spring force
        np.testing.assert_allclose(((k-free_k) @ d)[[left.ux, right.ux]], [0., 0.], atol=1e-12)


class RestraintAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bracing_analysis_desktop import prepare_context
        from bracing_design import DesignCriteria, solve_selection
        from bracing_layout import BracingLayout
        from bracing_layout_search import practical_seeds
        w = window()
        cls.context, cls.chosen, end_bays = building()
        seeds = dict(practical_seeds(prepare_context(w, allow_unconfigured=True), set()))
        cls.girder = next(v for k, v in seeds.items() if 'continuous supported' in k and 'girder' in k)
        g = cls.context['layout'].geometry
        cls.results = {}
        for name, pinned in (('girder', cls.girder), ('end_bays', end_bays)):
            local = {**cls.context, 'layout': BracingLayout(g, [], sorted(pinned))}
            cls.results[name] = solve_selection(local, cls.chosen, DesignCriteria(), RATES)
        cls.nominal = prepare_context(w, allow_unconfigured=True)['nominal_layout']

    def tearDown(self):
        w = window()
        w.eave_x_restraint_combo.setCurrentText('None')
        w.bracing_3d_restraint = w.bracing_location_layout = w.bracing_3d_settings = None
        w.generate_and_solve()

    def store(self, pinned):
        w = window()
        w.bracing_location_layout = {**copy.deepcopy(self.nominal), 'pinned_panels': sorted(pinned)}
        c = self.chosen
        w.bracing_3d_settings = {'roof_brace_label': c['roof_brace']['label'], 'wall_brace_label': c['wall_brace']['label'],
                                 'end_brace_label': c['end_brace']['label'], 'strut_section': c['strut'].name,
                                 'end_post_section': c['end_post'].name, 'mixed_struts': False}

    def test_summary_excludes_end_frames_and_needs_every_interior_frame(self):
        girder = self.results['girder']['frame_restraint']
        self.assertTrue(girder['adoptable'])
        interior = [f for f in girder['frames'] if 0 < f['index'] < len(girder['frames'])-1]
        self.assertAlmostEqual(girder['adoptable_kn_mm'], min(f['least']['secant_restraint_kn_mm'] for f in interior))
        self.assertNotIn(girder['governing']['frame'], (0, len(girder['frames'])-1))
        # End-bay bracing never reaches the middle frames: no credit.
        end_bays = self.results['end_bays']['frame_restraint']
        self.assertFalse(end_bays['adoptable'])
        self.assertEqual(end_bays['adoptable_kn_mm'], 0.)
        self.assertIn('no roof bracing reaches it', end_bays['reason'])
        # Workspace summary: one headline line; long scope text moves to the detail.
        from bracing_sizing_diagnostics import result_summary
        summary = result_summary(self.results['girder'])
        self.assertIn('2D frame restraint: %.3f kN/mm adoptable' % girder['adoptable_kn_mm'], summary['headline'])
        self.assertIn('Side-wind frame sway (report only)', summary['headline'])
        self.assertNotIn('Demand scope', summary['headline'])
        self.assertIn('Demand scope', summary['detail'])
        self.assertIn('2D frame restraint: none', result_summary(self.results['end_bays'])['headline'])

    def test_adopted_spring_matches_3d_sway_and_goes_stale(self):
        w = window()
        unrestrained = w.design_envelope_actions['sls_column_top_deflection']['value']
        label = w.bracing_layout_summary_label
        self.store(self.girder)
        # A result that cannot be adopted says why on the Bracing tab.
        w.adopt_bracing_3d_restraint(self.results['end_bays'])
        self.assertIn('No 2D frame restraint credit from this bracing: frame', label.text())
        self.assertIn('checked unrestrained', label.text())
        record, reason = w.adopt_bracing_3d_restraint(self.results['girder'])
        self.assertIsNone(reason)
        self.assertIn('Not in use: set End-wall portal restraint', label.text())
        w.eave_x_restraint_combo.setCurrentText('3D bracing model')
        w.generate_and_solve()
        self.assertEqual(w.bracing_3d_restraint_status['state'], 'applied')
        self.assertIn('In use.', label.text())
        self.assertEqual(w.structure.springs, {})
        [(dofs, k)] = w.structure.sway_springs
        self.assertAlmostEqual(k, record['stiffness_kn_mm']*1000.)
        restrained = w.design_envelope_actions['sls_column_top_deflection']['value']
        frames = self.results['girder']['transverse_sls']['frames']
        interior = max(f['governing_ws']['movement_mm'] for f in frames[1:-1])
        self.assertLess(restrained, unrestrained)
        # The least interior restraint is adopted, so 2D is close and not less than 3D.
        self.assertGreaterEqual(restrained, interior*.99)
        self.assertLess(restrained, interior*1.10)
        self.assertIn('3D bracing restraint:', w.results_box.toPlainText())
        # 3D analysis must not inherit the credit it supplies.
        from bracing_analysis_desktop import prepare_context
        self.assertEqual(prepare_context(w, allow_unconfigured=True)['portal'].structure.sway_springs, [])
        self.assertEqual(prepare_context(w, allow_unconfigured=True)['adopted_restraint'], record)
        # Portal sections changed: credit kept, flagged for recheck.
        _, combo, _ = w.active_auto_size_group_specs()[0]
        combo.setCurrentIndex((combo.currentIndex()+1) % combo.count())
        w.generate_and_solve()
        self.assertEqual(w.bracing_3d_restraint_status['state'], 'recheck')
        self.assertTrue(w.structure.sway_springs)
        self.assertIn('RECHECK 3D', label.text())
        # Bracing changed: no credit.
        w.bracing_3d_settings['roof_brace_label'] = '30 DIA ROD'
        w.generate_and_solve()
        self.assertEqual(w.bracing_3d_restraint_status['state'], 'stale')
        self.assertEqual(w.structure.sway_springs, [])
        self.assertIn('STALE', label.text())

    def test_floors_keep_credited_brace_stiffness(self):
        from bracing_restraint import adoption_record, floor_candidates, brace_ea_n
        from bracing_schedule import rod_options
        self.store(self.girder)
        w = window()
        record, _ = adoption_record(self.results['girder'], w.bracing_location_layout, w.bracing_3d_settings, w.collect_preset_data())
        rods = list(rod_options())
        floored = floor_candidates({g: rods for g in ('roof_brace', 'wall_brace', 'end_brace')}, record)
        for group in ('roof_brace', 'wall_brace', 'end_brace'):
            floor = record['brace_ea_floors'][group]['ea_n']
            self.assertTrue(floored[group])
            self.assertTrue(all(brace_ea_n(o) >= floor*(1-1e-9) for o in floored[group]))
            self.assertIn(self.chosen[group]['label'], [o['label'] for o in floored[group]])
        self.assertIs(floor_candidates({'roof_brace': rods}, None)['roof_brace'], rods)


class DeferredTransverseSlsTests(unittest.TestCase):
    def test_sizing_matches_and_returns_every_case(self):
        from bracing_design import DesignCriteria, auto_size
        from bracing_schedule import rod_options
        context, chosen, _ = building()
        catalog = window().catalog
        candidates = {g: list(rod_options()) for g in ('roof_brace', 'wall_brace', 'end_brace')}
        candidates.update(strut=sorted([*catalog.shs_profiles(), *catalog.rhs_profiles()], key=lambda p: p.A)[:10],
                          end_post=[chosen['end_post']])
        deferred, picked = auto_size(context, chosen, candidates, DesignCriteria(), RATES, fast=True)
        direct, direct_picked = auto_size({**context, 'transverse_sls_deferred': True}, chosen, candidates,
                                          DesignCriteria(), RATES, fast=True)
        self.assertEqual(deferred['design_request']['sections'], direct['design_request']['sections'])
        self.assertEqual(len(deferred['cases']), len(context['cases']))
        self.assertEqual(deferred['transverse_sls_search']['deferred_cases'], 16)
        self.assertNotIn('transverse_sls_search', direct)
        self.assertEqual(deferred['design']['passes'], direct['design']['passes'])
        self.assertAlmostEqual(deferred['design']['estimated_cost'], direct['design']['estimated_cost'])
        for mine, theirs in zip(deferred['transverse_sls']['frames'], direct['transverse_sls']['frames']):
            self.assertAlmostEqual(mine['governing_ws']['movement_mm'], theirs['governing_ws']['movement_mm'], places=9)


    def test_unstable_final_check_repeats_search_with_every_case(self):
        from unittest import mock
        import bracing_design
        from bracing_analysis import AnalysisError
        from bracing_design import DesignCriteria, auto_size
        from bracing_schedule import rod_options
        context, chosen, _ = building()
        catalog = window().catalog
        candidates = {g: list(rod_options()) for g in ('roof_brace', 'wall_brace', 'end_brace')}
        candidates.update(strut=sorted([*catalog.shs_profiles(), *catalog.rhs_profiles()], key=lambda p: p.A)[:10],
                          end_post=[chosen['end_post']])
        real = bracing_design.solve_selection

        def unstable_final(*args, reuse_cases=None, **kwargs):
            if reuse_cases:
                raise AnalysisError('SLS / Left to right / Left - / Cpi - / Ws: Unstable fixture')
            return real(*args, **kwargs)
        with mock.patch('bracing_design.solve_selection', side_effect=unstable_final):
            result, _ = auto_size(context, chosen, candidates, DesignCriteria(), RATES, fast=True)
        evidence = result['transverse_sls_search']
        self.assertTrue(evidence['repeated_with_all_cases'])
        self.assertIn('Unstable fixture', evidence['final_check_error'])
        self.assertEqual(len(result['cases']), len(context['cases']))


class HorizontalRoofBandTests(unittest.TestCase):
    def test_low_pitch_zones_are_horizontal_bands_along_the_slope(self):
        from wind import WindInputs, calculate_wind, low_pitch_cpe_values
        result = calculate_wind(WindInputs(roof_pitch_deg=8., building_width_m=20., eave_height_m=6.))
        scale = 1/math.cos(math.radians(8.))
        for zone, (name, start, end, _, _) in zip(result.roof_zones, low_pitch_cpe_values(result.h_over_d)):
            self.assertEqual(zone.name, name)
            self.assertAlmostEqual(zone.distance_from_eave_from_m, start*result.h_m*scale)
            self.assertAlmostEqual(zone.distance_from_eave_to_m, end*result.h_m*scale)


if __name__ == '__main__':
    unittest.main()
