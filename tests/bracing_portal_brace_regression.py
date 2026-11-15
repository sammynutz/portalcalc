"""Portal frame braces: layout rules, frame mechanics and the 3D network.

Run:  python bracing_portal_brace_regression.py
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
from portalcalc.resources import data_path
from types import SimpleNamespace
import unittest

import numpy as np

from portalcalc.bracing.analysis import AnalysisError, PortalReduction, build_braced_building, solve_building_case
from bracing_analysis_regression import fixed_layout, frame, geometry
from portalcalc.bracing.layout import BracingLayout, SCHEMA_V3, is_portal_brace, portal_wall_panel
from portalcalc.bracing.portal_brace import PortalBraceFrame, Section, closed_form_stiffness

COLUMN = Section('310 UB 40.4', 5210., 86.4e6)
BEAM = Section('250 UB 31.4', 4010., 44.5e6)


def independent_frame(height, bay, column, beam):
    """The same pinned-base portal built with main.Structure2D."""
    from portalcalc.frame.solver import FrameElement2D, Node, Structure2D
    s = Structure2D()
    for i, (x, y) in enumerate(((0., 0.), (0., height), (bay, height), (bay, 0.))):
        s.add_node(Node(i+1, x, y))
    c = SimpleNamespace(A=column.area_mm2, I_analysis=column.inertia_mm4, name=column.name)
    b = SimpleNamespace(A=beam.area_mm2, I_analysis=beam.inertia_mm4, name=beam.name)
    n = s.nodes
    s.add_element(FrameElement2D(1, n[0], n[1], 200000., c, group='COLUMN'))
    s.add_element(FrameElement2D(2, n[1], n[2], 200000., b, group='TOP'))
    s.add_element(FrameElement2D(3, n[3], n[2], 200000., c, group='COLUMN'))
    for node in (n[0], n[3]):
        s.add_support(node.ux)
        s.add_support(node.uy)
    return s


class LayoutTests(unittest.TestCase):
    def test_portal_braces_are_kept_apart_from_x_panels(self):
        g = geometry()
        layout = BracingLayout(g, blocked=['right_wall/bay/0'], pinned=['left_wall/bay/0', 'portal_right/bay/0'])
        self.assertEqual(layout.pinned, {'left_wall/bay/0'})
        self.assertEqual(layout.portal_braces, {'portal_right/bay/0'})
        self.assertEqual(layout.selection, {'left_wall/bay/0', 'portal_right/bay/0'})
        self.assertFalse(any(is_portal_brace(p) for p in layout.available))   # X locations only
        self.assertIn('portal_right/bay/1', layout.available_portals)          # an X-blocked bay can take one
        self.assertEqual(portal_wall_panel('portal_right/bay/3'), 'right_wall/bay/3')

    def test_a_bay_cannot_have_x_bracing_and_a_portal_brace(self):
        g = geometry()
        with self.assertRaisesRegex(ValueError, 'both X bracing and a portal brace'):
            BracingLayout(g, pinned=['left_wall/bay/0', 'portal_left/bay/0'])
        layout = BracingLayout(g, pinned=['left_wall/bay/0'])
        with self.assertRaisesRegex(ValueError, 'Remove the X bracing'):
            layout.toggle('portal_left/bay/0', 'pinned')
        layout.toggle('portal_left/bay/1', 'pinned')
        with self.assertRaisesRegex(ValueError, 'Remove the portal brace'):
            layout.toggle('left_wall/bay/1', 'pinned')
        layout.toggle('portal_left/bay/1', 'blocked')                          # blocking unpins
        self.assertEqual((layout.portal_braces, layout.portal_blocked), (set(), {'portal_left/bay/1'}))

    def test_schema_v3_only_with_portal_braces_and_round_trip(self):
        g = geometry()
        self.assertNotEqual(BracingLayout(g, pinned=['left_wall/bay/0']).to_dict()['schema'], SCHEMA_V3)
        layout = BracingLayout(g, blocked=['portal_left/bay/1'], pinned=['left_wall/bay/0', 'portal_right/bay/0'])
        data = layout.to_dict()
        self.assertEqual(data['schema'], SCHEMA_V3)
        again = BracingLayout.from_dict(data)
        self.assertEqual((again.pinned, again.portal_braces, again.portal_blocked),
                         (layout.pinned, layout.portal_braces, layout.portal_blocked))


class FrameTests(unittest.TestCase):
    def test_condensed_stiffness_matches_an_independent_frame_solve(self):
        height, bay = 7500., 9000.
        brace = PortalBraceFrame(height, bay, COLUMN, BEAM)
        s = independent_frame(height, bay, COLUMN, BEAM)
        knee_a, knee_b = s.nodes[1], s.nodes[2]
        for loads in ((1000., 0.), (0., 1000.), (700., -300.)):
            s.clear_loads()
            s.add_load(knee_a.ux, loads[0])
            s.add_load(knee_b.ux, loads[1])
            d, reactions, _ = s.solve()
            expected = np.array([d[knee_a.ux], d[knee_b.ux]])
            np.testing.assert_allclose(np.linalg.solve(brace.stiffness, loads), expected, rtol=1e-8)
            # Recovered member end forces and base reactions match too.
            recovered = brace.recover(expected)
            np.testing.assert_allclose(recovered['interface_forces_n'], loads, atol=1e-6)
            for element, member in zip(s.elements, recovered['members']):
                np.testing.assert_allclose(member['local_n_n_nmm'], element.local_end_forces(d), rtol=1e-7, atol=1e-4)
            base_a, base_b = s.nodes[0], s.nodes[3]
            np.testing.assert_allclose([recovered['base_reactions_n'][0]['fy_n'], recovered['base_reactions_n'][0]['fz_n'],
                                        recovered['base_reactions_n'][1]['fy_n'], recovered['base_reactions_n'][1]['fz_n']],
                                       [reactions[base_a.ux], reactions[base_a.uy], reactions[base_b.ux], reactions[base_b.uy]],
                                       atol=1e-6)

    def test_sway_stiffness_matches_the_closed_form(self):
        for height, bay in ((7500., 9000.), (6000., 6000.), (9000., 12000.)):
            brace = PortalBraceFrame(height, bay, COLUMN, BEAM)
            closed = closed_form_stiffness(height, bay, COLUMN.inertia_mm4, BEAM.inertia_mm4)
            # Equal eave movements: the sum of the condensed matrix; columns' and
            # beam's axial strain make it slightly softer than the closed form.
            self.assertAlmostEqual(brace.stiffness.sum()/closed, 1., delta=.01)
            self.assertLessEqual(brace.stiffness.sum(), closed)

    def test_knee_moments_and_overturning_follow_statics(self):
        height, bay = 7500., 9000.
        brace = PortalBraceFrame(height, bay, COLUMN, BEAM)
        u = np.linalg.solve(brace.stiffness, [5000., 5000.])   # 10 kN total at the eave
        recovered = brace.recover(u)
        columns = {m['member']: m for m in recovered['members']}
        for name in ('column_a', 'column_b'):
            self.assertAlmostEqual(columns[name]['moment_nmm'], 5000.*height, delta=1.)   # P/2 * h
            self.assertAlmostEqual(abs(columns[name]['axial_n']), 10000.*height/bay, delta=1.)
        self.assertAlmostEqual(sum(b['fy_n'] for b in recovered['base_reactions_n']), -10000., places=6)


class NetworkTests(unittest.TestCase):
    def three_sided(self, portals=('portal_right/bay/0', 'portal_right/bay/1')):
        g = geometry()
        layout = fixed_layout(g, end=True)
        layout = BracingLayout(g, blocked=[f'right_wall/bay/{b}' for b in range(len(g.bay_stations_mm)-1)],
                               pinned=sorted({p for p in layout.pinned if not p.startswith('right_wall/')} | set(portals)))
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        return g, layout, macro

    def test_open_side_portal_braces_carry_longitudinal_load_and_balance(self):
        g, layout, macro = self.three_sided()
        model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500,
                                                          portal_brace_sections={'column': COLUMN, 'beam': BEAM})
        self.assertEqual([b['id'] for b in model.portal_braces], ['portal_right/bay/0', 'portal_right/bay/1'])
        result = solve_building_case(model, layout, macro, dofs, lookup, {'front_kpa': 1., 'back_kpa': .3})
        self.assertLess(max(map(abs, result['balance_force_error_n'])), 1e-4)
        braces = result['portal_braces']
        self.assertEqual(len(braces), 2)
        brace_y = sum(base['fy_n'] for brace in braces for base in brace['base_reactions_n'])
        self.assertLess(brace_y, -1.)   # the open side's braces take part of the +Y load
        path = result['load_path']
        self.assertIn('portal_right', path['member_base_reactions_n'])
        self.assertAlmostEqual(path['member_base_reactions_n']['portal_right'][1], brace_y, places=6)
        self.assertLess(abs(path['roof_to_member_base_y_residual_n']), 1e-3*abs(path['applied_roof_y_n']))

    def test_portal_braces_stiffen_the_open_side(self):
        g, layout, macro = self.three_sided()
        stiff = BracingLayout(g, sorted(layout.all_blocked), sorted(layout.selection))
        bare = BracingLayout(g, sorted(layout.all_blocked), sorted(layout.pinned))
        case = {'front_kpa': 1., 'back_kpa': .3}
        movements = []
        for local in (bare, stiff):
            model, roof, dofs, lookup = build_braced_building(local, macro, 1500, 300, 300, 300, 1500,
                                                              portal_brace_sections={'column': COLUMN, 'beam': BEAM})
            result = solve_building_case(model, local, macro, dofs, lookup, case)
            right = [row[len(g.roof_stations)-1] for row in roof]
            movements.append(max(abs(result['displacements_mm'][n][1]) for n in right))
        self.assertLess(movements[1], movements[0])

    def test_stiffness_screen_ranks_portal_brace_inertia(self):
        from portalcalc.bracing.stiffness_screen import LightweightSlsScreen, MovementResponse
        g, layout, macro = self.three_sided()
        sections = {'column': COLUMN, 'beam': BEAM}
        model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500,
                                                          portal_brace_sections=sections)
        case = {'name': 'SLS', 'level': 'SLS', 'front_kpa': .6, 'back_kpa': .2}
        solved = solve_building_case(model, layout, macro, dofs, lookup, case)
        right = [row[len(g.roof_stations)-1] for row in roof]
        responses = [MovementResponse(f'eave {n}', ((3*n+1, 1.),), 1.) for n in right]
        screen = LightweightSlsScreen(model, [solved], responses)
        stiff = {'column': Section(COLUMN.name, COLUMN.area_mm2, 2*COLUMN.inertia_mm4), 'beam': BEAM}
        model2, roof2, dofs2, lookup2 = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500,
                                                              portal_brace_sections=stiff)
        exact = solve_building_case(model2, layout, macro, dofs2, lookup2, case)
        expected = max(abs(exact['displacements_mm'][n][1]) for n in right)
        self.assertAlmostEqual(screen.evaluate('portal_brace_column', 2.), expected, places=6)
        baseline = max(abs(solved['displacements_mm'][n][1]) for n in right)
        self.assertLess(expected, baseline)
        # The local derivative agrees with a small exact step; the blocks are restored after a trial.
        self.assertAlmostEqual(screen.predict('portal_brace_beam', 1.01), screen.evaluate('portal_brace_beam', 1.01),
                               delta=1e-3*baseline)
        self.assertAlmostEqual(screen.evaluate('portal_brace_beam', 1.), baseline, places=6)

    def test_portal_braces_need_sections(self):
        g, layout, macro = self.three_sided()
        with self.assertRaisesRegex(AnalysisError, 'portal brace column and beam sections'):
            build_braced_building(layout, macro, 1500, 300, 300, 300, 1500)


class DesignTests(unittest.TestCase):
    """Portal brace groups through solve_selection/auto_size on a small three-sided shed."""

    @classmethod
    def setUpClass(cls):
        from portalcalc.frame.sections import ShapeCatalog
        cls.catalog = ShapeCatalog(str(data_path("Shape_Catalog.xlsx")))

    def context(self):
        from bracing_design_regression import rod
        from portalcalc.bracing.layout import LayoutGeometry
        g = LayoutGeometry.create(6000, 16000, 3000, 10, "Gable", end_wall_stations_mm=[0, 6000])   # 2 bays
        pinned = [p for p in BracingLayout(g).panels if p.startswith(('roof/bay/0/', 'roof/bay/1/', 'left_wall/'))]
        pinned += ['front_end/panel/0', 'back_end/panel/0', 'portal_right/bay/0', 'portal_right/bay/1']
        layout = BracingLayout(g, blocked=['right_wall/bay/0', 'right_wall/bay/1'], pinned=pinned)
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        cases = [{"name": f"{level} {direction}", "level": level,
                  "front_kpa": (.4 if direction == 1 else -.1)*scale, "back_kpa": (.1 if direction == 1 else -.4)*scale}
                 for level, scale in (("ULS", 1.), ("SLS", .6)) for direction in (1, -1)]
        context = {"layout": layout, "nominal_layout": layout.to_dict(), "portal": macro, "cases": cases,
                   "source_hash": "fixture", "source_preset": {}, "front_clad": True, "back_clad": True,
                   "roof_brace": rod(16), "wall_brace": rod(16), "end_brace": rod(16)}
        from portalcalc.bracing.portal_brace import portal_candidates
        p = self.catalog.profile_by_name
        ubs = portal_candidates(self.catalog.ub_profiles())
        by_name = {c.name: c for c in ubs}
        selections = {"strut": p["100*4 SHS"], "end_post": p["100*4 SHS"], "roof_brace": rod(20), "wall_brace": rod(20),
                      "end_brace": rod(16), "portal_brace_column": by_name['310 UB 40.4'], "portal_brace_beam": by_name['250 UB 31.4']}
        candidates = {"strut": [p["100*4 SHS"]], "end_post": [p["100*4 SHS"]], "roof_brace": [rod(16), rod(20)],
                      "wall_brace": [rod(16), rod(20)], "end_brace": [rod(16)],
                      "portal_brace_column": ubs[:8], "portal_brace_beam": ubs[:8]}
        return context, selections, candidates

    def test_checks_follow_the_recovered_actions_and_cost_uses_stock_lengths(self):
        from portalcalc.bracing.design import DesignCriteria, solve_selection
        context, selections, _ = self.context()
        rates = {"shs": 3., "ub": 4., "wb": 5., "portal_brace_allowance": 500.}
        result = solve_selection(context, selections, DesignCriteria(), rates)
        checks = result['section_checks']
        column = checks['portal_right/bay/0/column_a']
        uls = [case for case in result['cases'] if case['case']['level'] == 'ULS']
        recovered = next(m for m in uls[0]['portal_braces'][0]['members'] if m['member'] == 'column_a')
        row = next(r for r in column['case_checks'] if r['case'] == uls[0]['case']['name'])
        self.assertAlmostEqual(row['actions']['moment_knm'], recovered['moment_nmm']/1e6, places=9)
        self.assertEqual(row['actions']['effective_length_z_mm'], 2*recovered['length_mm'])   # in-plane sway, pinned base
        self.assertEqual(row['actions']['ltb_length_mm'], min(1500., recovered['length_mm']))
        self.assertIn('portal_right/bay/1/beam', checks)
        items = {item['group']: item for item in result['design']['cost_items']}
        column_item, beam_item = items['portal_brace_column'], items['portal_brace_beam']
        self.assertEqual(column_item['member_count'], 4)
        # 3 m columns round up to 4.5 m stock; 8 m beams to 8.25 m.
        self.assertAlmostEqual(column_item['purchased_length_m'], 4*4.5)
        self.assertAlmostEqual(beam_item['total'], selections['portal_brace_beam'].A*2*8.25*.00785*4.)
        self.assertEqual(items['portal_brace_allowance']['total'], 1000.)
        self.assertAlmostEqual(result['design']['estimated_cost'], sum(item['total'] for item in result['design']['cost_items']))
        self.assertIn('portal_brace_column', result['sections'])
        self.assertTrue(any('Portal frame braces are UB moment frames' in line for line in result['limitations']))

    def test_moment_modifier_follows_as4100_table_5_6_1(self):
        from portalcalc.bracing.design import linear_moment_modifier, member_segments
        self.assertEqual(linear_moment_modifier(5., 5.), 1.)       # uniform single curvature
        self.assertEqual(linear_moment_modifier(5., -5.), 2.5)     # reverse curvature, 3.1 capped at 2.5
        self.assertEqual(linear_moment_modifier(0., 5.), 1.75)     # zero at one end (pinned base)
        self.assertAlmostEqual(linear_moment_modifier(-2.5, 5.), 1.75+1.05*.5+.3*.25)
        self.assertAlmostEqual(linear_moment_modifier(4., 5.), 1.75-1.05*.8+.3*.64)
        # Local end moments [.., M1, .., .., M2]: internal moment runs -M1 to M2.
        segments = member_segments([0, 0, 0, 0, 0, 7.5e6], 7600., 1500.)
        self.assertEqual(len(segments), 6)
        self.assertAlmostEqual(segments[-1][0], 7600./6)
        self.assertAlmostEqual(segments[-1][2], 7.5e6)
        self.assertEqual(len(member_segments([0, 0, 1e6, 0, 0, 1e6], 9000., 0.)), 1)   # 0 = the whole span

    def test_beam_double_curvature_kr_and_column_segments(self):
        from dataclasses import replace
        from portalcalc.bracing.design import DesignCriteria, solve_selection
        context, selections, _ = self.context()
        rates = {"shs": 3., "ub": 4., "wb": 5.}
        result = solve_selection(context, selections, DesignCriteria(), rates)
        beam = result['section_checks']['portal_right/bay/0/beam']
        uls = [row for row in beam['case_checks'] if row['level'] == 'ULS']
        self.assertTrue(all(row['actions']['moment_modifier'] > 2. for row in uls))   # sway: double curvature
        self.assertTrue(all(row['actions']['kr'] == .85 for row in uls))
        column = result['section_checks']['portal_right/bay/0/column_a']
        row = max((r for r in column['case_checks'] if r['level'] == 'ULS'), key=lambda r: r['ratio'])
        self.assertEqual(row['segment_count'], 2)                  # 3 m column, 1.5 m restraints
        self.assertEqual(row['segment'], 1)                        # the top segment governs
        self.assertAlmostEqual(row['actions']['moment_modifier'], 1.75-1.05*.5+.3*.25, places=6)
        both_knees = solve_selection(context, selections, DesignCriteria(portal_brace_beam_kr=.7), rates)
        ratio = lambda r, key: r['section_checks'][key]['ratio']
        self.assertLessEqual(ratio(both_knees, 'portal_right/bay/0/beam'), ratio(result, 'portal_right/bay/0/beam'))
        from portalcalc.bracing.analysis import AnalysisError
        for bad in (dict(portal_brace_beam_kr=.6), dict(portal_brace_beam_kr=1.1), dict(portal_brace_beam_restraint_mm=-1.)):
            with self.assertRaises(AnalysisError):
                replace(DesignCriteria(), **bad).validate()

    def test_allowance_defaults_to_zero_and_auto_size_sizes_the_portal_groups(self):
        from portalcalc.bracing.design import DesignCriteria, auto_size, solve_selection
        context, selections, candidates = self.context()
        rates = {"shs": 3., "ub": 4., "wb": 5.}
        result = solve_selection(context, selections, DesignCriteria(), rates)
        self.assertEqual({i['group']: i for i in result['design']['cost_items']}['portal_brace_allowance']['total'], 0.)
        best, chosen = auto_size(context, selections, candidates, DesignCriteria(), rates)
        self.assertTrue(best['design']['passes'])
        self.assertIn('portal_brace_column', chosen)
        self.assertTrue(all(row['passes'] for key, row in best['section_checks'].items() if key.startswith('portal_')))

    def test_spacing_rule_counts_a_portal_braced_bay(self):
        from portalcalc.bracing.spacing import spacing_assessment
        from portalcalc.bracing.layout import LayoutGeometry
        g = LayoutGeometry.create(6000, 48000, 3000, 10, "Gable", end_wall_stations_mm=[0, 6000])   # 6 bays
        roof = lambda b: [f'roof/bay/{b}/strip/{j}' for j in range(len(g.roof_stations)-1)]
        blocked = [f'right_wall/bay/{b}' for b in range(6)] + [f'left_wall/bay/{b}' for b in range(6)]
        layout = BracingLayout(g, blocked=blocked, pinned=roof(0)+roof(5)+['portal_right/bay/0', 'portal_right/bay/5'])
        self.assertEqual(spacing_assessment(layout, 5)['anchor_bays'], [1, 6])
        self.assertTrue(spacing_assessment(layout, 5)['passes'])


if __name__ == '__main__':
    unittest.main()
