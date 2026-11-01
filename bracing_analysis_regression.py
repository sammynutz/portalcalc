"""Independent mechanics, condensation/recovery and offscreen desktop checks."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from bracing_analysis import (AnalysisError, AxialMember, AxialModel3D, PortalReduction,
                              build_braced_building, end_wall_load_vector, roof_drag_load_vector, solve_building_case)
from bracing_layout import BracingLayout, LayoutGeometry


def geometry(**kwargs):
    return LayoutGeometry.create(**{"span_mm": 6000, "length_mm": 16000, "eave_mm": 3000,
                                    "pitch_deg": 10, "roof_shape": "Gable", **kwargs})


def frame(g, fixed=False):
    from main import Node, FrameElement2D, Structure2D
    s = Structure2D()
    profile = SimpleNamespace(A=1500., I_analysis=12e6, name="Benchmark section")
    points = [(0., 0.)] + [(x, g.roof_z(x)) for x in g.roof_stations] + [(g.span_mm, 0.)]
    for i, (x, z) in enumerate(points):
        s.add_node(Node(i+1, x, z))
    for i, (a, b) in enumerate(zip(s.nodes, s.nodes[1:])):
        s.add_element(FrameElement2D(i+1, a, b, 200000., profile, group="TOP" if 0 < i < len(points)-2 else "COLUMN"))
    for n in (s.nodes[0], s.nodes[-1]):
        s.add_support(n.ux)
        s.add_support(n.uy)
        if fixed:
            s.add_support(n.rz)
    return s


def fixed_layout(g, end=False):
    layout = BracingLayout(g)
    for p in layout.panels.values():
        if p.id.startswith(("left_wall/bay/0", "right_wall/bay/0", "roof/bay/0/")) or (end and p.face == "front_end"):
            layout.toggle(p.id, "pinned")
    return layout


class MechanicsTests(unittest.TestCase):
    def test_simultaneous_end_wall_and_internal_pressures_resolve_in_building_axes(self):
        from bracing_analysis_desktop import end_wind_cases
        from wind import WindInputs
        # Uniform internal pressure has zero longitudinal resultant on equal
        # opposing clad ends. Its local effects on each end remain present.
        for open_side in (False,True):
            inputs=WindInputs(building_width_m=30.,building_length_m=40.,orientation='Any',
                              left_wall_clad=True,right_wall_clad=not open_side,
                              front_wall_clad=True,back_wall_clad=True)
            for i,case in enumerate(end_wind_cases(inputs)):
                ww,lw=case['components_windward_leeward']
                reverse=i>=4
                front_internal=ww['internal_kpa'] if not reverse else lw['internal_kpa']
                back_internal=lw['internal_kpa'] if not reverse else ww['internal_kpa']
                self.assertAlmostEqual(front_internal,back_internal)
                expected_external=(ww['external_kpa']-lw['external_kpa'])*(-1 if reverse else 1)
                self.assertAlmostEqual(case['front_kpa']+case['back_kpa'],expected_external)
                front_external=ww['external_kpa'] if not reverse else lw['external_kpa']
                back_external=lw['external_kpa'] if not reverse else ww['external_kpa']
                self.assertAlmostEqual(case['front_kpa'],front_external-front_internal)
                self.assertAlmostEqual(case['back_kpa'],-back_external+back_internal)
        # With one clad end, internal pressure cannot cancel against an absent wall.
        one=WindInputs(orientation='Any',front_wall_clad=True,back_wall_clad=False)
        for case in end_wind_cases(one):
            self.assertEqual(case['back_kpa'],0.)
            self.assertNotEqual(case['front_kpa'],0.)

    def test_roof_friction_threshold_profiles_pressure_scale_and_open_side_basis(self):
        from bracing_analysis_desktop import end_wind_cases,roof_friction_drag
        from wind import WindInputs
        from dataclasses import replace
        inputs=WindInputs(building_width_m=30.,building_length_m=40.,eave_height_m=6.,roof_pitch_deg=0.,
                          front_wall_clad=True,back_wall_clad=True,left_wall_clad=True,right_wall_clad=True)
        drag=roof_friction_drag(inputs,.8,6.)
        self.assertEqual(drag['windward_exclusion_m'],24.)
        self.assertEqual(drag['projected_area_m2'],480.)
        self.assertAlmostEqual(drag['signed_y_force_kn'],15.36)
        self.assertFalse(drag['preliminary_open_sided'])
        self.assertAlmostEqual(roof_friction_drag(inputs,.8,6.,roof_profile='Corry (Corodek)')['signed_y_force_kn'],7.68)
        for depth in (20.,24.):
            self.assertEqual(roof_friction_drag(replace(inputs,building_length_m=depth),.8,6.)['projected_area_m2'],0.)
        # Narrow tall building: breadth controls, even though d/h <= 4.
        narrow=replace(inputs,building_width_m=5.,building_length_m=24.,eave_height_m=10.)
        self.assertEqual(roof_friction_drag(narrow,.8,10.)['projected_area_m2'],20.)
        opened=replace(inputs,right_wall_clad=False)
        self.assertTrue(roof_friction_drag(opened,.8,6.)['preliminary_open_sided'])
        with self.assertRaises(AnalysisError):roof_friction_drag(inputs,.8,6.,roof_profile='Unknown')
        cases=end_wind_cases(opened)
        for c in cases:
            d=c['roof_drag']
            # No end-wall area/action-combination or internal-pressure factor.
            self.assertAlmostEqual(abs(d['signed_y_force_kn']),c['wu_kpa']*c['wind_pressure_scale']*.04*480.)
        first=cases[0]['roof_drag'];back=cases[4]['roof_drag']
        self.assertEqual((first['y_start_m'],first['y_end_m']),(24.,40.))
        self.assertEqual((back['y_start_m'],back['y_end_m']),(0.,16.))
        self.assertLess(back['signed_y_force_kn'],0.)

    def test_roof_drag_distribution_preserves_force_moments_and_mirrors_strip(self):
        # Cutoff crosses an irregular bay; external force all enters the roof.
        for shape in ('Gable','Skillion'):
            g=geometry(roof_shape=shape,bay_stations_mm=[0.,5000.,11000.,16000.])
            layout=fixed_layout(g)
            macro=PortalReduction(frame(g),[(x,g.roof_z(x)) for x in g.roof_stations])
            model,_,dofs,lookup=build_braced_building(layout,macro,1500,300,300,300,1500)
            for reverse in (False,True):
                lo,hi=(0.,6.5) if reverse else (9.5,16.)
                p=-.032 if reverse else .032
                drag={'y_start_m':lo,'y_end_m':hi,'signed_y_pressure_kpa':p}
                f,work=roof_drag_load_vector(model,layout,lookup,drag)
                amount=p*g.span_mm*(hi-lo)
                self.assertAlmostEqual(f[1::3].sum(),amount,places=7)
                self.assertEqual(work[0]['direct_ground_resultant_n'],0.)
                self.assertTrue(all(np.linalg.norm(f[3*i:3*i+3])==0 for i,n in enumerate(model.nodes) if n[2]==0.))
                roof_integral=sum((b-a)*(g.roof_z(a)+g.roof_z(b))/2 for a,b in zip(g.roof_stations,g.roof_stations[1:]))
                moments=np.cross(np.asarray(model.nodes),f.reshape(-1,3)).sum(axis=0)
                np.testing.assert_allclose(moments,[-p*(hi-lo)*roof_integral,0.,amount*g.span_mm/2],atol=1e-6)
                # Spatial distribution also retains the loaded strip's Y centre.
                centre=sum(n[1]*f[3*i+1] for i,n in enumerate(model.nodes))/amount
                self.assertAlmostEqual(centre,(lo+hi)*500.,places=6)
                r=solve_building_case(model,layout,macro,dofs,lookup,{'front_kpa':0.,'back_kpa':0.,'roof_drag':drag})
                self.assertAlmostEqual(r['load_path']['applied_roof_y_n'],amount,places=6)
                self.assertAlmostEqual(r['load_path']['direct_ground_y_n'],0.)
                self.assertLess(abs(r['load_path']['roof_to_member_base_y_residual_n']),1e-5)
                self.assertLess(max(abs(v) for v in r['balance_force_error_n']),1e-5)
                self.assertLess(max(abs(v) for v in r['balance_moment_error_nmm']),.01)
            f,work=roof_drag_load_vector(model,layout,lookup,None)
            self.assertFalse(np.any(f));self.assertEqual(work,[])
            with self.assertRaises(AnalysisError):
                roof_drag_load_vector(model,layout,lookup,{'y_start_m':0.,'y_end_m':17.,'signed_y_pressure_kpa':.03})

    def test_end_wind_leeward_coefficient_uses_length_over_span_all_gable_pitches(self):
        from bracing_analysis_desktop import end_wind_cases
        from wind import WindInputs
        for pitch in (0.,7.5,20.,30.):
            for depth,expected in ((15.,-.5),(40.,-.5+.2/3),(60.,-.3),(120.,-.2)):
                with self.subTest(pitch=pitch,depth=depth):
                    cases=end_wind_cases(WindInputs(building_width_m=30.,building_length_m=depth,roof_pitch_deg=pitch))
                    for case in cases:
                        self.assertAlmostEqual(case['pressure_basis']['leeward_cpe'],expected)
                        self.assertAlmostEqual(case['pressure_basis']['d_over_b'],depth/30.)
                        self.assertEqual(case['components_windward_leeward'][1]['cpe'],case['pressure_basis']['leeward_cpe'])

    def test_end_pressure_reductions_count_only_applied_end_and_effective_internal_surfaces(self):
        from bracing_analysis_desktop import end_wind_combination_factors,end_wind_cases
        from wind import WindInputs
        inputs=WindInputs(front_wall_clad=True,back_wall_clad=True,left_wall_clad=True,right_wall_clad=True)
        self.assertEqual(end_wind_combination_factors(inputs,.2),(.9,1.,2))
        self.assertEqual(end_wind_combination_factors(inputs,-.44),(.8,.8,3))
        one=__import__('dataclasses').replace(inputs,front_wall_clad=False)
        self.assertEqual(end_wind_combination_factors(one,.2),(1.,1.,1))
        self.assertEqual(end_wind_combination_factors(one,.5),(.9,.9,2))
        cases=end_wind_cases(inputs)
        # Large enclosed end walls: Ka is 0.9 windward, 0.95 leeward.
        # Two end walls only: Kce=0.9, with no absent roof counted as a third.
        for case in cases:
            ww,lw=case['components_windward_leeward']
            self.assertAlmostEqual(ww['external_factor'],.81)
            self.assertAlmostEqual(lw['external_factor'],.855)
            self.assertEqual(ww['internal_factor'],1.)

    def test_open_leeward_end_cpi_uses_corrected_end_wall_coefficient(self):
        from bracing_analysis_desktop import end_wind_cases
        from wind import WindInputs
        cases=end_wind_cases(WindInputs(building_width_m=30.,building_length_m=40.,front_wall_clad=True,back_wall_clad=False,
                                      left_wall_clad=True,right_wall_clad=True))
        first=cases[0]
        # Dominant leeward opening: 1.085 volume multiplier, leeward Ka=0.95.
        self.assertAlmostEqual(first['cpi'],(-.5+.2/3)*.95*1.085)

    def x_model(self, area=200, rods=True):
        model = AxialModel3D()
        left = model.add_node((0, 0, 0), fixed=True)
        right = model.add_node((8000, 0, 0), fixed=True)
        top = model.add_node((4000, 0, 3000))
        model.fixed.update((3*top+1, 3*top+2))
        for i, base in enumerate((left, right)):
            model.add_member(AxialMember(str(i), base, top, area, tension_only=rods))
        return model, top

    def test_cross_rods_match_closed_form_and_reverse_without_compression(self):
        model, node = self.x_model()
        for load in (10000., -10000.):
            f = np.zeros(len(model.nodes)*3)
            f[node*3] = load
            result = model.solve(f)
            # L=5000, cos(theta)=4/5; only one rod resists.
            expected = load*5000/(200000*200*(4/5)**2)
            self.assertAlmostEqual(result["displacements_mm"][node][0], expected, places=9)
            self.assertEqual(len([m for m in result["members"] if m["state"] == "slack"]), 1)
            self.assertAlmostEqual(max(m["axial_n"] for m in result["members"]), abs(load)/(4/5), places=7)
            self.assertTrue(all(m["axial_n"] >= 0 for m in result["members"]))

    def test_rod_area_scaling_and_bilateral_compression(self):
        a, node = self.x_model()
        b, _ = self.x_model(area=400)
        f = np.zeros(9)
        f[6] = 10000
        ra, rb = a.solve(f), b.solve(f)
        self.assertAlmostEqual(ra["displacements_mm"][node][0], 2*rb["displacements_mm"][node][0])
        c, _ = self.x_model(rods=False)
        rc = c.solve(f)
        self.assertTrue(any(m["axial_n"] < 0 for m in rc["members"]))
        self.assertAlmostEqual(rc["displacements_mm"][node][0], rb["displacements_mm"][node][0])

    def test_true_spatial_bar_network_matches_vector_solution(self):
        # Three perpendicular bars, rotated about two axes; no planar shortcut.
        angle = .61
        rx = np.array([[1, 0, 0], [0, math.cos(angle), -math.sin(angle)], [0, math.sin(angle), math.cos(angle)]])
        rz = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
        axes = rx @ rz
        model = AxialModel3D()
        bases = [model.add_node(-3000*axes[:, i], fixed=True) for i in range(3)]
        top = model.add_node((0, 0, 0))
        for i, base in enumerate(bases):
            model.add_member(AxialMember(str(i), base, top, 300, tension_only=True))
        f = np.zeros(12)
        f[-3:] = axes @ np.array([2000, 3000, 4000])
        result = model.solve(f)
        np.testing.assert_allclose(result["displacements_mm"][top], f[-3:]*3000/(200000*300), atol=1e-10)
        np.testing.assert_allclose([m["axial_n"] for m in result["members"]], [2000, 3000, 4000], atol=1e-6)

    def test_slack_mechanism_and_missing_load_path_fail(self):
        model = AxialModel3D()
        a = model.add_node((0, 0, 0), fixed=True)
        b = model.add_node((4000, 0, 0))
        model.fixed.update((4, 5))
        model.add_member(AxialMember("rod", a, b, 200, tension_only=True))
        f = np.array([0, 0, 0, -1000, 0, 0.])
        unloaded = model.solve(np.zeros(6))
        self.assertEqual(unloaded["tangent_status"], "neutral_rods_required_stability_unverified")
        with self.assertRaises(AnalysisError):
            model.solve(f)
        model.fixed.remove(4)
        with self.assertRaisesRegex(AnalysisError, "Unstable"):
            model.solve(-f)

    def test_portal_attachment_reduction_matches_direct_loaded_solution(self):
        g = geometry()
        # Non-nodal attachments exercise the rotation/Hermite terms.
        points = [(g.span_mm*f, g.roof_z(g.span_mm*f)) for f in (0., .2, .5, .8, 1.)]
        # Refine the FE space first; attachment coordinates need not coincide.
        g2 = geometry(end_wall_stations_mm=[0, 1000, 2000, 3000, 4000, 5000, 6000])
        structure = frame(g2, fixed=True)
        loads = np.zeros(3*len(structure.nodes))
        loads[structure.nodes[2].uy] = -12000
        macro = PortalReduction(structure, points, loads)
        applied = np.array([5000., -3000., 1000., -2000., 0., -1000., -1200., -800., -3000., -500.])
        direct = structure.solve_with_stiffness(macro.k, loads + macro.h.T @ applied)
        movement = np.linalg.solve(macro.stiffness, macro.equivalent_load+applied)
        np.testing.assert_allclose(movement, macro.h @ direct, atol=1e-8)
        recovered = macro.recover(movement)
        np.testing.assert_allclose(recovered["interface_forces_n"], applied.reshape(-1, 2), atol=1e-6)
        for n, item in zip(structure.nodes, recovered["node_displacements"]):
            self.assertAlmostEqual(item["ux_mm"], direct[n.ux], places=7)
            self.assertAlmostEqual(item["uz_mm"], direct[n.uy], places=7)

    def test_end_wall_load_integration_preserves_resultant_and_overturning(self):
        for shape in ("Gable", "Skillion"):
            g = geometry(roof_shape=shape, end_wall_stations_mm=[0, 2000, 4000, 6000])
            layout = fixed_layout(g)
            macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
            model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
            f, work = end_wall_load_vector(model, layout, lookup, 1.2, -.4)
            area = sum((b-a)*(g.roof_z(a)+g.roof_z(b))/2 for a, b in zip(g.roof_stations, g.roof_stations[1:]))
            self.assertAlmostEqual(sum(f[1::3]), (1.2-.4)*area/1000, places=7)
            for item in work:
                self.assertAlmostEqual(item["roof_resultant_n"], item["direct_ground_resultant_n"])
            # Integral of p*H^2/2 gives base overturning from vertical strips.
            integral_h2 = sum((b-a)*(g.roof_z(a)**2+g.roof_z(a)*g.roof_z(b)+g.roof_z(b)**2)/3
                              for a, b in zip(g.roof_stations, g.roof_stations[1:]))
            moment = np.sum(np.cross(np.asarray(model.nodes), f.reshape(-1, 3)), axis=0)
            self.assertAlmostEqual(moment[0], -(1.2-.4)/2000*integral_h2, places=5)

    def test_unbraced_portal_sway_mechanism_can_be_stabilised_by_real_rod(self):
        from main import Node, FrameElement2D, Structure2D
        structure = Structure2D()
        bottom, top = Node(1, 3000, 0), Node(2, 3000, 4000)
        for n in (bottom, top):
            structure.add_node(n)
        structure.add_support(bottom.ux)
        structure.add_support(bottom.uy)
        profile = SimpleNamespace(A=1500., I_analysis=12e6, name="Benchmark column")
        structure.add_element(FrameElement2D(1, bottom, top, 200000, profile, group="TOP"))
        macro = PortalReduction(structure, [(3000, 4000)])
        # The pin-based column has zero horizontal stiffness before bracing.
        self.assertLess(abs(macro.stiffness[0, 0]), 1e-6)
        model = AxialModel3D()
        base = model.add_node((0, 0, 0), fixed=True)
        roof = model.add_node((3000, 0, 4000))
        model.fixed.add(3*roof+1)
        model.add_block([3*roof, 3*roof+2], macro.stiffness)
        model.add_member(AxialMember("real rod", base, roof, 200, tension_only=True))
        force = np.array([0, 0, 0, 10000, 0, 0.])
        result = model.solve(force)
        expected_z = -10000*(4/3)/(200000*1500/4000)
        expected_x = ((10000/(3/5))*5000/(200000*200)-(4/5)*expected_z)/(3/5)
        self.assertAlmostEqual(result["displacements_mm"][roof][2], expected_z, places=8)
        self.assertAlmostEqual(result["displacements_mm"][roof][0], expected_x, places=8)

    def test_interior_attachment_member_end_forces_include_physical_point_load(self):
        from main import Node, FrameElement2D, Structure2D
        structure = Structure2D()
        a, b = Node(1, 0, 3000), Node(2, 6000, 3000)
        for n in (a, b):
            structure.add_node(n)
        for dof in (a.ux, a.uy, a.rz):
            structure.add_support(dof)
        profile = SimpleNamespace(A=1500., I_analysis=12e6, name="Benchmark cantilever")
        structure.add_element(FrameElement2D(1, a, b, 200000, profile, group="TOP"))
        macro = PortalReduction(structure, [(3000, 3000)])
        applied = np.array([0., -10000.])
        movement = np.linalg.solve(macro.stiffness, applied)
        recovered = macro.recover(movement)
        ends = recovered["member_end_forces"][0]["local_n_n_nmm"]
        # Independent statics: a midpoint downward force gives V=P, M=P*a
        # at the root and zero actions at the unloaded free end.
        np.testing.assert_allclose(ends, [0, 10000, 30e6, 0, 0, 0], atol=1e-6)
        self.assertEqual(len(recovered["interior_attachment_point_loads"]), 1)

    def test_reduced_building_matches_unreduced_portals_and_recovers_reactions(self):
        g = geometry(end_wall_stations_mm=[0, 2000, 4000, 6000])
        layout = fixed_layout(g, end=True)
        structure = frame(g)
        for node in (structure.nodes[0], structure.nodes[-1]):
            structure.add_spring(node.rz, 5e8)
        macro = PortalReduction(structure, [(x, g.roof_z(x)) for x in g.roof_stations])
        model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
        case = {"name": "Independent comparison", "front_kpa": 1.1, "back_kpa": .3}
        reduced = solve_building_case(model, layout, macro, dofs, lookup, case)
        loads, _ = end_wall_load_vector(model, layout, lookup, 1.1, .3)
        # Assemble original portal free DOFs plus longitudinal roof translations.
        nf = len(macro.free)
        portals = len(roof)
        ny = sum(len(row) for row in roof)
        transform = np.zeros((3*len(model.nodes), portals*nf+ny))
        full = np.zeros((portals*nf+ny, portals*nf+ny))
        yindex = portals*nf
        for i, nodes in enumerate(roof):
            full[i*nf:(i+1)*nf, i*nf:(i+1)*nf] = macro.k[np.ix_(macro.free, macro.free)]
            for j, node in enumerate(nodes):
                transform[3*node, i*nf:(i+1)*nf] = macro.h[2*j, macro.free]
                transform[3*node+2, i*nf:(i+1)*nf] = macro.h[2*j+1, macro.free]
                transform[3*node+1, yindex] = 1
                yindex += 1
        for member, response in zip(model.members, reduced["members"]):
            if member.tension_only and response["state"] == "slack":
                continue
            delta = np.subtract(model.nodes[member.end], model.nodes[member.start])
            length = np.linalg.norm(delta)
            row = delta/length @ (transform[3*member.end:3*member.end+3]-transform[3*member.start:3*member.start+3])
            full += member.modulus_mpa*member.area_mm2/length*np.outer(row, row)
        original = np.linalg.solve(full, transform.T @ loads)
        np.testing.assert_allclose(transform @ original, np.asarray(reduced["displacements_mm"]).reshape(-1), atol=1e-7)
        for i, recovered in enumerate(reduced["portals"]):
            for n, output in zip(structure.nodes, recovered["node_displacements"]):
                if n.ux in macro.free:
                    self.assertAlmostEqual(output["ux_mm"], original[i*nf+list(macro.free).index(n.ux)], places=7)
        self.assertLess(max(abs(v) for v in reduced["balance_force_error_n"]), 1e-5)
        self.assertLess(max(abs(v) for v in reduced["balance_moment_error_nmm"]), .01)

    def test_unbraced_and_disconnected_buildings_do_not_get_artificial_restraints(self):
        g = geometry()
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        with self.assertRaisesRegex(AnalysisError, "Pin"):
            build_braced_building(BracingLayout(g), macro, 1500, 200, 200, 200, 1500)
        layout = BracingLayout(g, pinned=["left_wall/bay/0"])
        model, _, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
        with self.assertRaises(AnalysisError):
            solve_building_case(model, layout, macro, dofs, lookup, {"front_kpa": 1., "back_kpa": .3})

    def test_longitudinal_roof_load_reaches_side_brace_bases_not_normal_end_braces(self):
        g = geometry()
        layout = fixed_layout(g,end=True)
        macro = PortalReduction(frame(g),[(x,g.roof_z(x)) for x in g.roof_stations])
        model,_,dofs,lookup = build_braced_building(layout,macro,1500,300,300,300,1500)
        for sign in (1.,-1.):
            result = solve_building_case(model,layout,macro,dofs,lookup,
                                        {'front_kpa':sign,'back_kpa':sign*.3})
            path = result['load_path']
            # Independent integrated wall area for this symmetric gable. Half
            # reaches roof supports and half goes directly to end-post bases.
            area = g.span_mm*(g.eave_mm+(g.roof_z(g.span_mm/2)-g.eave_mm)/2)
            expected = sign*1.3*area/1000/2
            self.assertAlmostEqual(path['applied_roof_y_n'],expected,places=6)
            self.assertAlmostEqual(path['direct_ground_y_n'],expected,places=6)
            ground = path['member_base_reactions_n']
            side_y = sum(v[1] for face,v in ground.items() if face in {'left_wall','right_wall'})
            end_y = sum(v[1] for face,v in ground.items() if face in {'front_end','back_end'})
            self.assertAlmostEqual(side_y,-expected,places=6)
            self.assertEqual(end_y,0.)
            self.assertLess(abs(path['roof_to_member_base_y_residual_n']),1e-5)
            for member in result['members']:
                if member['kind']=='end_brace':
                    self.assertEqual(model.nodes[member['start']][1],model.nodes[member['end']][1])


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_3d_analysis_")
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

    def setUp(self):
        self.window.apply_preset_data(copy.deepcopy(self.defaults))
        self.window.bracing_location_layout = fixed_layout(self.window.current_bracing_layout_geometry()).to_dict()
        self.window.generate_and_solve()

    def test_directional_pressures_reverse_physical_ends_and_respect_openings(self):
        from bracing_analysis_desktop import end_wind_cases
        from wind import WindInputs
        cases = end_wind_cases(WindInputs(orientation="Any", front_wall_clad=True, back_wall_clad=True))
        for i in range(4):
            self.assertAlmostEqual(cases[i]["front_kpa"], -cases[i+4]["back_kpa"])
            self.assertAlmostEqual(cases[i]["back_kpa"], -cases[i+4]["front_kpa"])
        opened = end_wind_cases(WindInputs(orientation="Any", front_wall_clad=False, back_wall_clad=True))
        self.assertTrue(all(c["front_kpa"] == 0 for c in opened))
        self.assertTrue(any(c["back_kpa"] != 0 for c in opened))
        with self.assertRaisesRegex(AnalysisError, "No end-wall pressure"):
            end_wind_cases(WindInputs(front_wall_clad=False, back_wall_clad=False))

    def test_actual_desktop_analysis_preserves_portal_and_exports_complete_evidence(self):
        from bracing_analysis_desktop import prepare_context, analyse_context, text_report
        w = self.window
        before = copy.deepcopy(w.collect_preset_data())
        springs = dict(w.structure.springs)
        movement = w.displacements.copy()
        context = prepare_context(w)
        profile = min(w.catalog.shs_profiles(), key=lambda p: abs(p.A-1500))
        result = analyse_context(context, profile, profile)
        self.assertEqual(result["status"], "SOLVED_DEMANDS_ONLY")
        self.assertEqual(len(result["cases"]), 8)
        self.assertEqual(w.collect_preset_data(), before)
        self.assertEqual(w.structure.springs, springs)
        np.testing.assert_array_equal(w.displacements, movement)
        encoded = json.loads(json.dumps(result, allow_nan=False))
        self.assertTrue(encoded["cases"][0]["portals"][0]["member_end_forces"])
        self.assertIn("ULS MEMBER DEMAND ENVELOPES", text_report(result))
        self.assertIn("no P-delta", text_report(result))
        self.assertNotEqual(result["nominal_layout"]["geometry"]["span_mm"], result["analysis_layout"]["geometry"]["span_mm"])
        for case in result["cases"]:
            self.assertTrue(all(m["axial_n"] >= 0 for m in case["members"] if m["tension_only"]))
        w.building_length_input.setText("48")
        with self.assertRaisesRegex(AnalysisError, "dimensions changed"):
            prepare_context(w)

    def test_legacy_restraint_removed_in_private_copy_and_new_settings_roundtrip(self):
        from bracing_analysis_desktop import prepare_context
        w = self.window
        preset = w.collect_preset_data()
        preset["geometry"]["end_wall_bracing"] = "Both ends"
        w.apply_preset_data(preset)
        w.generate_and_solve()
        context = prepare_context(w)
        endpoints = [n for e in context["portal"].structure.elements if e.group == "TOP" for n in (e.start, e.end)]
        left = min(endpoints, key=lambda n: n.x)
        right = max(endpoints, key=lambda n: n.x)
        for n in (left, right):
            self.assertNotIn(n.ux, context["portal"].structure.springs)
            self.assertNotIn(n.ux, context["portal"].structure.restrained_dofs)
        self.assertTrue(any(n.ux in w.structure.springs for n in (left, right)))
        w.bracing_3d_settings = {"schema": "portalcalc.bracing-analysis-settings.v1", "strut_section": "test", "end_post_section": "test"}
        saved = copy.deepcopy(w.collect_preset_data())
        w.apply_preset_data(self.defaults)
        self.assertIsNone(w.bracing_3d_settings)
        w.apply_preset_data(saved)
        self.assertEqual(w.bracing_3d_settings, saved["bracing"]["analysis_3d"])

    def test_dialog_runs_exports_and_invalidates_previous_success_on_failure(self):
        from bracing_analysis_desktop import prepare_context
        from bracing_analysis_ui import BracingAnalysisDialog
        dialog = BracingAnalysisDialog(prepare_context(self.window), self.window.catalog)
        try:
            dialog.run_button.click()
            self.assertIsNotNone(dialog.result)
            self.assertGreater(dialog.member_table.rowCount(), 0)
            self.assertEqual(dialog.case_combo.count(), 8)
            legacy_end = copy.deepcopy(dialog.context["end_brace"])
            dialog.end_brace_combo.setCurrentIndex((dialog.end_brace_combo.currentIndex()+1) % dialog.end_brace_combo.count())
            self.assertIsNone(dialog.result)
            self.assertFalse(dialog.export_json_button.isEnabled())
            dialog.run_button.click()
            self.assertEqual(dialog.result["sections"]["end_brace"]["label"], dialog.end_brace_combo.currentText())
            self.assertEqual(dialog.context["end_brace"], legacy_end)
            with tempfile.TemporaryDirectory() as output:
                path = Path(output)/"evidence.json"
                with mock.patch("bracing_workspace_ui.QFileDialog.getSaveFileName", return_value=(str(path), "JSON")):
                    dialog.export_json_button.click()
                self.assertEqual(json.loads(path.read_text())["status"], "SOLVED_DEMANDS_ONLY")
            with mock.patch("bracing_workspace_ui.solve_selection", side_effect=AnalysisError("Unstable benchmark")):
                dialog.run_button.click()
            self.assertIsNone(dialog.result)
            self.assertFalse(dialog.export_json_button.isEnabled())
            self.assertIn("Unstable benchmark", dialog.status_label.text())
            readonly = BracingAnalysisDialog(dialog.context, self.window.catalog, read_only=True)
            self.assertFalse(readonly.strut_combo.isEnabled())
            self.assertFalse(readonly.post_combo.isEnabled())
            self.assertFalse(readonly.end_brace_combo.isEnabled())
            self.assertFalse(readonly.auto_button.isEnabled())
            self.assertTrue(all(not box.isEnabled() for box in readonly.criteria_inputs.values()))
            readonly.reject()
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
