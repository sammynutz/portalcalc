"""Rated components, sharing/interaction, mixed sizing and data-entry checks.

Declared test ratings are synthetic fixtures; named project-default tests also
verify the supplied schedule, adopted interim stiffness and project rope pricing.
"""
import copy
import json
import math
import os
import tempfile
import unittest

from bracing_analysis import AnalysisError, AxialModel3D, AxialMember
from bracing_components import (StrutPlan, assembly_assessment, location_options,
                                wire_check, wire_modulus, wire_options, wire_price, WIRE_ASSUMED_STIFFNESS)
from bracing_design import DesignCriteria, group_cost, member_assessment, solve_selection, auto_size
from bracing_schedule import rod_options, SOURCE, WIRE_PRICE_SCHEMA, WIRE_PRICE_SOURCE, WIRE_PRICE_SHA256
from purlin_combined import properties, compression_capacity, tension_capacity, METHOD


def location(sharing=False):
    data = {"section": "FIXTURE PURLIN", "area_mm2": 600., "basis_key": "FIXTURE BASIS",
            "bending_key": "FIXTURE BENDING", "analysis_span_mm": 8000., "bending_span_mm": 8000.,
            "bending_ratio": .5, "service_ratio": .2, "added_bending_ratio": .02, "added_service_ratio": .01,
            "axial_rating": {"basis_key": "FIXTURE BASIS", "compression_kn": 40., "tension_kn": 80.,
                             "elastic_buckling_kn": 100., "source": "SYNTHETIC TEST DATA ONLY",
                             "back_to_back_sharing": sharing, "extra_bending_key": "FIXTURE BENDING",
                             "extra_compression_kn": 50., "extra_tension_kn": 70., "extra_sls_deflection_mm": 2.,
                             "back_to_back_compression_kn": 50., "back_to_back_tension_kn": 70., "back_to_back_sls_deflection_mm": 3.}}
    return {"id": "strut/bay/0/line/0", "purlin": data, "existing_count": 1,
            "bay": 0, "line": 0, "length_mm": 8000., "x_mm": 0., "braced_bay": True}


def fixture(force=-20000.):
    member = {"id": "strut/bay/0/line/0", "kind": "longitudinal_strut", "tension_only": False,
              "length_mm": 8000., "start": 0, "end": 1, "axial_n": force}
    return {"cases": [{"case": {"name": level, "level": level}, "members": [dict(member)]}
                      for level in ("ULS", "SLS")]}


class RatedComponentTests(unittest.TestCase):
    def test_result_summary_separates_strength_local_deflection_and_global_movement(self):
        from bracing_sizing_diagnostics import result_summary, governing_check_row
        strength = {'case': 'ultimate', 'mode': 'Axial plus bending', 'ratio': 1.6, 'component': 1}
        local = {'case': 'service', 'mode': 'Local deflection', 'ratio': 2.0}
        check = {'case': 'ultimate', 'mode': 'Axial plus bending', 'ratio': 1.6,
                 'case_checks': [{**strength, 'ratio': .4, 'component': 0}, strength, local]}
        self.assertEqual(governing_check_row(check)['component'], 1)
        result = {'cases': [{'case': {'name': 'ultimate', 'level': 'ULS'}}, {'case': {'name': 'service', 'level': 'SLS'}}],
                  'nodes': [{'label': 'Right eave'}], 'design': {'global_sls': {'ratio': 13.125, 'node': 0,
                     'case': 'service', 'movement_mm': 525., 'limit_mm': 40.}, 'taut_tangent_verified': True},
                  'analysis_layout': {'pinned_panels': ['left_wall/bay/0']},
                  'section_checks': {'a': check, 'b': {'case': 'ultimate', 'ratio': None,
                    'case_checks': [{'case': 'ultimate', 'ratio': None}]}}}
        summary = result_summary(result)
        self.assertEqual(summary['member_counts']['Strength'], 1)
        self.assertEqual(summary['member_counts']['Local deflection'], 1)
        self.assertEqual(summary['member_counts']['Missing check'], 1)
        self.assertEqual(summary['failed_members'], 2)
        self.assertIn('525.0 mm / 40.0 mm limit', summary['text'])
        self.assertEqual(summary['wall_panel_counts']['right_wall'], 0)
        result['section_checks'] = {}
        self.assertIn('Overall longitudinal movement: FAIL', result_summary(result)['text'])
        self.assertEqual(result_summary(result)['failed_members'], 0)
        result['sizing_failure'] = {'status': 'STOPPED_NO_ADOPTION'}
        self.assertIn('original input selections shown', result_summary(result)['text'])

    def test_calculated_tension_independent_workings_and_declared_override(self):
        data = {'section': 'C/Z 250 19', 'area_mm2': 808., 'basis_key': 'TEST', 'axial_rating': {}}
        t = tension_capacity(data, DesignCriteria())
        self.assertAlmostEqual(t['tension_gross_yield_kn'], .9*808*450/1000)
        self.assertAlmostEqual(t['tension_net_fracture_kn'], .9*.85*.85*(.7*808)*480/1000)
        self.assertEqual(t['tension_capacity_kn'], min(t['tension_gross_yield_kn'], t['tension_net_fracture_kn']))
        self.assertEqual(t, tension_capacity(data, DesignCriteria(brace_fy_mpa=100., brace_fu_mpa=200.)))
        self.assertLess(tension_capacity(data, DesignCriteria(net_area_factor=.5))['tension_capacity_kn'], t['tension_capacity_kn'])
        data['axial_rating'] = {'tension_kn': 90., 'source': 'SYNTHETIC TEST', 'basis_key': 'TEST'}
        self.assertEqual(tension_capacity(data)['tension_capacity_kn'], 90.)
        data['axial_rating']['basis_key'] = 'STALE'
        with self.assertRaisesRegex(AnalysisError, 'tension rating required'):
            tension_capacity(data)
        data['axial_rating'] = {'tension_method': 'declared'}
        with self.assertRaisesRegex(AnalysisError, 'tension rating required'):
            tension_capacity(data)
        data['axial_rating'] = {}
        data['area_mm2'] = 999.
        with self.assertRaisesRegex(AnalysisError, 'area does not match'):
            tension_capacity(data)

    def test_simplified_purlin_workings_match_sheet_and_metroll_axes(self):
        self.assertAlmostEqual(compression_capacity(713., 79.6, 8000.), 63.7416705975)
        props = properties("C/Z 200 19", "Z")
        self.assertEqual(props["web_rx_mm"], 78.55)
        self.assertEqual(props["web_ix_mm4"], 4400000.)
        self.assertNotEqual(props["web_ix_mm4"], 4840000.)  # principal Ix is not used
        loc = location()
        data = loc["purlin"]
        data.update(section="C/Z 200 19", area_mm2=713., bending_ratio=1.34/2.55,
                    axial_rating={"compression_method": METHOD, "purlin_shape": "Z"})
        checked = assembly_assessment(fixture(-28000.), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        row = checked["case_checks"][0]
        ns = 903*713*(78.55/8000)**2
        self.assertAlmostEqual(row["compression_capacity_kn"], ns)
        self.assertAlmostEqual(row["available_compression_kn"], ns*(1-1.34/2.55))
        self.assertAlmostEqual(row["ratio"], 28/ns+1.34/2.55)
        self.assertTrue(checked["passes"])
        changed_criteria = DesignCriteria(net_area_factor=.1, tension_distribution_factor=.1)
        self.assertEqual(checked["ratio"], member_assessment(fixture(-28000.), 0, location_options(loc, [])[0], changed_criteria)["ratio"])
        # A second connected purlin changes the axial share, not its table radius.
        loc["existing_count"] = 2
        pair = assembly_assessment(fixture(-28000.), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        self.assertAlmostEqual(pair["ratio"], 14/ns+1.34/2.55)

    def test_simplified_limits_tension_missing_properties_and_exhausted_bending(self):
        loc = location()
        data = loc["purlin"]
        data.update(section="C/Z 250 15", area_mm2=638., axial_rating={"compression_method": METHOD, "tension_method": "declared"})
        def assess(force):
            return assembly_assessment(fixture(force), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        self.assertTrue(assess(-1000.)["passes"])
        self.assertIsNone(assess(1000.)["ratio"])
        data["axial_rating"].update(tension_kn=80., source="SYNTHETIC TENSION TEST", basis_key=data["basis_key"])
        self.assertAlmostEqual(assess(1000.)["ratio"], .5+1/80)
        data["bending_ratio"] = 1.1
        failed = assess(-1000.)
        self.assertFalse(failed["passes"])
        self.assertEqual(failed["case_checks"][0]["available_compression_kn"], 0.)
        data["section"] = "unsupported"
        self.assertIsNone(assess(-1000.)["ratio"])
        with self.assertRaisesRegex(AnalysisError, "Inconsistent published"):
            properties("C/Z 150 24", "Z")
        self.assertEqual(properties("C/Z 150 24", "C")["area_mm2"], 708.)
        for shape in ("C", "Z"):
            self.assertGreater(properties("C/Z 250 15", shape)["web_rx_mm"], 96.)
        with self.assertRaises(AnalysisError):
            compression_capacity(638., 96.4, 0.)

    def test_wire_uses_declared_ea_and_capacity_not_solid_diameter(self):
        data = {"10": {"ea_kn": 4000., "design_tension_kn": 30., "construction": "TEST CONSTRUCTION",
                       "source": "SYNTHETIC RATED ASSEMBLY", "price_per_m": 2., "fittings_per_run": 10.}}
        options = wire_options(data)
        self.assertEqual([o["diameter_mm"] for o in options], [8, 10, 12, 14, 16])
        wire = options[1]
        model = AxialModel3D()
        a = model.add_node((0, 0, 0), fixed=True)
        b = model.add_node((5000, 0, 0))
        model.fixed.update((4, 5))
        model.add_member(AxialMember("wire", a, b, wire["area_mm2"], wire_modulus(wire), True))
        result = model.solve([0, 0, 0, 10000, 0, 0])
        self.assertAlmostEqual(result["displacements_mm"][1][0], 10000*5000/4000000)
        self.assertAlmostEqual(wire_check(wire, 10, "test")["ratio"], 1/3)
        self.assertAlmostEqual(wire_price(wire, 10, 2)["total"], 40.)
        self.assertAlmostEqual(wire_modulus(options[2]), 200000.)
        missing = wire_options({'12': {'stiffness_basis': 'declared_ea'}})[2]
        with self.assertRaises(AnalysisError):
            wire_modulus(missing)
        self.assertIsNone(wire_check(missing, 0, "test")["ratio"])

    def test_interim_wire_stiffness_nominal_area_preserves_strength_and_declared_overrides(self):
        for option, capacity in zip(wire_options(), (22.96, 35.92, 51.2, 70.4, 92.)):
            rating = option['rating']
            self.assertEqual(rating['stiffness_basis'], WIRE_ASSUMED_STIFFNESS)
            self.assertAlmostEqual(rating['ea_kn'], 200.*math.pi*option['diameter_mm']**2/4)
            self.assertAlmostEqual(wire_modulus(option), 200000.)
            self.assertEqual(rating['design_tension_kn'], capacity)
            self.assertIn('not supplier-verified', rating['ea_source'])
        # Hand check a 10 mm, 5 m rope: delta = PL/EA at 10 kN.
        wire = wire_options()[1]
        model = AxialModel3D()
        a = model.add_node((0, 0, 0), fixed=True)
        b = model.add_node((5000, 0, 0))
        model.fixed.update((4, 5))
        model.add_member(AxialMember('wire', a, b, wire['area_mm2'], wire_modulus(wire), True))
        solved = model.solve([0, 0, 0, 10000, 0, 0])
        self.assertAlmostEqual(solved['displacements_mm'][1][0], 10000*5000/(200000*math.pi*100/4))
        for raw in (0., -1.):
            invalid = wire_options({'10': {'ea_kn': raw, 'ea_source': 'EXPLICIT INVALID FIXTURE'}})[1]
            self.assertEqual(invalid['rating']['stiffness_basis'], 'declared_ea')
            with self.assertRaises(AnalysisError):
                wire_modulus(invalid)

    def test_supplied_rod_strengths_and_exact_fittings(self):
        rods = rod_options()
        self.assertEqual([r["label"] for r in rods], ["16 DIA ROD", "20 DIA ROD", "24 DIA ROD", "30 DIA ROD"])
        self.assertEqual([r["design_tension_kn"] for r in rods], [54.28672, 84.823, 122.1451, 190.8518])
        self.assertEqual([r["forged_turnbuckle"] for r in rods], ["M16", "M20", "M22", "M28"])
        result = fixture(54286.72)
        for case in result["cases"]:
            case["members"][0]["kind"] = "roof_brace"
            case["members"][0]["tension_only"] = True
        checked = member_assessment(result, 0, rods[0], DesignCriteria(net_area_factor=.1))
        self.assertAlmostEqual(checked["ratio"], 1.)
        self.assertEqual(checked["case_checks"][0]["grade_mpa"], 300.)
        self.assertIsNone(rods[-1]["fitting_unit_cost"])
        with self.assertRaisesRegex(AnalysisError, "M28"):
            group_cost(result, "roof_brace", rods[-1], {"ub": 3.})
        priced = rod_options({"30": 100.})[-1]
        self.assertEqual(group_cost(result, "roof_brace", priced, {"ub": 3.})["listed_fittings_cost"], 100.)

    def test_schedule_wire_strength_needs_separate_ea_source(self):
        self.assertEqual([o["rating"]["design_tension_kn"] for o in wire_options()], [22.96, 35.92, 51.2, 70.4, 92.])
        data = {"10": {"ea_kn": 4000.}}
        wire = wire_options(data)[1]
        with self.assertRaisesRegex(AnalysisError, "EA source"):
            wire_modulus(wire)
        data["10"]["ea_source"] = "SYNTHETIC SOFTWARE TEST EA ONLY"
        wire = wire_options(data)[1]
        self.assertAlmostEqual(wire_check(wire, 35.92, "test")["ratio"], 1.)
        self.assertEqual(wire["rating"]["source"], SOURCE)
        data["10"]["design_tension_kn"] = 100.
        self.assertFalse(wire_check(wire_options(data)[1], 35.92, "test").get("passes", True))

    def test_project_rope_prices_exact_turnbuckles_and_physical_diagonal_costs(self):
        from pathlib import Path
        import hashlib
        image = Path(__file__).resolve().parent/'assets/project_wire_rope_prices_2026-10-31.png'
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest().upper(), WIRE_PRICE_SHA256)
        for option, rate, tb, fitting in zip(wire_options(), (1.35, 1.75, 1.90, 2.40, 3.50),
                                             ('M10', 'M12', 'M16', 'M16', 'M20'), (4., 4., 9.62, 9.62, 16.60)):
            self.assertEqual(option['rating']['price_per_m'], rate)
            self.assertEqual(option['forged_turnbuckle'], tb)
            cost = wire_price(option, 20., 2)  # two physical 10 m diagonals, including slack one
            self.assertAlmostEqual(cost['material_cost'], 20*rate)
            self.assertAlmostEqual(cost['listed_fittings_cost'], 2*fitting)
            self.assertAlmostEqual(cost['total'], 20*rate+2*fitting)
            self.assertEqual(cost['turnbuckle_count'], 2)
            self.assertEqual(cost['rope_price_source'], WIRE_PRICE_SOURCE)
            self.assertIn('excluded by user', cost['allowance'])
        self.assertEqual(wire_options()[2]['rating']['quoted_rope_price_range_per_m'], [1.8, 1.9])
        self.assertIn('Upper end', wire_options()[2]['rating']['rope_price_adoption'])

    def test_rope_price_migration_preserves_overrides_and_deliberately_missing_values(self):
        legacy = {'12': {'price_per_m': None, 'fittings_per_run': None},
                  '10': {'price_per_m': 2.5, 'fittings_per_run': 8.}}
        options = wire_options(legacy)
        self.assertEqual(options[2]['rating']['price_per_m'], 1.9)
        self.assertEqual(options[2]['rating']['fittings_per_run'], 9.62)
        self.assertEqual(options[1]['rating']['price_per_m'], 2.5)
        self.assertEqual(options[1]['rating']['fittings_per_run'], 8.)
        self.assertEqual(options[1]['rating']['rope_price_source'], 'Project-entered rope rate override')
        for key in ('price_per_m', 'fittings_per_run'):
            option = wire_options({'12': {'pricing_schema': WIRE_PRICE_SCHEMA, key: None}})[2]
            self.assertIsNone(option['rating'][key])
            with self.assertRaises(AnalysisError):
                wire_price(option, 10., 1)
        zero = wire_options({'12': {'price_per_m': 0.}})[2]
        with self.assertRaises(AnalysisError):
            wire_price(zero, 10., 1)

    def test_two_existing_purlins_share_force_and_are_not_costed_again(self):
        single = location_options(location(), [])[0]
        double_location = location()
        double_location["existing_count"] = 2
        double = location_options(double_location, [])[0]
        one = assembly_assessment(fixture(), 0, single, DesignCriteria(), member_assessment)
        two = assembly_assessment(fixture(), 0, double, DesignCriteria(), member_assessment)
        self.assertAlmostEqual(one["ratio"], .5+.5/(1-.2))
        self.assertAlmostEqual(two["ratio"], .25+.5/(1-.1))
        self.assertFalse(one["passes"])
        self.assertTrue(two["passes"])
        self.assertTrue(all(row["load_share"] == .5 for row in two["case_checks"]))
        cost = group_cost(fixture(), "strut", StrutPlan({double_location["id"]: double}, double), {"ub": 3., "purlin": 4.})
        self.assertEqual(cost["total"], 0.)
        self.assertEqual(cost["purchased_kg"], 0.)
        self.assertGreater(cost["installed_kg"], 0.)

    def test_added_cold_ply_requires_separate_restraint_and_bending_rating(self):
        loc = location()
        options = location_options(loc, [])
        extra = next(o for o in options if o.arrangement == "additional_purlin")
        # One existing ply keeps its full bending; the added ply uses its
        # separately rated residual axial capacity, not the installed table.
        checked = assembly_assessment(fixture(), 0, extra, DesignCriteria(), member_assessment)
        self.assertTrue(checked["passes"])
        cost = group_cost(fixture(), "strut", StrutPlan({loc["id"]: extra}, extra), {"ub": 3., "purlin": 4.})
        self.assertAlmostEqual(cost["total"], 600*8*.00785*1.07*4)
        loc["purlin"]["axial_rating"]["extra_compression_kn"] = None
        extra = next(o for o in location_options(loc, []) if o.arrangement == "additional_purlin")
        missing = assembly_assessment(fixture(), 0, extra, DesignCriteria(), member_assessment)
        self.assertFalse(missing["passes"])
        self.assertIsNone(missing["ratio"])

    def test_back_to_back_bending_sharing_is_explicit_not_composite_inertia(self):
        loc = location()
        loc["purlin"]["bending_ratio"] = .9
        loc["purlin"]["axial_rating"]["compression_kn"] = 20.
        options = location_options(loc, [])
        plain = next(o for o in options if o.arrangement == "back_to_back")
        a = assembly_assessment(fixture(-10000), 0, plain, DesignCriteria(), member_assessment)
        loc["purlin"]["axial_rating"]["back_to_back_sharing"] = True
        shared = next(o for o in location_options(loc, []) if o.arrangement == "back_to_back")
        b = assembly_assessment(fixture(-10000), 0, shared, DesignCriteria(), member_assessment)
        self.assertFalse(a["passes"])
        self.assertTrue(b["passes"])
        self.assertEqual(plain.A, shared.A)
        self.assertEqual(shared.A, 1200.)

    def test_stale_or_wrong_span_ratings_cannot_pass(self):
        for field, value in (("basis_key", "wrong"), ("compression_kn", None)):
            loc = location()
            loc["purlin"]["axial_rating"][field] = value
            row = assembly_assessment(fixture(), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
            self.assertFalse(row["passes"])
            self.assertIsNone(row["ratio"])
        loc = location()
        loc["purlin"]["bending_span_mm"] = 6000
        row = assembly_assessment(fixture(), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        self.assertFalse(row["passes"])


class ComponentWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["APPDATA"] = cls.temp.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.window.generate_and_solve()
        from bracing_layout import BracingLayout
        layout = BracingLayout(cls.window.current_bracing_layout_geometry())
        for panel in layout.panels.values():
            if "/bay/0" in panel.id and ("wall" in panel.face or panel.face.startswith("roof")):
                layout.toggle(panel.id, "pinned")
        cls.window.bracing_location_layout = layout.to_dict()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.temp.cleanup()

    def dialog(self, saved=None):
        from bracing_analysis_desktop import prepare_context
        from bracing_analysis_ui import BracingAnalysisDialog
        return BracingAnalysisDialog(prepare_context(self.window), self.window.catalog, saved)

    def test_grade300_apex_pair_defaults_manual_override_and_cladding_overlay(self):
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        from bracing_design import section_properties
        context = prepare_context(self.window)
        context['face_cladding'] = {'left_wall':True,'right_wall':False,'front_end':True,'back_end':True}
        ridge = min(range(len(context['layout'].geometry.roof_stations)),key=lambda i:abs(context['layout'].geometry.roof_stations[i]-context['layout'].geometry.span_mm/2))
        identifier = f'strut/bay/0/line/{ridge}'
        d = BracingAnalysisDialog(context,self.window.catalog,{'criteria':{'brace_fy_mpa':250.},
            'strut_members':{identifier:{'existing_count':1}}})
        try:
            self.assertEqual(d.criteria().brace_fy_mpa,300.)
            self.assertTrue(d.criteria_inputs['brace_fy_mpa'].isReadOnly())
            angle = next(c for c in d.component_editor.brace_candidates() if c['label'].startswith('EA '))
            props = section_properties(angle,d.criteria())
            self.assertEqual(props.fy_mpa,300.)
            self.assertEqual(props.fu_mpa,410.)
            member = d.selections()['strut'].members[identifier]
            self.assertEqual(member.location['existing_count'],2)
            self.assertAlmostEqual(member.A,2*member.location['purlin']['area_mm2'])
            data = member.location['purlin']
            self.assertAlmostEqual(data['tributary_width_mm'],600.,places=4)
            self.assertAlmostEqual(data['analysis_span_mm'],8000.)
            expected = self.window.purlin_check['outward_kn_m']*600/self.window.purlin_check['spacing_mm']
            self.assertAlmostEqual(data['beam_inputs']['outward_kn_m'],expected)
            self.assertEqual(d.canvas.orientation_label('right_wall'),'RIGHT (OPEN)')
            self.assertEqual(d.canvas.orientation_label('left_wall'),'LEFT (CLAD)')
            d.run_analysis()
            self.assertIsNotNone(d.result,d.status_label.text())
            checked = d.result
            self.assertIn('600.000',d.report_text)
            self.assertIn('Original roof tributary width: 600.0 mm',d.check_description(identifier))
            self.assertFalse(checked['face_cladding']['right_wall'])
            d.cladding_overlay.setChecked(False)
            self.assertIs(d.result,checked)
            self.assertFalse(d.canvas.show_cladding)
            for azimuth in (-135.,-45.,45.,135.):
                d.canvas.azimuth=azimuth
                self.assertFalse(d.canvas.grab().isNull())
            payload = d.snapshot()
            payload['strut_members'][identifier]['existing_count'] = 1
            payload['strut_members'][identifier]['existing_count_mode'] = 'manual'
            restored = BracingAnalysisDialog(context,self.window.catalog,payload)
            try:
                self.assertEqual(restored.selections()['strut'].members[identifier].location['existing_count'],1)
                self.assertEqual(restored.snapshot(),payload)
            finally:restored.reject()
        finally:d.reject()

    def fill_fixture_ratings(self, dialog):
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtCore import Qt
        editor = dialog.component_editor
        for combo in editor.purlin_methods.values():
            combo.setCurrentIndex(combo.findData("declared"))
        for combo in editor.purlin_tension_methods.values():
            combo.setCurrentIndex(combo.findData("declared"))
        for row in range(editor.purlin_table.rowCount()):
            for col, value in ((2, "100"), (3, "160"), (4, "300"), (6, "SYNTHETIC SOFTWARE TEST ONLY")):
                editor.purlin_table.setItem(row, col, QTableWidgetItem(value))
            editor.purlin_table.item(row, 5).setCheckState(Qt.Checked)
            for col, value in ((1, "80"), (2, "120"), (3, "3"), (4, "80"), (5, "120"), (6, "3")):
                editor.extra_table.setItem(row, col, QTableWidgetItem(value))
        g = dialog.context["layout"].geometry
        ridge = min(range(len(g.roof_stations)), key=lambda i: abs(g.roof_stations[i]-g.span_mm/2))
        for row, (loc, arr, steel) in enumerate(editor.strut_rows):
            if loc["line"] == ridge:
                editor.strut_table.setItem(row, 3, QTableWidgetItem("2"))

    def test_unrated_real_purlins_are_visible_but_cannot_pass_or_auto_size(self):
        dialog = self.dialog()
        try:
            for combo in dialog.component_editor.purlin_tension_methods.values():
                combo.setCurrentIndex(combo.findData('declared'))
            self.assertTrue(dialog.component_editor.mixed.isChecked())
            self.assertEqual(len(dialog.component_editor.wire_candidates()), 5)
            self.assertEqual([o["label"] for o in dialog.component_editor.brace_candidates() if o.get("component_type") == "scheduled_rod"], ["16 DIA ROD", "20 DIA ROD", "24 DIA ROD", "30 DIA ROD"])
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result)
            self.assertFalse(dialog.result["design"]["passes"])
            self.assertTrue(any(r["ratio"] is None for r in dialog.result["section_checks"].values()))
            worked = [r for checked in dialog.result["section_checks"].values() for r in checked["case_checks"] if r.get("method") == METHOD and "compression_capacity_kn" in r]
            self.assertTrue(worked)
            self.assertTrue(any(r["axial_kn"] < 0 and r["ratio"] is not None for r in worked))
            self.assertIn("SIMPLIFIED PURLIN COMPRESSION", dialog.report_text)
            before = dialog.snapshot()
            baseline_sections = copy.deepcopy(dialog.result["design_request"]["sections"])
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result)
            self.assertEqual(dialog.snapshot(), before)
            self.assertEqual(dialog.result["design_request"]["sections"], baseline_sections)
            issue = dialog.result["sizing_failure"]
            self.assertEqual(issue["status"], "STOPPED_NO_ADOPTION")
            self.assertIn("Nt", dialog.status_label.text())
            self.assertTrue(issue["rejected_alternatives"])
            self.assertIn("REJECTED ALTERNATIVES", dialog.report_text)
            self.assertTrue(dialog.export_json_button.isEnabled())
            self.assertFalse(dialog.sizing_issue_button.isHidden())
            dialog.sizing_issue_button.click()
            self.assertIs(dialog.tabs.currentWidget(), dialog.component_editor)
            self.assertIs(dialog.component_editor.tabs.currentWidget(), dialog.component_editor.purlin_table)
            self.assertEqual(dialog.component_editor.purlin_table.currentColumn(), 3)
            json.dumps(dialog.result, allow_nan=False)
            # Supplying the missing tensile data resolves this stop without
            # inventing compression ratings or adopting a failed candidate.
            from PySide6.QtWidgets import QTableWidgetItem
            for row in range(dialog.component_editor.purlin_table.rowCount()):
                dialog.component_editor.purlin_table.setItem(row, 3, QTableWidgetItem("160"))
                dialog.component_editor.purlin_table.setItem(row, 6, QTableWidgetItem("SYNTHETIC TENSION TEST ONLY"))
            self.assertIsNone(dialog.result)
            self.assertTrue(dialog.sizing_issue_button.isHidden())
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            self.assertNotIn("sizing_failure", dialog.result)
        finally:
            dialog.reject()

    def test_default_calculated_tension_allows_cost_sizing_and_exact_rerun(self):
        dialog = self.dialog()
        try:
            editor = dialog.component_editor
            self.assertTrue(all(combo.currentData() == 'calculated' for combo in editor.purlin_tension_methods.values()))
            self.assertTrue(all(r['tension_kn'] is None for r in editor.snapshot()['purlin_axial_ratings'].values()))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            worked = [r for check in dialog.result['section_checks'].values() for r in check['case_checks'] if r.get('tension_method') == 'calculated']
            self.assertTrue(worked)
            for r in worked:
                expected = .9*min(r['tension_gross_area_mm2']*450, .85*.85*.7*r['tension_gross_area_mm2']*480)/1000
                self.assertAlmostEqual(r['tension_capacity_kn'], expected)
                self.assertAlmostEqual(r['ratio'], r['axial_kn']/expected+r['bending_ratio'])
            preview = float(editor.purlin_table.item(0, 13).text())
            dialog.criteria_inputs['net_area_factor'].setValue(.5)
            self.assertLess(float(editor.purlin_table.item(0, 13).text()), preview)
            dialog.criteria_inputs['net_area_factor'].setValue(.7)
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result['design']['passes'], dialog.status_label.text())
            self.assertTrue(all(row['passes'] for row in dialog.result['section_checks'].values()))
            self.assertIn('PURLIN MEMBER TENSION WORKINGS', dialog.report_text)
            rerun = solve_selection(dialog.current_context(), dialog.selections(), dialog.criteria(), dialog.context['cost_rates'])
            self.assertTrue(rerun['design']['passes'])
            self.assertAlmostEqual(rerun['design']['estimated_cost'], dialog.result['design']['estimated_cost'])
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(saved, restored.snapshot())
            finally:
                restored.reject()
            json.dumps(dialog.result, allow_nan=False)
        finally:
            dialog.reject()

    def test_one_sided_layout_sls_stop_points_to_system_movement_without_adopting_sections(self):
        d = self.dialog()
        try:
            model = d.layout_editor.model
            for identifier in list(model.pinned):
                model.toggle(identifier, 'pinned')
            last = len(model.geometry.bay_stations_mm)-2
            for panel in model.panels.values():
                if (panel.face.startswith('roof') or panel.face == 'left_wall') and any(f'/bay/{i}' in panel.id for i in (0,last)):
                    model.toggle(panel.id, 'pinned')
            d.run_analysis()
            self.assertIsNotNone(d.result, d.status_label.text())
            self.assertGreater(d.result['design']['global_sls']['ratio'], 1.)
            self.assertIn('Overall longitudinal movement: FAIL', d.result_summary_label.text())
            self.assertIn('local deflection failures', d.result_summary_label.text())
            self.assertTrue(d.movement_button.isEnabled())
            before = d.snapshot()
            d.run_auto_size()
            self.assertIsNotNone(d.result, d.status_label.text())
            issue = d.result['sizing_failure']
            self.assertEqual(issue['input_target'], 'layout')
            self.assertIn('global longitudinal serviceability', issue['reason'])
            self.assertEqual(d.snapshot(), before)
            self.assertGreater(issue['global_sls']['movement_mm'], issue['global_sls']['limit_mm'])
            self.assertIn('Original-input longitudinal SLS movement', d.report_text)
            self.assertEqual(d.case_combo.currentText(), issue['global_sls']['case'])
            self.assertIn('original input selections shown', d.result_summary_label.text())
            self.assertEqual(d.visual_mode.currentData(), 'deformed')
            d.sizing_issue_button.click()
            self.assertEqual(d.visual_mode.currentData(), 'deformed')
            self.assertEqual(d.deformation_scale.value(), 1.)
            d.case_combo.setCurrentIndex(0)
            d.deformation_scale.setValue(50.)
            d.movement_button.click()
            self.assertEqual(d.case_combo.currentText(), issue['global_sls']['case'])
            self.assertEqual(d.deformation_scale.value(), 1.)
            self.assertIn('Circled node', d.member_detail.text())
            self.assertEqual(d.member_table.item(0, 4).text(), 'Strength')
            self.assertEqual(d.tabs.currentIndex(), 1)
            json.dumps(d.result, allow_nan=False)
            d.invalidate()
            self.assertTrue(d.result_summary_box.isHidden())
            self.assertFalse(d.movement_button.isEnabled())
        finally:
            d.reject()

    def test_simplified_method_shape_and_tension_data_roundtrip(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog()
        try:
            for row, key in enumerate(dialog.component_editor.rating_keys):
                dialog.component_editor.purlin_shapes[key].setCurrentText("C")
                dialog.component_editor.purlin_table.setItem(row, 3, QTableWidgetItem("160"))
                dialog.component_editor.purlin_table.setItem(row, 6, QTableWidgetItem("SYNTHETIC TEST TENSION DATA"))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(all(r["ratio"] is not None for r in dialog.result["section_checks"].values()))
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            self.assertTrue(all(a.location["purlin"]["axial_rating"]["compression_method"] == METHOD
                                for a in dialog.selections()["strut"].members.values()))
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(saved, restored.snapshot())
                self.assertTrue(all(v.currentText() == "C" for v in restored.component_editor.purlin_shapes.values()))
            finally:
                restored.reject()
            json.dumps(dialog.result, allow_nan=False)
        finally:
            dialog.reject()

    def test_scheduled_wire_ea_reaches_actual_building_solve_and_export(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog({"mixed_struts": False})
        try:
            table = dialog.component_editor.wire_table
            dialog.component_editor.wire_stiffness_modes[1].setCurrentIndex(1)
            for col, value in ((2, "4000"), (4, "2"), (5, "12"), (7, "SYNTHETIC SOFTWARE TEST EA ONLY")):
                table.setItem(1, col, QTableWidgetItem(value))
            for combo in dialog.brace_combos.values():
                combo.setCurrentIndex(combo.findText("10 mm WIRE ROPE"))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            physical = [m for m in dialog.result["cases"][0]["members"] if m["tension_only"]]
            self.assertTrue(physical)
            for member in physical:
                self.assertAlmostEqual(member["area_mm2"]*member["modulus_mpa"], 4000000.)
                self.assertEqual(dialog.result["section_checks"][member["id"]]["case_checks"][0]["capacity_kn"], 35.92)
            json.dumps(dialog.result, allow_nan=False)
        finally:
            dialog.reject()

    def test_interim_wire_stiffness_actual_model_export_roundtrip_and_invalidation(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog({'mixed_struts': False})
        try:
            editor = dialog.component_editor
            for col, value in ((4, '2'), (5, '12')):
                editor.wire_table.setItem(1, col, QTableWidgetItem(value))
            for combo in dialog.brace_combos.values():
                combo.setCurrentIndex(combo.findText('10 mm WIRE ROPE'))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            physical = [m for m in dialog.result['cases'][0]['members'] if m['tension_only']]
            self.assertTrue(physical)
            for member in physical:
                self.assertAlmostEqual(member['modulus_mpa'], 200000.)
                self.assertAlmostEqual(member['area_mm2'], math.pi*100/4)
                check = dialog.result['section_checks'][member['id']]['case_checks'][0]
                self.assertEqual(check['stiffness_basis'], WIRE_ASSUMED_STIFFNESS)
                self.assertEqual(check['capacity_kn'], 35.92)
            self.assertIn('WIRE-ROPE STIFFNESS BASIS', dialog.report_text)
            self.assertIn('E = 200 GPa', dialog.report_text)
            self.assertIn('interim rope stiffness', dialog.status_label.text())
            self.assertTrue(dialog.result['design']['assumed_wire_stiffness_groups'])
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(saved, restored.snapshot())
                self.assertEqual(restored.component_editor.wire_candidates()[1]['rating']['stiffness_basis'], WIRE_ASSUMED_STIFFNESS)
            finally:
                restored.reject()
            json.dumps(dialog.result, allow_nan=False)
            editor.wire_stiffness_modes[1].setCurrentIndex(1)
            self.assertIsNone(dialog.result)
            editor.wire_table.setItem(1, 2, QTableWidgetItem('4000'))
            editor.wire_table.setItem(1, 7, QTableWidgetItem('SYNTHETIC DECLARED EA'))
            self.assertEqual(editor.wire_candidates()[1]['rating']['ea_kn'], 4000.)
            self.assertNotIn('modulus_mpa', editor.wire_candidates()[1]['rating'])
            saved_declared = dialog.snapshot()
            restored = self.dialog(saved_declared)
            try:
                self.assertEqual(saved_declared, restored.snapshot())
                self.assertEqual(restored.component_editor.wire_candidates()[1]['rating']['ea_kn'], 4000.)
            finally:
                restored.reject()
        finally:
            dialog.reject()

    def test_loaded_rope_prices_roundtrip_export_and_clearing_invalidates_results(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog({'mixed_struts': False, 'wire_rope_ratings': {'12': {'price_per_m': None}}})
        try:
            editor = dialog.component_editor
            for combo in dialog.brace_combos.values():
                combo.setCurrentIndex(combo.findText('12 mm WIRE ROPE'))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            costs = [c for c in dialog.result['design']['cost_items'] if c.get('price_per_m')]
            self.assertTrue(costs)
            for cost in costs:
                self.assertEqual(cost['price_per_m'], 1.9)
                self.assertEqual(cost['fittings_per_run'], 9.62)
                self.assertEqual(cost['turnbuckle'], 'M16')
            self.assertIn('grips and other termination hardware excluded', dialog.report_text)
            self.assertIn('Upper end of supplied $1.80-$1.90/m range', dialog.report_text)
            for col, value in ((4, '2.25'), (5, '15')):
                editor.wire_table.setItem(2, col, QTableWidgetItem(value))
            self.assertIsNone(dialog.result)
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(saved, restored.snapshot())
                row = restored.component_editor.wire_candidates()[2]['rating']
                self.assertEqual((row['price_per_m'], row['fittings_per_run']), (2.25, 15.))
                self.assertIn('override', row['rope_price_source'])
            finally:
                restored.reject()
            editor.wire_table.setItem(2, 4, QTableWidgetItem(''))
            self.assertIsNone(editor.wire_candidates()[2]['rating']['price_per_m'])
            cleared = self.dialog(dialog.snapshot())
            try:
                self.assertIsNone(cleared.component_editor.wire_candidates()[2]['rating']['price_per_m'])
            finally:
                cleared.reject()
        finally:
            dialog.reject()

    def test_mixed_fixture_reanalyses_locations_and_preserves_data_roundtrip(self):
        dialog = self.dialog()
        try:
            self.fill_fixture_ratings(dialog)
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            plan = dialog.selections()["strut"]
            self.assertIsInstance(plan, StrutPlan)
            forms = {a.arrangement for a in plan.members.values()}
            self.assertIn("existing_purlins", forms)
            self.assertGreater(len(forms), 1)
            for m in dialog.result["cases"][0]["members"]:
                if m["kind"] == "longitudinal_strut":
                    self.assertAlmostEqual(m["area_mm2"], plan.members[m["id"]].A)
            rerun = solve_selection(dialog.current_context(), dialog.selections(), dialog.criteria(), dialog.context["cost_rates"])
            self.assertTrue(rerun["design"]["passes"])
            json.dumps(dialog.result, allow_nan=False)
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(restored.component_editor.snapshot(), dialog.component_editor.snapshot())
            finally:
                restored.reject()
            # Changed roof bending invalidates added-ply ratings, while pure
            # axial ratings remain keyed to the unchanged section/restraints.
            context = copy.deepcopy(dialog.context)
            for data in context["installed_purlins"].values():
                data["bending_key"] = "changed roof bending"
            from bracing_analysis_ui import BracingAnalysisDialog
            stale = BracingAnalysisDialog(context, self.window.catalog, saved)
            try:
                self.assertTrue(all(r["extra_compression_kn"] is None for r in stale.component_editor.snapshot()["purlin_axial_ratings"].values()))
            finally:
                stale.reject()
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
