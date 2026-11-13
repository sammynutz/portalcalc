"""Open canopies, open-ended roofs and roof friction in the 3D bracing model.

Independent checks with synthetic portals (no Qt): canopy gravity and wind
resultants against hand calculations, continuous-roof side-wind bands, roof
friction surfaces, open-ended and fully open buildings, and an uncondensed
full-matrix comparison of a loaded canopy frame.

Run:  python bracing_canopy_regression.py
"""
import copy
import math
from dataclasses import replace
import unittest
from types import SimpleNamespace

import numpy as np

from bracing_analysis import AnalysisError, PortalReduction, build_braced_building, solve_building_case, end_wall_load_vector
from bracing_analysis_desktop import end_wind_cases, roof_friction_drag
from bracing_analysis_regression import geometry, frame, fixed_layout
from bracing_roof_loads import building_roof_cases, canopy_parts, _snapshot
from bracing_side_wind import half_slope_x, roof_cpe_segments
from wind import WindInputs, calculate_wind, free_roof_cpn, transverse_free_roof_cpn


def canopy_frame(g, left_mm=0., right_mm=0., pitch_deg=0., segments=2):
    """``frame(g)`` with cantilever rafter canopies off the eave nodes."""
    from main import Node, FrameElement2D
    s = frame(g)
    profile = SimpleNamespace(A=1200., I_analysis=8e6, name="Canopy rafter")
    eaves = {'left': min((n for n in s.nodes if n.y > 1), key=lambda n: n.x),
             'right': max((n for n in s.nodes if n.y > 1), key=lambda n: n.x)}
    for side, width in (('left', left_mm), ('right', right_mm)):
        if width <= 0:
            continue
        outward = -1 if side == 'left' else 1
        previous = eaves[side]
        for k in range(1, segments+1):
            run = width*k/segments
            node = Node(len(s.nodes)+1, eaves[side].x+outward*run, eaves[side].y-math.tan(math.radians(pitch_deg))*run)
            s.add_node(node)
            s.add_element(FrameElement2D(len(s.elements)+1, previous, node, 200000., profile,
                                         group=f'{side.upper()}_CANOPY_TOP'))
            previous = node
    return s


def inputs(**kwargs):
    return WindInputs(**{'building_width_m': 6., 'building_length_m': 16., 'eave_height_m': 3., 'roof_pitch_deg': 10.,
                         'orientation': 'Any', 'front_wall_clad': True, 'back_wall_clad': True,
                         'left_wall_clad': True, 'right_wall_clad': True, **kwargs})


def canopy_member_load(state, elements):
    """Total global vertical (+up) member load, from the equivalent end forces."""
    total = 0.
    for e in elements:
        local = np.asarray(state['member_equivalent_loads'].get(str(e.id), [0.]*6))
        global_ = e.transformation_matrix().T@local
        total += global_[1]+global_[4]
    return total


class CanopyLoadTests(unittest.TestCase):
    def test_canopy_gravity_follows_the_canopy_area_loads(self):
        g = geometry()
        s = canopy_frame(g, right_mm=3000.)
        macro = PortalReduction(s, [(x, g.roof_z(x)) for x in g.roof_stations])
        wind = inputs(right_canopy_length_m=3.)
        plain = {'loads': {'g_kpa': '.1', 'q_kpa': '.25'}}
        loaded = {**plain, 'canopies': {'right_g_kpa': '.15', 'right_q_kpa': '.25', 'right_solar_kpa': '.05'}}
        base, _ = building_roof_cases(end_wind_cases(wind), macro, g, wind, plain)
        with_canopy, _ = building_roof_cases(end_wind_cases(wind), macro, g, wind, loaded)
        case = lambda cases: next(c for c in cases if c['name'] == 'ULS / 1.2G + 1.5Q')
        widths = (4000., 8000., 4000.)
        for a, b, width in zip(case(base)['portal_loads'], case(with_canopy)['portal_loads'], widths):
            difference = sum(b['loads_n'][n.native_uy]-a['loads_n'][n.native_uy] for n in s.nodes)
            # Horizontal canopy plan 3 m x tributary width; G and solar take 1.2.
            self.assertAlmostEqual(difference, -(1.2*(.15+.05)+1.5*.25)*width/1000*3000., places=6)
        canopy_ids = {str(e.id) for e in s.elements if e.group == 'RIGHT_CANOPY_TOP'}
        top_ids = {str(e.id) for e in s.elements if e.group == 'TOP'}
        for state in case(with_canopy)['portal_loads']:
            self.assertTrue(set(state['member_equivalent_loads']) <= canopy_ids | top_ids)

    def test_end_wind_canopy_pressure_uses_roof_bands_and_open_underside(self):
        # Short building: one roof band (Cpe -1.3 at External max, h/d >= 1)
        # and the first side-wall band (-0.65) everywhere; areas < 10 m2 so Ka=1.
        g = geometry(length_mm=1000., bay_stations_mm=[0., 500., 1000.])
        s = canopy_frame(g, left_mm=2000.)
        macro = PortalReduction(s, [(x, g.roof_z(x)) for x in g.roof_stations])
        wind = inputs(building_length_m=1., left_canopy_length_m=2.)
        bases = end_wind_cases(wind)
        cases, _ = building_roof_cases(bases, macro, g, wind, {'loads': {}})
        chosen = next(c for c in cases if c['level'] == 'SLS' and c.get('g_factor') == 0.
                      and c.get('cpi_case') == 'Cpi +' and c.get('cpe_case') == 'External max'
                      and 'Front to back' in c['name'])
        base = next(c for c in bases if c['name'].startswith('SLS / Front to back / Cpi +'))
        q = base['wu_kpa']*base['wind_pressure_scale']
        factor = max(base['pressure_basis']['kce'], .8)
        # Underside: Table 5.1(B) single opening, Ka=1 x side-wall -0.65, Kci 0.8.
        expected_outward = q*(1.3*factor+(-.65)*.8)
        canopy = [e for e in s.elements if e.group == 'LEFT_CANOPY_TOP']
        for state, width in zip(chosen['portal_loads'], (250., 500., 250.)):
            # Flat canopy: local y is vertical; equivalent end shears sum to the load.
            self.assertAlmostEqual(canopy_member_load(state, canopy), expected_outward*width/1000*2000., places=6)

    def test_continuous_roof_side_wind_bands_run_tip_to_tip(self):
        g = geometry(span_mm=12000, pitch_deg=5)
        wind = calculate_wind(inputs(building_width_m=12., roof_pitch_deg=5.))
        plain = roof_cpe_segments(wind, g, False, True)
        self.assertEqual(roof_cpe_segments(wind, g, False, True, 0., 0.), plain)
        bands = roof_cpe_segments(wind, g, False, True, 3000., 2000.)
        self.assertAlmostEqual(bands[0][0], -3000.)
        self.assertAlmostEqual(bands[-1][1], 14000.)
        # Zones are measured from the windward canopy tip: the first band
        # matches the plain roof's first band, shifted by the canopy width.
        self.assertAlmostEqual(bands[0][1]+3000., plain[0][1])
        self.assertEqual(bands[0][2], plain[0][2])
        mirrored = roof_cpe_segments(wind, g, True, True, 3000., 2000.)
        self.assertAlmostEqual(mirrored[-1][1], 14000.)
        self.assertAlmostEqual(14000.-mirrored[-1][0], plain[0][1])
        # Pitched gable: upwind/downwind split at half the total slope length.
        steep = geometry(span_mm=12000, pitch_deg=15)
        s = canopy_frame(steep, left_mm=3000., right_mm=3000.)
        roof = [e for e in s.elements if e.group in ('TOP', 'LEFT_CANOPY_TOP', 'RIGHT_CANOPY_TOP')]
        self.assertAlmostEqual(half_slope_x(roof, 0.), 6000., places=6)
        wind15 = calculate_wind(inputs(building_width_m=12., roof_pitch_deg=15.))
        split = roof_cpe_segments(wind15, steep, False, False, 3000., 3000., 6000.)
        self.assertEqual([(a, b) for a, b, _ in split], [(-3000., 6000.), (6000., 15000.)])

    def test_canopy_parts_reads_widths_and_loads(self):
        g = geometry()
        s = canopy_frame(g, left_mm=2500., right_mm=4000., pitch_deg=5.)
        parts = {p['side']: p for p in canopy_parts(s, {'canopies': {'left_g_kpa': '0.2', 'right_underside': 'Blocked under'}})}
        self.assertAlmostEqual(parts['left']['plan_mm'], 2500.)
        self.assertAlmostEqual(parts['right']['plan_mm'], 4000.)
        self.assertAlmostEqual(parts['left']['loads'].g, -.2)
        self.assertEqual(parts['right']['underside'], 'blocked')
        self.assertEqual(canopy_parts(frame(g), {}), [])


class FrictionAndOpenEndTests(unittest.TestCase):
    def test_friction_surfaces(self):
        closed = roof_friction_drag(inputs(building_length_m=40.), 1., 4.)
        self.assertNotIn('surfaces', closed)
        self.assertNotIn('canopies', closed)
        self.assertAlmostEqual(closed['signed_y_force_kn'], .04*6*(40-16))
        one_open = roof_friction_drag(inputs(building_length_m=40., front_wall_clad=False), 1., 4.)
        self.assertAlmostEqual(one_open['signed_y_force_kn'], closed['signed_y_force_kn'])
        open_ends = roof_friction_drag(inputs(building_length_m=40., front_wall_clad=False, back_wall_clad=False), 1., 4.)
        self.assertEqual(open_ends['surfaces'], ['top', 'underside'])
        self.assertAlmostEqual(open_ends['signed_y_force_kn'], 2*closed['signed_y_force_kn'])
        self.assertAlmostEqual(open_ends['signed_y_pressure_kpa'], 2*closed['signed_y_pressure_kpa'])
        canopy = roof_friction_drag(inputs(building_length_m=40., right_canopy_length_m=3.), 1., 4., reverse=True)
        (entry,) = canopy['canopies']
        self.assertEqual((entry['side'], entry['surfaces']), ('right', ['top', 'underside']))
        self.assertAlmostEqual(entry['signed_y_force_kn'], -2*.04*3*(40-16))
        self.assertAlmostEqual(canopy['signed_y_force_kn'], -closed['signed_y_force_kn'])

    def test_open_ended_buildings_use_friction_as_the_longitudinal_load(self):
        open_ends = inputs(building_length_m=40., front_wall_clad=False, back_wall_clad=False)
        cases = end_wind_cases(open_ends)
        self.assertTrue(all(c['front_kpa'] == 0 and c['back_kpa'] == 0 for c in cases))
        self.assertTrue(all(abs(c['roof_drag']['signed_y_force_kn']) > 0 for c in cases))
        with self.assertRaisesRegex(AnalysisError, 'too short for roof friction'):
            end_wind_cases(replace(open_ends, building_length_m=10.))

    def test_fully_open_structure_uses_free_roof_coefficients(self):
        g = geometry(length_mm=40000., bay_stations_mm=[0., 8000., 16000., 24000., 32000., 40000.])
        s = frame(g)
        macro = PortalReduction(s, [(x, g.roof_z(x)) for x in g.roof_stations])
        wind = inputs(building_length_m=40., front_wall_clad=False, back_wall_clad=False,
                      left_wall_clad=False, right_wall_clad=False)
        bases = end_wind_cases(wind)
        cases, _ = building_roof_cases(bases, macro, g, wind, {'loads': {}})
        base = next(c for c in bases if c['name'].startswith('SLS / Front to back / Cpi +'))
        q = base['wu_kpa']*base['wind_pressure_scale']
        for branch, uplift in (('External max', True), ('External min', False)):
            case = next(c for c in cases if c['name'] == f"{base['name']} / {branch} / W")
            cpn = transverse_free_roof_cpn('empty', uplift)
            for state, width in zip(case['portal_loads'], (4000., 8000., 8000., 8000., 8000., 4000.)):
                vertical = sum(state['loads_n'][n.native_uy] for n in s.nodes)
                # Normal pressure on both slopes: vertical resultant = net x span.
                self.assertAlmostEqual(vertical, -cpn*q*width/1000*6000., places=5)
        side = [c for c in cases if c.get('wind_family') == 'transverse' and c['level'] == 'SLS'
                and c['name'].endswith('/ Ws') and c['cpe_case'] == 'Left +']
        self.assertTrue(side)
        self.assertTrue(all(c['cpi'] == 0. for c in side))
        h_over_d = calculate_wind(replace(wind, left_canopy_length_m=0., right_canopy_length_m=0.)).h_over_d
        windward = free_roof_cpn(10., h_over_d, 'windward', 'empty', True)
        leeward = free_roof_cpn(10., h_over_d, 'leeward', 'empty', True)
        case = side[0]
        q = case['wu_kpa']*case['wind_pressure_scale']
        drag = case['transverse_roof_drag']
        # 4*min(h, L) exceeds the 6 m span, so there is no transverse friction.
        self.assertEqual(drag['x_start_mm'], drag['x_end_mm'])
        for state, width in zip(case['portal_loads'], (4000., 8000., 8000., 8000., 8000., 4000.)):
            vertical = sum(state['loads_n'][n.native_uy] for n in s.nodes)
            # Windward and leeward halves of the gable, net Cpn (B.5, empty
            # under), no Cpi: vertical resultant = -(Cpn_w + Cpn_l) q width span/2.
            self.assertAlmostEqual(vertical, -(windward+leeward)*q*width/1000*3000., places=5)


class CanopyMatrixTests(unittest.TestCase):
    def test_loaded_canopy_frames_match_the_uncondensed_matrix(self):
        g = geometry(end_wall_stations_mm=[0, 2000, 4000, 6000])
        layout = fixed_layout(g, end=True)
        s = canopy_frame(g, left_mm=2500., right_mm=3000., pitch_deg=5.)
        macro = PortalReduction(s, [(x, g.roof_z(x)) for x in g.roof_stations])
        model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
        states = []
        for i in range(len(roof)):
            s.clear_loads()
            factor = .5 if i in (0, len(roof)-1) else 1.
            for e in s.elements:
                if e.group == 'TOP':
                    s.add_uniform_global_line_load(e, fy_n_per_mm=-.8*factor)
                elif 'CANOPY' in e.group:
                    s.add_uniform_global_line_load(e, fx_n_per_mm=.1*factor, fy_n_per_mm=.6*factor)
            states.append(_snapshot(s))
        case = {'name': 'Loaded canopy frames', 'front_kpa': 1.1, 'back_kpa': .3, 'portal_loads': states}
        result = solve_building_case(model, layout, macro, dofs, lookup, case)
        network, _ = end_wall_load_vector(model, layout, lookup, 1.1, .3)
        nf = len(macro.free); npf = len(roof); ny = sum(map(len, roof)); size = npf*nf+ny
        transform = np.zeros((3*len(model.nodes), size)); full = np.zeros((size, size)); rhs = np.zeros(size)
        iy = npf*nf
        for i, nodes in enumerate(roof):
            full[i*nf:(i+1)*nf, i*nf:(i+1)*nf] = macro.k[np.ix_(macro.free, macro.free)]
            rhs[i*nf:(i+1)*nf] = np.asarray(states[i]['loads_n'])[macro.free]
            for j, node in enumerate(nodes):
                transform[3*node, i*nf:(i+1)*nf] = macro.h[2*j, macro.free]
                transform[3*node+2, i*nf:(i+1)*nf] = macro.h[2*j+1, macro.free]
                transform[3*node+1, iy] = 1.; iy += 1
        for member, response in zip(model.members, result['members']):
            if member.tension_only and response['state'] == 'slack':
                continue
            delta = np.subtract(model.nodes[member.end], model.nodes[member.start]); length = np.linalg.norm(delta)
            row = delta/length@(transform[3*member.end:3*member.end+3]-transform[3*member.start:3*member.start+3])
            full += member.modulus_mpa*member.area_mm2/length*np.outer(row, row)
        original = np.linalg.solve(full, rhs+transform.T@network)
        np.testing.assert_allclose(transform@original, np.asarray(result['displacements_mm']).reshape(-1), atol=1e-7)
        for i, recovered in enumerate(result['portals']):
            full_d = np.zeros(len(macro.k)); full_d[macro.free] = original[i*nf:(i+1)*nf]
            for e, output in zip(s.elements, recovered['member_end_forces']):
                expected = e.local_end_forces(full_d)-np.asarray(states[i]['member_equivalent_loads'].get(str(e.id), np.zeros(6)))
                np.testing.assert_allclose(output['local_n_n_nmm'], expected, atol=1e-5)
        self.assertLess(max(map(abs, result['balance_force_error_n'])), 1e-5)
        self.assertLess(max(map(abs, result['balance_moment_error_nmm'])), .01)


def canopy_geometry(left_mm=2500., right_mm=3000., pitch_deg=5., **kwargs):
    drop = lambda width: math.tan(math.radians(pitch_deg))*width
    return geometry(canopies=[{'side': 'left', 'width_mm': left_mm, 'drop_mm': drop(left_mm)},
                              {'side': 'right', 'width_mm': right_mm, 'drop_mm': drop(right_mm)}], **kwargs)


def canopy_portal(g, pitch_deg=5.):
    """Synthetic canopy portal condensed onto the roof stations and canopy edges."""
    s = canopy_frame(g, left_mm=g.canopy('left')[0], right_mm=g.canopy('right')[0], pitch_deg=pitch_deg)
    points = [(x, g.roof_z(x)) for x in g.roof_stations] + [g.canopy_tip(side) for side, _, _ in g.canopies]
    return s, PortalReduction(s, points)


class CanopyLayoutTests(unittest.TestCase):
    def test_geometry_round_trips_and_plain_layouts_are_unchanged(self):
        from bracing_layout import BracingLayout, LayoutGeometry, SCHEMA, SCHEMA_V2
        g = canopy_geometry()
        self.assertEqual(LayoutGeometry.from_dict(g.to_dict()), g)
        layout = BracingLayout(g, pinned=['canopy_left/bay/0'])
        self.assertEqual(layout.to_dict()['schema'], SCHEMA_V2)
        self.assertEqual(BracingLayout.from_dict(layout.to_dict()).pinned, {'canopy_left/bay/0'})
        plain = geometry()
        self.assertNotIn('canopies', plain.to_dict())
        self.assertEqual(BracingLayout(plain).to_dict()['schema'], SCHEMA)
        self.assertNotEqual(plain, g)
        self.assertEqual(plain, g.main_geometry())
        with self.assertRaisesRegex(ValueError, 'gable roofs only'):
            geometry(roof_shape='Skillion', canopies=[{'side': 'left', 'width_mm': 2000., 'drop_mm': 100.}])

    def test_canopy_panels_and_strut_lines(self):
        g = canopy_geometry()
        layout = __import__('bracing_layout').BracingLayout(g)
        bays = len(g.bay_stations_mm)-1
        self.assertEqual(sum(p.startswith('canopy_left/') for p in layout.panels), bays)
        panel = layout.panels['canopy_right/bay/1']
        tip_x, tip_z = g.canopy_tip('right')
        self.assertAlmostEqual(tip_x, 6000.+3000.)
        self.assertAlmostEqual(tip_z, 3000.-math.tan(math.radians(5.))*3000.)
        self.assertEqual(panel.vertices[0], (6000., g.bay_stations_mm[1], g.roof_z(6000.)))
        self.assertEqual(panel.vertices[1], (tip_x, g.bay_stations_mm[1], tip_z))
        lines = g.strut_lines
        self.assertEqual(len(lines), len(g.roof_stations)+2)
        self.assertEqual([side for _, _, side in lines[-2:]], ['left', 'right'])
        self.assertEqual((g.canopy_line('left'), g.canopy_line('right')), (len(g.roof_stations), len(g.roof_stations)+1))

    def test_saved_main_panels_carry_over_when_only_canopies_change(self):
        from bracing_layout import BracingLayout, carry_over
        plain = geometry()
        saved = BracingLayout(plain, blocked=['left_wall/bay/1'], pinned=['roof/bay/0/strip/0', 'left_wall/bay/0'])
        moved = carry_over(saved, canopy_geometry())
        self.assertEqual(moved.pinned, saved.pinned)
        self.assertEqual(moved.blocked, saved.blocked)
        with_canopy = BracingLayout(canopy_geometry(), pinned=['canopy_left/bay/0', 'left_wall/bay/0'])
        self.assertEqual(carry_over(with_canopy, plain).pinned, {'left_wall/bay/0'})
        self.assertIsNone(carry_over(saved, geometry(span_mm=7000)))


class CanopyGroupTests(unittest.TestCase):
    def test_policy_and_group_bookkeeping(self):
        from bracing_layout import BracingLayout, brace_group
        from bracing_policy import evidence, group_faces, rod_faces, selection_score
        from bracing_design import active_groups, selected_key
        self.assertEqual(brace_group('canopy_left'), 'canopy_brace')
        self.assertEqual(brace_group('roof_left'), 'roof_brace')
        layout = BracingLayout(canopy_geometry(), pinned=['canopy_left/bay/0', 'left_wall/bay/0'])
        context = {'layout': layout, 'face_cladding': {'left_wall': False}, 'brace_policy': {'project_use': 'agricultural'}}
        self.assertEqual(group_faces(context, 'canopy_brace'), ['canopy_left'])
        self.assertEqual(rod_faces(context, 'canopy_brace'), [])          # a canopy roof is not an unclad wall
        self.assertEqual(rod_faces(context, 'wall_brace'), ['left_wall'])
        wire, rod = {'component_type': 'wire_rope', 'label': '8 mm WIRE ROPE'}, {'component_type': 'scheduled_rod', 'label': '12 DIA ROD'}
        plain = {'roof_brace': rod, 'wall_brace': rod, 'end_brace': rod}
        self.assertEqual(active_groups(plain), ['roof_brace', 'wall_brace', 'end_brace'])
        self.assertEqual(selected_key(plain), ('12 DIA ROD',)*3)
        with_canopy = {**plain, 'canopy_brace': wire}
        self.assertEqual(selected_key(with_canopy)[-1], '8 mm WIRE ROPE')
        result = {'design': {'estimated_cost': 1.}}
        # Agricultural use prefers wire: the canopy rod costs one preference step.
        self.assertEqual(selection_score(context, {**plain, 'canopy_brace': rod}, result)[0]
                         - selection_score(context, with_canopy, result)[0], 1)
        self.assertIn('canopy_brace', evidence(context, with_canopy)['groups'])
        self.assertNotIn('canopy_brace', evidence(context, plain)['groups'])

    def test_canopy_strut_lines_are_named(self):
        from bracing_sizing_diagnostics import describe_location
        g = {'span_mm': 6000.}
        self.assertEqual(describe_location({'x_mm': -2500., 'bay': 0, 'line': 4}, g), 'Bay 1, left canopy edge purlin')
        self.assertEqual(describe_location({'x_mm': 9000., 'bay': 1, 'line': 5}, g), 'Bay 2, right canopy edge purlin')
        self.assertEqual(describe_location({'x_mm': 0., 'bay': 0, 'line': 0}, g), 'Bay 1, left eave')


class CanopyBracingRuleTests(unittest.TestCase):
    def setUp(self):
        self.g = canopy_geometry()   # 2 bays, gable: roof strips 0..1
        self.full = {'roof/bay/0/strip/0', 'roof/bay/0/strip/1'}

    def test_follows_complete_roof_bays_else_any_roof_bay(self):
        from bracing_layout import canopy_bracing
        self.assertEqual(canopy_bracing(self.g, set(), self.full | {'roof/bay/1/strip/0'}),
                         {'canopy_left/bay/0', 'canopy_right/bay/0'})
        self.assertEqual(canopy_bracing(self.g, set(), {'roof/bay/1/strip/0'}),
                         {'canopy_left/bay/1', 'canopy_right/bay/1'})
        self.assertEqual(canopy_bracing(self.g, set(), set()), set())
        self.assertEqual(canopy_bracing(geometry(), set(), self.full), set())

    def test_full_roof_braces_canopy_ends_and_spacing_bays_only(self):
        from bracing_layout import canopy_bays, canopy_bracing
        g = canopy_geometry(length_mm=63000., bay_mm=9000.)   # 7 bays
        strips = len(g.roof_stations)-1
        full_roof = {f'roof/bay/{b}/strip/{j}' for b in range(7) for j in range(strips)}
        added = canopy_bracing(g, set(), full_roof)
        self.assertEqual(added, {f'canopy_{s}/bay/{b}' for s in ('left', 'right') for b in (0, 3, 6)})
        # Spacing 6: no run of 6 unbraced bays between the ends, so the ends only.
        self.assertEqual(canopy_bracing(g, set(), full_roof, max_spacing=6),
                         {f'canopy_{s}/bay/{b}' for s in ('left', 'right') for b in (0, 6)})
        self.assertEqual(canopy_bays([0, 1, 2], 5), [0, 2])
        self.assertEqual(canopy_bays([0, 10, 20], 5), [0, 10, 20])
        self.assertEqual(canopy_bays([0, 20], 5), [0, 20])   # nothing in the gap to add
        # A gap with no roof-braced bay inside does not stop later gaps being split.
        # [0, 13] -> split at 6; gap 0-6 has no bay inside, gap 6-13 still gets 7.
        self.assertEqual(canopy_bays([0, 6, 7, 13], 5), [0, 6, 7, 13])

    def test_blocked_panels_move_and_braced_sides_are_kept(self):
        from bracing_layout import canopy_bracing
        self.assertEqual(canopy_bracing(self.g, {'canopy_left/bay/0'}, self.full),
                         {'canopy_left/bay/1', 'canopy_right/bay/0'})
        self.assertEqual(canopy_bracing(self.g, set(), self.full | {'canopy_left/bay/1'}), {'canopy_right/bay/0'})

    def test_search_layouts_include_canopy_bracing(self):
        from bracing_layout import BracingLayout
        from bracing_layout_search import layout_context
        import combined_sizing
        context = {'layout': BracingLayout(self.g), 'source_preset': {}}
        local = layout_context(context, self.full)
        self.assertTrue({'canopy_left/bay/0', 'canopy_right/bay/0'} <= local['layout'].pinned)
        self.assertIn('canopy_right/bay/0', local['nominal_layout']['pinned_panels'])
        self.assertTrue({'canopy_left/bay/0'} <= combined_sizing._with_layout(context, self.full)['layout'].pinned)


class CanopyNetworkTests(unittest.TestCase):
    def setUp(self):
        self.g = canopy_geometry(end_wall_stations_mm=[0, 2000, 4000, 6000])
        self.s, self.macro = canopy_portal(self.g)
        self.main = {p for p in fixed_layout(self.g.main_geometry(), end=True).pinned}
        last = len(self.g.bay_stations_mm)-2
        self.canopy = {f'canopy_{side}/bay/{b}' for side in ('left', 'right') for b in (0, last)}
        self.drag = {'y_start_m': 2., 'y_end_m': 16., 'signed_y_pressure_kpa': .05,
                     'canopies': [{'side': 'left', 'y_start_m': 2., 'y_end_m': 16., 'signed_y_pressure_kpa': .1},
                                  {'side': 'right', 'y_start_m': 2., 'y_end_m': 16., 'signed_y_pressure_kpa': .1}]}

    def build(self, pinned):
        from bracing_layout import BracingLayout
        layout = BracingLayout(self.g, pinned=sorted(pinned))
        return (layout,)+build_braced_building(layout, self.macro, 1500, 200, 200, 200, 1500, canopy_brace_area_mm2=150)

    def test_canopy_edges_struts_and_braces(self):
        layout, model, roof, dofs, lookup = self.build(self.main | self.canopy)
        n = len(self.g.roof_stations)
        self.assertTrue(all(len(nodes) == n+2 for nodes in roof))
        ids = {m.id: m for m in model.members}
        self.assertIn(f'strut/bay/0/line/{n}', ids)
        self.assertIn(f'strut/bay/0/line/{n+1}', ids)
        self.assertEqual(sum(m.kind == 'canopy_brace' for m in model.members), 2*len(self.canopy))
        self.assertTrue(all(m.area_mm2 == 150 for m in model.members if m.kind == 'canopy_brace'))
        with self.assertRaisesRegex(AnalysisError, 'canopy brace section'):
            from bracing_layout import BracingLayout
            build_braced_building(BracingLayout(self.g, pinned=sorted(self.canopy)), self.macro, 1500, 200, 200, 200, 1500)

    def test_unbraced_canopy_edges_are_a_mechanism(self):
        layout, model, roof, dofs, lookup = self.build(self.main)
        with self.assertRaisesRegex(AnalysisError, 'Unstable'):
            solve_building_case(model, layout, self.macro, dofs, lookup,
                                {'front_kpa': 0., 'back_kpa': 0., 'roof_drag': self.drag})

    def test_canopy_friction_reaches_the_bracing_and_the_full_matrix_agrees(self):
        layout, model, roof, dofs, lookup = self.build(self.main | self.canopy)
        case = {'name': 'Canopy friction', 'front_kpa': .4, 'back_kpa': .2, 'roof_drag': self.drag}
        result = solve_building_case(model, layout, self.macro, dofs, lookup, case)
        workings = {w['face']: w for w in result['load_workings']}
        for side in ('left', 'right'):
            width = self.g.canopy(side)[0]
            self.assertAlmostEqual(workings[f'{side}_canopy_friction']['roof_resultant_n'], .1/1000*width*14000., places=6)
        self.assertLess(abs(result['load_path']['roof_to_member_base_y_residual_n']), 1e-6)
        self.assertLess(max(map(abs, result['balance_force_error_n'])), 1e-5)
        canopy_braces = [m for m in result['members'] if m['kind'] == 'canopy_brace']
        self.assertGreater(max(m['axial_n'] for m in canopy_braces), 1.)
        # Uncondensed check: portal K at every frame plus the 3D members.
        from bracing_analysis import roof_drag_load_vector
        network, _ = end_wall_load_vector(model, layout, lookup, .4, .2)
        network = network+roof_drag_load_vector(model, layout, lookup, self.drag)[0]
        macro = self.macro
        nf = len(macro.free); npf = len(roof); ny = sum(map(len, roof)); size = npf*nf+ny
        transform = np.zeros((3*len(model.nodes), size)); full = np.zeros((size, size))
        iy = npf*nf
        for i, nodes in enumerate(roof):
            full[i*nf:(i+1)*nf, i*nf:(i+1)*nf] = macro.k[np.ix_(macro.free, macro.free)]
            for j, node in enumerate(nodes):
                transform[3*node, i*nf:(i+1)*nf] = macro.h[2*j, macro.free]
                transform[3*node+2, i*nf:(i+1)*nf] = macro.h[2*j+1, macro.free]
                transform[3*node+1, iy] = 1.; iy += 1
        for member, response in zip(model.members, result['members']):
            if member.tension_only and response['state'] == 'slack':
                continue
            delta = np.subtract(model.nodes[member.end], model.nodes[member.start]); length = np.linalg.norm(delta)
            row = delta/length@(transform[3*member.end:3*member.end+3]-transform[3*member.start:3*member.start+3])
            full += member.modulus_mpa*member.area_mm2/length*np.outer(row, row)
        original = np.linalg.solve(full, transform.T@network)
        np.testing.assert_allclose(transform@original, np.asarray(result['displacements_mm']).reshape(-1), atol=1e-7)


class CanopyDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import tempfile
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_3d_canopy_")
        os.environ["APPDATA"] = cls.temp.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.defaults = copy.deepcopy(cls.window.collect_preset_data())

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.temp.cleanup()

    def with_canopy(self, side='right', width='4', kind='Canopy'):
        w = self.window
        w.apply_preset_data(copy.deepcopy(self.defaults))
        getattr(w, f'{side}_canopy_type_combo').setCurrentText(kind)
        getattr(w, f'{side}_canopy_length_input').setText(width)
        w.update_canopy_depth_from_geometry(side)
        w.generate_and_solve()
        return w

    def test_canopy_context_and_presets(self):
        from bracing_analysis_desktop import prepare_context
        plain = copy.deepcopy(self.defaults)
        self.assertNotIn('right_purlin_section', plain['canopies'])
        w = self.with_canopy()
        check = w.canopy_purlin_checks['right']
        self.assertEqual(check['match']['status'], 'OK')
        self.assertEqual(check['spacing_source'], 'Main roof purlin spacing')
        preset = w.collect_preset_data()['canopies']
        self.assertEqual((preset['right_purlin_section'], preset['right_purlin_spacing_mm']), ('Auto (lightest passing)', ''))
        context = prepare_context(w, allow_unconfigured=True)
        g = context['layout'].geometry
        (side, width, drop), = g.canopies
        self.assertEqual(side, 'right')
        self.assertAlmostEqual(width, 4000., delta=1.)
        self.assertEqual(len(context['portal'].points), len(g.roof_stations)+1)
        line = str(g.canopy_line('right'))
        self.assertEqual(context['installed_purlins'][line]['canopy_side'], 'right')
        self.assertIn('canopy_brace', context)
        # End-wind cases carry the canopy's top-and-underside friction.
        self.assertTrue(any(c.get('roof_drag', {}).get('canopies') for c in context['cases']))

    def test_canopy_braces_are_their_own_sized_checked_and_costed_group(self):
        import combined_sizing as shared
        from bracing_analysis_desktop import prepare_context
        from bracing_design import auto_size, solve_selection
        w = self.with_canopy()
        context = prepare_context(w, allow_unconfigured=True)
        dialog = shared._workspace(w, context)
        try:
            self.assertIn('canopy_brace', dialog.brace_combos)
            self.assertIn('canopy_brace_label', dialog.snapshot())
            start = dialog.selections(context)
        finally:
            dialog.deleteLater()
        self.assertIn('canopy_brace', start)
        candidates = shared._candidates(w, context)
        self.assertIn('canopy_brace', candidates)
        g = context['layout'].geometry
        last = len(g.bay_stations_mm)-2
        pinned = {p for p in context['layout'].panels if p.startswith(('roof/bay/0/', 'left_wall/bay/0', 'right_wall/bay/0',
                                                                           f'roof/bay/{last}/', f'left_wall/bay/{last}', f'right_wall/bay/{last}'))}
        pinned |= {f'canopy_right/bay/{b}' for b in (0, last)}
        local = shared._with_layout(context, pinned)
        criteria = shared._criteria(w, context)
        checked = solve_selection(local, start, criteria, context['cost_rates'])
        canopy_checks = [k for k in checked['section_checks'] if k.startswith('brace/canopy_right/')]
        self.assertEqual(len(canopy_checks), 4)   # two panels x two diagonals
        cost = next(c for c in checked['design']['cost_items'] if c['group'] == 'canopy_brace')
        self.assertEqual(cost['member_count'], 4)
        self.assertGreater(cost['total'], 0.)
        installation = next(c for c in checked['design']['cost_items'] if c['group'] == 'panel_installation')
        self.assertEqual(installation['panel_count'], len(pinned))
        sized, chosen = auto_size(local, start, candidates, criteria, context['cost_rates'], fast=True)
        self.assertTrue(sized['design']['passes'])
        self.assertIn('canopy_brace', chosen)
        # The canopy edge purlin line is checked as a strut in every bay.
        line = g.canopy_line('right')
        self.assertEqual(sum(k.startswith('strut/') and k.endswith(f'/line/{line}') for k in sized['section_checks']), last+1)

    def test_find_layout_braces_the_canopy_and_fixed_layouts_need_it(self):
        import combined_sizing as shared
        from bracing_analysis_desktop import prepare_context
        from bracing_design import solve_selection
        from bracing_layout_search import find_layout
        w = self.with_canopy()
        context = prepare_context(w, allow_unconfigured=True)
        candidates = shared._candidates(w, context)
        start = shared._start_selections(w, context, candidates)
        criteria = shared._criteria(w, context)
        result, chosen, layout = find_layout(shared._with_layout(context, ()), start, candidates, criteria, context['cost_rates'])
        self.assertTrue(result['design']['passes'])
        canopy = {p for p in layout.pinned if p.startswith('canopy_right/')}
        self.assertTrue(canopy)
        roof_bays = {int(p.split('/')[2]) for p in layout.pinned if p.startswith('roof/')}
        self.assertTrue({int(p.split('/')[2]) for p in canopy} <= roof_bays)
        self.assertIn('canopy_brace', chosen)
        # A user's fixed layout without canopy bracing gets a clear message.
        main_only = {p for p in layout.pinned if not p.startswith('canopy_')}
        local = {**context, 'layout': __import__('bracing_layout').BracingLayout(context['layout'].geometry, (), sorted(main_only))}
        with self.assertRaisesRegex(AnalysisError, 'right canopy has no roof bracing'):
            solve_selection(local, chosen, criteria, context['cost_rates'])

    def test_layout_editor_shows_canopy_faces_and_keeps_saved_panels(self):
        from bracing_layout import BracingLayout
        from bracing_layout_ui import BracingLayoutDialog
        w = self.with_canopy()
        geometry = w.current_bracing_layout_geometry()
        saved = BracingLayout(geometry.main_geometry(), pinned=['left_wall/bay/0']).to_dict()
        dialog = BracingLayoutDialog(geometry, saved)
        try:
            self.assertEqual(dialog.model.geometry, geometry)
            self.assertEqual(dialog.model.pinned, {'left_wall/bay/0'})
            faces = [dialog.face_filter.itemData(i) for i in range(dialog.face_filter.count())]
            self.assertIn('canopy_right', faces)
            dialog.resize(900, 600)
            self.assertFalse(dialog.canvas.grab().isNull())   # paints canopy faces and labels
            self.assertTrue(any(identifier.startswith('canopy_right/') for _, identifier, _ in dialog.canvas.hit_panels))
        finally:
            dialog.deleteLater()

    def test_buildings_without_canopies_have_no_canopy_group(self):
        import combined_sizing as shared
        from bracing_analysis_desktop import prepare_context
        w = self.window
        w.apply_preset_data(copy.deepcopy(self.defaults))
        w.generate_and_solve()
        context = prepare_context(w, allow_unconfigured=True)
        self.assertNotIn('canopy_brace', context)
        self.assertNotIn('canopy_brace', shared._candidates(w, context))
        self.assertNotIn('canopy_brace', shared._start_selections(w, context, None))

    def test_guards(self):
        from bracing_analysis_desktop import prepare_context
        w = self.with_canopy(kind='Lean-to')
        with self.assertRaisesRegex(AnalysisError, 'lean-tos'):
            prepare_context(w, allow_unconfigured=True)
        w = self.with_canopy()
        w.right_canopy_eave_height_input.setText(str(float(w.eave_input.text())-1))
        w.update_canopy_depth_from_geometry('right')
        w.generate_and_solve()
        with self.assertRaisesRegex(AnalysisError, 'attach at the main eave'):
            prepare_context(w, allow_unconfigured=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
