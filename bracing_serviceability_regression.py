"""Strength-only sizing retains SLS evidence without accepting serviceability."""
import copy
from dataclasses import replace
import itertools
import json
import os
import tempfile
from types import SimpleNamespace
import unittest

from bracing_analysis import AnalysisError
from bracing_components import assembly_assessment,location_options
from bracing_design import DesignCriteria,member_assessment,solve_selection,auto_size,attach_design
import bracing_design_regression as design_fixtures
from bracing_design_regression import member_fixture
from bracing_components_regression import location,fixture
from bracing_sizing_diagnostics import result_summary


class StrengthModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import ShapeCatalog
        cls.catalog=ShapeCatalog('Shape_Catalog.xlsx')

    def test_default_validation_and_generic_local_deflection_scope(self):
        self.assertTrue(DesignCriteria().include_serviceability)
        for invalid in ('false',0,1,None):
            with self.assertRaises(AnalysisError):replace(DesignCriteria(),include_serviceability=invalid).validate()
        criteria=DesignCriteria(sls_divisor=100000.)
        profile=self.catalog.profile_by_name['100*4 SHS']
        result=member_fixture('longitudinal_strut',8000.,-1000.)
        full=member_assessment(result,0,profile,criteria)
        strength=member_assessment(result,0,profile,replace(criteria,include_serviceability=False))
        self.assertFalse(full['passes']);self.assertTrue(strength['passes'])
        sls=next(r for r in strength['case_checks'] if r['level']=='SLS')
        self.assertGreater(sls['ratio'],1.);self.assertFalse(sls['included_in_design'])
        self.assertGreater(sls['deflection_mm'],sls['limit_mm'])
        self.assertEqual(strength['case'],'ULS')

    def test_purlin_and_added_ply_exclude_only_sls_not_missing_strength_ratings(self):
        loc=location();loc['purlin']['service_ratio']=3.
        loc['purlin']['axial_rating'].pop('extra_sls_deflection_mm')
        assembly=next(o for o in location_options(loc,[]) if o.arrangement=='additional_purlin')
        criteria=DesignCriteria(include_serviceability=False)
        checked=assembly_assessment(fixture(-20000.),0,assembly,criteria,member_assessment)
        self.assertTrue(checked['passes'])
        self.assertTrue(any(r['ratio'] is None and not r['included_in_design'] for r in checked['case_checks']))
        loc['purlin']['axial_rating'].pop('extra_compression_kn')
        checked=assembly_assessment(fixture(-20000.),0,assembly,criteria,member_assessment)
        self.assertFalse(checked['passes']);self.assertIsNone(checked['ratio'])

    def test_grouped_strength_sizing_matches_exhaustive_catalogue_despite_sls_failure(self):
        c,s,p=design_fixtures.DesignMechanicsTests.small_context(SimpleNamespace(catalog=self.catalog))
        rates={'shs':3.,'ub':4.,'wb':5.};criteria=DesignCriteria(sls_divisor=100000.,include_serviceability=False)
        costs=[]
        for strut,roof,wall in itertools.product(p['strut'],p['roof_brace'],p['wall_brace']):
            r=solve_selection(c,{**s,'strut':strut,'roof_brace':roof,'wall_brace':wall},criteria,rates)
            if r['design']['passes']:costs.append(r['design']['estimated_cost'])
        r,chosen=auto_size(c,s,p,criteria,rates,max_solves=8)
        self.assertTrue(r['design']['passes']);self.assertFalse(r['design']['serviceability_passes'])
        self.assertGreater(r['design']['global_sls']['ratio'],1.)
        self.assertAlmostEqual(r['design']['estimated_cost'],min(costs))
        self.assertTrue(solve_selection(c,chosen,criteria,rates)['design']['passes'])
        self.assertIn('REPORT ONLY',result_summary(r)['text'])
        self.assertEqual(result_summary(r)['member_counts']['Local deflection'],0)
        self.assertIn('STRENGTH ONLY',result_summary(r)['text'])
        with self.assertRaises(AnalysisError):auto_size(c,s,p,replace(criteria,include_serviceability=True),rates,max_solves=4)
        json.dumps(r,allow_nan=False)

    def test_strength_mode_still_rejects_strength_failure_instability_and_material_rule(self):
        from bracing_components import wire_options
        c,s,p=design_fixtures.DesignMechanicsTests.small_context(SimpleNamespace(catalog=self.catalog))
        criteria=DesignCriteria(include_serviceability=False);rates={'shs':3.,'ub':4.,'wb':5.}
        r=solve_selection(c,s,criteria,rates)
        r['cases'][0]['tangent_status']='neutral_rods_required_stability_unverified'
        self.assertFalse(attach_design(r,s,criteria,rates,c)['design']['passes'])
        c['face_cladding']={'left_wall':False,'right_wall':True}
        selected={**s,'wall_brace':wire_options()[-1]}
        self.assertFalse(solve_selection(c,selected,criteria,rates)['design']['passes'])
        for case in c['cases']:
            if case['level']=='ULS':case['front_kpa']*=1000.;case['back_kpa']*=1000.
        self.assertFalse(solve_selection(c,s,criteria,rates)['design']['passes'])

    def test_mixed_purlin_sizing_reuses_existing_members_without_sls_growth(self):
        from purlin_strut_regression import SharedMechanicsTests
        c,s,p,rates=SharedMechanicsTests().cost_trade_fixture()
        criteria=DesignCriteria(sls_divisor=100000.,include_serviceability=False)
        r,chosen=auto_size(c,s,p,criteria,rates,max_solves=8)
        self.assertTrue(r['design']['passes'])
        self.assertFalse(r['design']['serviceability_passes'])
        self.assertTrue(any(a.arrangement=='existing_purlins' for a in chosen['strut'].members.values()))
        self.assertTrue(all(not row['included_in_design'] for check in r['section_checks'].values()
                            for row in check['case_checks'] if row.get('level')=='SLS'))
        self.assertTrue(solve_selection(c,chosen,criteria,rates)['design']['passes'])


class StrengthModeWorkspaceTests(unittest.TestCase):
    def test_toggle_invalidates_restores_and_exports_the_selected_design_scope(self):
        os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PORTALCALC_LICENSE_ENFORCED']='0'
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        from bracing_analysis_desktop import prepare_context
        from bracing_analysis_regression import fixed_layout
        from bracing_workspace_ui import BracingAnalysisDialog
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as settings:
            os.environ['APPDATA']=settings
            w=MainWindow();w.confirm_on_close=False
            try:
                w.bracing_location_layout=fixed_layout(w.current_bracing_layout_geometry()).to_dict()
                w.generate_and_solve();context=prepare_context(w)
                d=BracingAnalysisDialog(context,w.catalog,{'mixed_struts':False})
                try:
                    self.assertTrue(d.serviceability_check.isChecked())
                    d.strut_combo.setCurrentIndex(d.strut_combo.findData('250*12.5 SHS'))
                    d.post_combo.setCurrentIndex(d.post_combo.findData(max(d.post_profiles,key=lambda p:p.A).name))
                    d.criteria_inputs['sls_divisor'].setValue(2000.)
                    d.run_analysis();self.assertIsNotNone(d.result)
                    self.assertFalse(d.result['design']['passes'])
                    d.serviceability_check.setChecked(False)
                    self.assertIsNone(d.result);self.assertFalse(d.export_json_button.isEnabled())
                    d.run_analysis();self.assertTrue(d.result['design']['passes'])
                    self.assertIn('serviceability excluded',d.status_label.text())
                    self.assertIn('Design basis: Strength only',d.report_text)
                    self.assertIn('REPORT ONLY',d.result_summary_label.text())
                    saved=d.snapshot();cache=d.calculation_cache()
                    self.assertFalse(saved['criteria']['include_serviceability'])
                    restored=BracingAnalysisDialog(context,w.catalog,saved,cached_calculation=cache)
                    try:
                        self.assertIsNotNone(restored.result)
                        self.assertFalse(restored.serviceability_check.isChecked())
                    finally:restored.reject()
                    changed=copy.deepcopy(saved);changed['criteria']['include_serviceability']=True
                    restored=BracingAnalysisDialog(context,w.catalog,changed,cached_calculation=cache)
                    self.assertIsNone(restored.result);restored.reject()
                finally:d.reject()
            finally:w.close()


if __name__=='__main__':unittest.main(verbosity=2)
