"""Project rules, family-first cost ranking, and actual analysis acceptance.

Rope fixtures use SYNTHETIC data except the named project-default pricing test.
"""
import copy
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from bracing_analysis import AnalysisError
from bracing_components import wire_options
from bracing_design import DesignCriteria, auto_size, solve_selection
from bracing_policy import candidate_rank, eligible, evidence, family
from bracing_schedule import rod_options


def wire(price=100., ea=50000.):
    return wire_options({'16': {'ea_kn': ea, 'ea_source': 'SYNTHETIC SOFTWARE TEST EA',
                               'price_per_m': price, 'fittings_per_run': 10.}})[-1]


class PolicyMechanicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import ShapeCatalog
        cls.catalog = ShapeCatalog('Shape_Catalog.xlsx')

    def fixture(self, project='agricultural'):
        from bracing_design_regression import DesignMechanicsTests
        context, selected, pools = DesignMechanicsTests.small_context(SimpleNamespace(catalog=self.catalog))
        context['brace_policy'] = {'project_use': project}
        context['face_cladding'] = {face: True for face in ('left_wall','right_wall','front_end','back_end')}
        rod = rod_options()[1]
        for g in ('roof_brace','wall_brace','end_brace'):
            selected[g] = rod
            pools[g] = [rod, wire()]
        return context, selected, pools

    def test_preferences_and_unclad_group_rule_are_distinct(self):
        c,s,p = self.fixture()
        r,w = p['wall_brace']
        self.assertLess(candidate_rank(c,'roof_brace',w),candidate_rank(c,'roof_brace',r))
        c['brace_policy']['project_use']='commercial_industrial'
        self.assertLess(candidate_rank(c,'roof_brace',r),candidate_rank(c,'roof_brace',w))
        c['brace_policy']['project_use']='unspecified'
        self.assertEqual(candidate_rank(c,'roof_brace',r),candidate_rank(c,'roof_brace',w))
        c['face_cladding']['left_wall']=False
        self.assertTrue(eligible(c,'wall_brace',r))
        self.assertFalse(eligible(c,'wall_brace',w))
        self.assertFalse(eligible(c,'wall_brace',{'label':'EA 100x100x10'}))
        self.assertTrue(eligible(c,'roof_brace',w))
        self.assertEqual(evidence(c,s)['groups']['wall_brace']['rod_required_faces'],['left_wall'])
        c['face_cladding']['front_end']=False
        c['layout'].toggle('front_end/panel/0','pinned')
        self.assertFalse(eligible(c,'end_brace',w))

    def test_agricultural_prefers_passing_wire_even_if_initial_rods_are_cheaper(self):
        c,s,p = self.fixture()
        rates={'shs':3.,'ub':4.,'wb':5.}
        base=solve_selection(c,s,DesignCriteria(),rates)
        result,chosen=auto_size(c,s,p,DesignCriteria(),rates)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['roof_brace']),'wire')
        self.assertEqual(family(chosen['wall_brace']),'wire')
        self.assertGreater(result['design']['estimated_cost'],base['design']['estimated_cost'])
        self.assertTrue(solve_selection(c,chosen,DesignCriteria(),rates)['design']['passes'])
        json.dumps(result,allow_nan=False)

    def test_project_default_rope_prices_enable_checked_agricultural_sizing(self):
        c,s,p = self.fixture()
        for g in ('roof_brace', 'wall_brace', 'end_brace'):
            p[g] = [rod_options()[1], *wire_options()]
        result, chosen = auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
        self.assertTrue(result['design']['passes'])
        for g in ('roof_brace', 'wall_brace'):
            self.assertEqual(family(chosen[g]), 'wire')
            self.assertIn(chosen[g]['rating']['price_per_m'], (1.35, 1.75, 1.90, 2.40, 3.50))
        self.assertTrue(solve_selection(c,chosen,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})['design']['passes'])

    def test_commercial_prefers_rods_even_when_wire_is_cheaper(self):
        c,s,p = self.fixture('commercial_industrial')
        for g in ('roof_brace','wall_brace','end_brace'):
            s[g]=wire(price=.001)
            p[g]=[rod_options()[1],s[g]]
        result,chosen=auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['roof_brace']),'rod')
        self.assertEqual(family(chosen['wall_brace']),'rod')

    def test_manual_wire_on_unclad_wall_cannot_pass_and_sizing_replaces_it(self):
        c,s,p = self.fixture()
        c['face_cladding']['left_wall']=False
        s['wall_brace']=wire()
        rates={'shs':3.,'ub':4.,'wb':5.}
        result=solve_selection(c,s,DesignCriteria(),rates)
        self.assertFalse(result['design']['passes'])
        forbidden=[r for r in result['section_checks'].values() if 'policy_violation' in r]
        self.assertTrue(forbidden)
        self.assertTrue(all(r['ratio'] is None and 'rod required' in r['mode'] for r in forbidden))
        result,chosen=auto_size(c,s,p,DesignCriteria(),rates)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['wall_brace']),'rod')
        self.assertEqual(family(chosen['roof_brace']),'wire')

    def test_missing_wire_stiffness_or_prices_falls_back_without_inventing_data(self):
        for missing in ('ea_kn','price_per_m'):
            c,s,p = self.fixture()
            for g in ('roof_brace','wall_brace','end_brace'):
                p[g][1]['rating'][missing]=None
            result,chosen=auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
            self.assertTrue(result['design']['passes'])
            self.assertEqual(family(chosen['roof_brace']),'rod')
            self.assertEqual(family(chosen['wall_brace']),'rod')
            self.assertIsNone(p['roof_brace'][1]['rating'][missing])

    def test_preference_does_not_override_wire_strength_or_global_sls(self):
        c,s,p = self.fixture()
        for g in ('roof_brace','wall_brace','end_brace'):
            p[g][1]['rating']['design_tension_kn']=.001
            p[g][1]['rating']['source']='SYNTHETIC TINY CAPACITY'
        result,chosen=auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['roof_brace']),'rod')
        with self.assertRaises(AnalysisError):
            auto_size(c,s,p,replace(DesignCriteria(),sls_divisor=100000),{'shs':3.,'ub':4.,'wb':5.},max_solves=4)

    def test_failed_preferred_solve_retains_matching_verified_rods_and_evidence(self):
        from unittest.mock import patch
        c,s,p=self.fixture()
        real_solve=solve_selection
        def fail_wire(context,selected,criteria,rates):
            if family(selected['roof_brace'])=='wire':
                raise AnalysisError('SYNTHETIC FAILED PREFERRED SOLVE')
            return real_solve(context,selected,criteria,rates)
        with patch('bracing_design.solve_selection',side_effect=fail_wire):
            result,chosen=auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['roof_brace']),'rod')
        self.assertEqual(result['design_request']['sections']['roof_brace'],chosen['roof_brace']['label'])

    def test_soft_rope_falls_back_to_rods_when_movement_cannot_pass(self):
        c,s,p=self.fixture()
        for g in ('roof_brace','wall_brace','end_brace'):
            p[g][1]=wire(price=.001,ea=1.)
        result,chosen=auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['roof_brace']),'rod')
        self.assertLessEqual(result['design']['global_sls']['ratio'],1.)

    def test_mixed_failed_preferred_solve_keeps_sections_matching_evidence(self):
        from unittest.mock import patch
        from bracing_components import StrutPlan,StrutAssembly
        c,s,p=self.fixture()
        rates={'shs':3.,'ub':4.,'wb':5.}
        baseline=solve_selection(c,s,DesignCriteria(),rates)
        original=s['strut']
        members={m['id']:StrutAssembly(original.name,({'type':'steel','existing':False,'profile':original},),
                    'test_steel',{'id':m['id'],'line':int(m['id'].split('/')[-1])})
                 for m in baseline['cases'][0]['members'] if m['kind']=='longitudinal_strut'}
        s['strut']=StrutPlan(members,original)
        real_solve=solve_selection
        def fail_wire(context,selected,criteria,costs):
            if family(selected['roof_brace'])=='wire':
                raise AnalysisError('SYNTHETIC FAILED PREFERRED SOLVE')
            return real_solve(context,selected,criteria,costs)
        with patch('bracing_design.solve_selection',side_effect=fail_wire), patch('bracing_mixed_sizing.location_options',side_effect=lambda loc,profiles:[members[loc['id']]]):
            result,chosen=auto_size(c,s,p,DesignCriteria(),rates)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(family(chosen['roof_brace']),'rod')
        self.assertEqual(result['design_request']['sections']['roof_brace'],chosen['roof_brace']['label'])


class PolicyWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bracing_components_regression import ComponentWorkspaceTests
        ComponentWorkspaceTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from bracing_components_regression import ComponentWorkspaceTests
        ComponentWorkspaceTests.tearDownClass.__func__(cls)

    def test_policy_and_confirmed_product_roundtrip_and_invalidate(self):
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        d=BracingAnalysisDialog(prepare_context(self.window),self.window.catalog)
        try:
            self.assertEqual(d.component_editor.project_use.currentData(),'unspecified')
            self.assertEqual(set(d.current_context()['face_cladding']),{'left_wall','right_wall','front_end','back_end'})
            d.component_editor.project_use.setCurrentIndex(d.component_editor.project_use.findData('agricultural'))
            self.assertEqual(d.current_context()['brace_policy']['project_use'],'agricultural')
            self.assertIn('preference',d.auto_button.text())
            self.assertTrue(all(w['rating']['construction']=='Austlift 6x24 fibre core G1570' for w in d.component_editor.wire_candidates()))
            self.assertTrue(all(w['rating'].get('ea_kn', 0) > 0 for w in d.component_editor.wire_candidates()))
            self.assertTrue(all(w['rating']['stiffness_basis']=='assumed_solid_steel_200gpa' for w in d.component_editor.wire_candidates()))
            d.run_analysis()
            self.assertIsNotNone(d.result,d.status_label.text())
            self.assertIn('PROJECT BRACING MATERIAL POLICY',d.report_text)
            saved=d.snapshot()
            restored=BracingAnalysisDialog(d.context,d.catalog,saved)
            try:self.assertEqual(saved,restored.snapshot())
            finally:restored.reject()
            d.component_editor.project_use.setCurrentIndex(d.component_editor.project_use.findData('commercial_industrial'))
            self.assertIsNone(d.result)
            self.assertFalse(d.export_json_button.isEnabled())
        finally:d.reject()

    def test_mixed_purlin_sizing_honours_rope_preference_with_synthetic_ratings(self):
        from PySide6.QtWidgets import QTableWidgetItem
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        d=BracingAnalysisDialog(prepare_context(self.window),self.window.catalog)
        try:
            d.component_editor.project_use.setCurrentIndex(d.component_editor.project_use.findData('agricultural'))
            table=d.component_editor.wire_table
            row=table.rowCount()-1
            d.component_editor.wire_stiffness_modes[row].setCurrentIndex(1)
            for column,value in ((2,'50000'),(4,'100'),(5,'10'),(7,'SYNTHETIC SOFTWARE TEST EA')):
                table.setItem(row,column,QTableWidgetItem(value))
            d.run_auto_size()
            self.assertIsNotNone(d.result,d.status_label.text())
            self.assertTrue(d.result['design']['passes'],d.status_label.text())
            self.assertEqual(family(d.selections()['roof_brace']),'wire')
            self.assertEqual(family(d.selections()['wall_brace']),'wire')
            self.assertEqual(d.result['brace_policy']['project_use'],'agricultural')
            self.assertEqual(d.result['design_request']['brace_policy'],d.result['brace_policy'])
            repeat=solve_selection(d.current_context(),d.selections(),d.criteria(),d.context['cost_rates'])
            self.assertTrue(repeat['design']['passes'])
            self.assertEqual(repeat['design']['estimated_cost'],d.result['design']['estimated_cost'])
            json.dumps(d.result,allow_nan=False)
        finally:d.reject()


if __name__=='__main__':
    unittest.main(verbosity=2)
