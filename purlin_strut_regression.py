"""Independent single-span manufacturer-table and added-strut checks."""
import json
from pathlib import Path
import unittest

from bracing_analysis import AnalysisError
from purlin_strut_tables import snapshot, table_row, single_span_capacities
from purlin_shared_design import location_data, solve_shared, shared_check, ARRANGEMENT, PAIR_ARRANGEMENT
from purlin_combined import properties, same_profile_size
from bracing_components import location_options, assembly_assessment
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
    def pair_fixture(self, count=1):
        assemblies = fixture(count=count)
        assemblies[0] = next(o for o in location_options(assemblies[0].location, [])
            if o.arrangement == PAIR_ARRANGEMENT and o.components[-1]['data']['section'] == 'C/Z 250 15')
        return assemblies

    def test_added_pair_thirds_independent_moments_load_conservation_and_selfweight(self):
        assemblies = self.pair_fixture()
        pair = assemblies[0]
        self.assertEqual(len(pair.components), 3)
        self.assertAlmostEqual(pair.A, 3*638.)
        run = solve_shared(assemblies, [8000.,8000.], 0)
        out = run['cases'][0]
        self.assertAlmostEqual(out['bays'][0]['roof']['positive_moment_knm'], (1+1/3)*8000**2/16/1e6)
        self.assertAlmostEqual(out['bays'][0]['added']['negative_moment_knm'], (1/3)*8000**2/8/1e6)
        self.assertAlmostEqual(out['applied_load_n'], -16000.)
        self.assertEqual(out['loads']['added_count_by_bay'], [2,0])
        self.assertAlmostEqual(out['loads']['added_n_mm_by_bay'][0], -1/3)
        own = 638.*7.85e-6*9.80665
        self.assertAlmostEqual(run['cases'][1]['applied_load_n'], 16000+2*1.2*own*8000)
        for case in run['cases']:
            self.assertLess(abs(case['applied_load_n']+case['support_reaction_n']), 1e-5)
        for component in pair.components:
            row = shared_check(run, {'line':'0','bay':0,'error':None}, component, -3., 'ULS', 'ULS',150.,DesignCriteria())
            self.assertAlmostEqual(row['roof_load_fraction'], 1/3)
            self.assertEqual(row['added_purlin_count'], 2)

    def test_apex_pair_plus_two_additions_shares_in_quarters(self):
        assemblies = self.pair_fixture(count=2)
        run = solve_shared(assemblies, [8000.,8000.], 0)
        allocation = run['load_allocation_by_bay']['0']
        self.assertEqual((allocation['existing_count'], allocation['added_count']), (2,2))
        self.assertAlmostEqual(allocation['existing_group_fraction'], .5)
        self.assertAlmostEqual(allocation['each_added_fraction'], .25)
        for component in assemblies[0].components:
            row = shared_check(run, {'line':'0','bay':0,'error':None}, component, -3., 'ULS','ULS',150.,DesignCriteria())
            self.assertAlmostEqual(row['roof_load_fraction'], .25)
        self.assertAlmostEqual(run['cases'][0]['applied_load_n'], -32000.)

    def test_pair_passes_when_single_fails_and_costs_two_physical_additions(self):
        from bracing_components_regression import fixture as axial_fixture
        from bracing_design import member_assessment, group_cost
        rates = {'purlin':1.9, 'shs':5.12, 'ub':3.7, 'wb':4.48}
        results = []
        for assemblies in (fixture(), self.pair_fixture()):
            run = solve_shared(assemblies, [8000.,8000.], 0)
            result = {**axial_fixture(-120000.), 'purlin_beams': {'runs': {'0':run},
                'members': {assemblies[0].location['id']: {'line':'0','bay':0,'error':None}}}}
            checked = assembly_assessment(result,0,assemblies[0],DesignCriteria(),member_assessment)
            cost = group_cost(result,'strut',assemblies[0],rates)['total']
            results.append((checked,cost))
        self.assertFalse(results[0][0]['passes'])
        self.assertTrue(results[1][0]['passes'])
        forces = [r['axial_kn'] for r in results[1][0]['case_checks'] if r['case']=='ULS']
        self.assertEqual(len(forces), 3)
        for force in forces:self.assertAlmostEqual(force, -40.)
        self.assertAlmostEqual(results[1][1], 2*results[0][1])

    def test_automatic_sizing_adopts_added_pair_and_exactly_reanalyses_it(self):
        # Synthetic ULS stress fixture, not a wind-design/combination example.
        import copy
        from types import SimpleNamespace
        from main import ShapeCatalog, END_WALL_BRACING_SIZE_OPTIONS
        from bracing_design_regression import DesignMechanicsTests
        from bracing_components import StrutPlan
        from bracing_design import auto_size, solve_selection
        from bracing_schedule import rod_options
        catalog = ShapeCatalog('Shape_Catalog.xlsx')
        context, selected, pools = DesignMechanicsTests.small_context(SimpleNamespace(catalog=catalog))
        for case in context['cases']:
            if case['level'] == 'ULS':
                case['front_kpa'] *= 80
                case['back_kpa'] *= 80
        members = {}
        for line,x in enumerate(context['layout'].geometry.roof_stations):
            loc = copy.deepcopy(fixture()[0].location)
            loc.update(id=f'strut/bay/0/line/{line}',line=line,x_mm=x)
            loc['purlin']['axial_rating']['compression_method'] = 'lysaght_1999'
            loc['purlin'].update(bending_ratio=1/1.57,service_ratio=.8/1.2,bending_span_mm=8000.)
            members[loc['id']] = location_options(loc,[])[0]
        selected['strut'] = StrutPlan(members,selected['strut'])
        pools['strut'] = catalog.shs_profiles()
        braces = [*rod_options(),*[{'label':label,'area_mm2':area,'description':text}
                   for label,area,text in END_WALL_BRACING_SIZE_OPTIONS if label.startswith('EA')]]
        for group in ('roof_brace','wall_brace','end_brace'):pools[group] = braces
        rates = {'shs':5.12,'ub':3.7,'wb':4.48,'purlin':1.9}
        result,chosen = auto_size(context,selected,pools,DesignCriteria(),rates)
        self.assertTrue(result['design']['passes'])
        self.assertTrue(any(a.arrangement == PAIR_ARRANGEMENT for a in chosen['strut'].members.values()))
        rerun = solve_selection(context,chosen,DesignCriteria(),rates)
        self.assertTrue(rerun['design']['passes'])
        self.assertEqual({k:v['ratio'] for k,v in result['section_checks'].items()},
                         {k:v['ratio'] for k,v in rerun['section_checks'].items()})

    def test_added_profiles_match_roof_size_and_stale_frozen_mismatch_cannot_pass(self):
        import copy
        assemblies = fixture()
        candidates = assemblies[0].location['added_purlins']
        self.assertTrue(candidates)
        self.assertTrue(all(same_profile_size(c['section'],'C/Z 250 15') for c in candidates))
        self.assertGreater(len({c['section'] for c in candidates}), 1)  # thickness still varies
        bad = copy.deepcopy(candidates[0]);bad['section'] = 'C/Z 200 19'
        location = {**assemblies[0].location, 'added_purlins':[bad]}
        self.assertFalse(any(o.arrangement in {ARRANGEMENT,PAIR_ARRANGEMENT} for o in location_options(location,[])))
        assemblies[0].components[-1]['data']['section'] = 'C/Z 200 19'
        with self.assertRaisesRegex(AnalysisError, 'match the installed roof profile'):
            solve_shared(assemblies,[8000.,8000.],0)

    def cost_trade_fixture(self):
        import copy
        from types import SimpleNamespace
        from main import ShapeCatalog
        from bracing_design_regression import DesignMechanicsTests, rod
        from bracing_components import StrutPlan
        catalog = ShapeCatalog('Shape_Catalog.xlsx')
        context, selected, pools = DesignMechanicsTests.small_context(SimpleNamespace(catalog=catalog))
        members = {}
        large = catalog.profile_by_name['250*12.5 SHS']
        for line,x in enumerate(context['layout'].geometry.roof_stations):
            loc = copy.deepcopy(fixture()[0].location)
            loc.update(id=f'strut/bay/0/line/{line}',line=line,x_mm=x)
            loc['purlin']['axial_rating']['compression_method'] = 'lysaght_1999'
            loc['purlin'].update(bending_ratio=1/1.57,service_ratio=.8/1.2,bending_span_mm=8000.)
            members[loc['id']] = next(o for o in location_options(loc,[large]) if o.arrangement=='additional_steel')
        selected['strut'] = StrutPlan(members,large)
        pools['strut'] = [large]
        for group in ('roof_brace','wall_brace'):
            selected[group] = rod(16)
            pools[group] = [rod(16),rod(20)]
        rates = {'shs':5.12,'ub':3.7,'wb':4.48,'purlin':1.9}
        return context,selected,pools,rates

    def test_checked_strut_ceiling_does_not_stop_other_group_stiffening(self):
        from unittest import mock
        from bracing_design import auto_size,solve_selection,member_assessment,candidate_stiffness
        c,s,p,rates = self.cost_trade_fixture()
        load = 1.25*2*candidate_stiffness(s['roof_brace'])
        def analysed(context,chosen,criteria,rates):
            result = solve_selection(context,chosen,criteria,rates)
            ratio = load/sum(candidate_stiffness(chosen[g]) for g in ('roof_brace','wall_brace'))
            sls = result['design']['global_sls']
            sls.update(ratio=ratio,movement_mm=ratio*sls['limit_mm'])
            result['design']['passes'] = result['design']['member_checks_pass'] and ratio<=1 and result['design']['taut_tangent_verified']
            return result
        def assessed(result,index,option,criteria):
            checked = member_assessment(result,index,option,criteria)
            # No local capacity rating for the large steel addition in this
            # search pool; the cold-formed alternatives retain real checks.
            if getattr(option,'arrangement',None)=='additional_steel':
                return {**checked,'passes':False,'ratio':None,'case_checks':[
                    {'case':'ULS','mode':'Fixture unavailable steel rating','ratio':None}]}
            return checked
        with mock.patch('bracing_design.solve_selection',side_effect=analysed) as solver, \
             mock.patch('bracing_design.member_assessment',side_effect=assessed):
            result,chosen = auto_size(c,s,p,DesignCriteria(),rates,max_solves=8)
        self.assertTrue(result['design']['passes'])
        self.assertTrue(result['sizing_search']['locally_checked_strut_floor_limits'])
        self.assertTrue(all(a.arrangement!='additional_steel' for a in chosen['strut'].members.values()))
        self.assertEqual(result['sizing_search']['exact_solves'],solver.call_count)
        self.assertLessEqual(solver.call_count,8)
        self.assertTrue(analysed(c,chosen,DesignCriteria(),rates)['design']['passes'])

    def test_coupled_cost_trade_rejects_movement_failure_and_adopts_exact_pass(self):
        from unittest import mock
        from bracing_design import solve_selection,candidate_stiffness
        from bracing_mixed_sizing import reduce_strut_brace_cost
        c,s,p,rates = self.cost_trade_fixture()
        load = 1.8*2*candidate_stiffness(s['roof_brace'])
        def analysed(context,chosen,criteria,rates):
            result = solve_selection(context,chosen,criteria,rates)
            # Independent parallel-spring oracle exercises a genuine coupled
            # cost trade: cold-formed struts alone lose stiffness; larger braces
            # recover it. Member checks and beam sharing remain the real solver.
            ea = sum(candidate_stiffness(chosen[g]) for g in ('roof_brace','wall_brace'))
            ea += .05*sum(a.A*200000 for a in chosen['strut'].members.values())
            ratio = load/ea
            sls = result['design']['global_sls']
            sls.update(ratio=ratio,movement_mm=ratio*sls['limit_mm'])
            result['design']['passes'] = result['design']['member_checks_pass'] and ratio<=1 and result['design']['taut_tangent_verified']
            return result
        initial = analysed(c,s,DesignCriteria(),rates)
        self.assertTrue(initial['design']['passes'])
        with mock.patch('bracing_design.solve_selection',side_effect=analysed) as solver:
            best,trials,used = reduce_strut_brace_cost(c,(initial,s),p,DesignCriteria(),rates,4)
        self.assertEqual(used,solver.call_count)
        self.assertLessEqual(used,4)
        self.assertTrue(any(not t.get('passes',False) and not t['adopted'] for t in trials))
        self.assertTrue(any(t['adopted'] for t in trials))
        self.assertTrue(best[0]['design']['passes'])
        self.assertLess(best[0]['design']['estimated_cost'],initial['design']['estimated_cost'])
        self.assertTrue(analysed(c,best[1],DesignCriteria(),rates)['design']['passes'])

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

    def test_added_pair_ui_roundtrip_and_legacy_profile_migration(self):
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        context = prepare_context(self.window)
        saved = {'strut_members': {'strut/bay/0/line/1': {'existing_count':1,
                 'arrangement':PAIR_ARRANGEMENT, 'steel_section':'100*4 SHS', 'added_purlin_section':'C/Z 200 12'}}}
        d = BracingAnalysisDialog(context,self.window.catalog,saved)
        try:
            editor = d.component_editor
            self.assertIn('replaced',editor.added_purlin_combos['strut/bay/0/line/1'].toolTip())
            d.run_analysis()
            self.assertIsNotNone(d.result,d.status_label.text())
            pair = d.selections()['strut'].members['strut/bay/0/line/1']
            self.assertEqual(sum(not c['existing'] for c in pair.components),2)
            self.assertTrue(all(same_profile_size(c['data']['section'],pair.location['purlin']['section']) for c in pair.components))
            self.assertIn('2 added single-span ply/plies each carry 33.33%',d.report_text)
            payload = d.snapshot()
            restored = BracingAnalysisDialog(context,self.window.catalog,payload)
            try:self.assertEqual(restored.snapshot(),payload)
            finally:restored.reject()
            json.dumps(d.result,allow_nan=False)
        finally:d.reject()

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
