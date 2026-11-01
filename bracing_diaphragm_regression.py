"""Independent diaphragm stiffness, SLS scope, recovery and saved UI checks."""
import copy
from dataclasses import asdict
import json
import math
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from bracing_analysis import AnalysisError, AxialMember, AxialModel3D, PortalReduction, build_braced_building
from bracing_analysis_desktop import analyse_context, prepare_context, text_report
from bracing_analysis_regression import frame, fixed_layout
from bracing_design import DesignCriteria, attach_design, auto_size, solve_selection
import bracing_design_regression as design_fixtures
from bracing_diaphragm import DiaphragmSettings, equivalent_diagonal, roof_diaphragm_model, recover_diaphragm
from bracing_layout import BracingLayout, LayoutGeometry


class DiaphragmMechanicsTests(unittest.TestCase):
    def test_load_change_preserves_common_cladding_stiffness_and_invalidates_strength_ratings(self):
        from bracing_components import rating_for_roof_duty
        previous={'section|bay_span_mm=8000|roof_duty_sha256=old':
                  {'beam_capacity_key':'old','cladding_coupling_kn_m_per_mm':.1,
                   'cladding_coupling_source':'Synthetic test stiffness',
                   'beam_roof_positive_moment_knm':100.,'extra_compression_kn':100.}}
        retained=rating_for_roof_duty(previous,'section|bay_span_mm=8000|roof_duty_sha256=new','new')
        self.assertEqual(retained['cladding_coupling_kn_m_per_mm'],.1)
        self.assertNotIn('beam_roof_positive_moment_knm',retained)
        self.assertNotIn('extra_compression_kn',retained)

    def test_equal_diagonal_tensions_are_panel_extension_not_net_shear(self):
        panel={'id':'panel','member_ids':['diag/0','diag/1'],'shear_projection':.8,
               'n_mm':8500.,'bay_length_mm':8500.,'length_mm':14790.,'shear_direction':'along_slope'}
        result={'members':[{'id':identifier,'panel_id':'panel','axial_n':35000.} for identifier in panel['member_ids']]}
        bare={'displacements_mm':[[0,0,0]],'tangent_status':'positive_with_taut_rods'}
        recovered=recover_diaphragm(result,[panel],0,bare)
        self.assertEqual(recovered['diaphragm_panels'][0]['net_shear_kn'],0.)
        self.assertEqual(recovered['diaphragm_panels'][0]['net_longitudinal_shear_kn'],0.)
        self.assertAlmostEqual(recovered['diaphragm_members'][0]['panel_shear_kn'],28.)

    def test_note_conversion_and_invalid_inputs(self):
        p = equivalent_diagonal(12104.,8500.,1.8)
        length = math.sqrt(12104.**2+8500.**2)
        area = length**3/(200*1.8*12104.**2)
        self.assertAlmostEqual(p['area_mm2'],area)
        self.assertAlmostEqual(p['ea_kn'],area*200)
        self.assertAlmostEqual(p['shear_stiffness_kn_per_mm'],1/1.8)
        self.assertAlmostEqual(p['equivalent_diameter_mm'],math.sqrt(4*area/math.pi))
        for bad in (0.,-1.,float('nan'),float('inf'),True,None):
            with self.subTest(bad=bad), self.assertRaises(AnalysisError):
                equivalent_diagonal(12104.,8500.,bad)
        for bad in ({'enabled':1},{'enabled':'false'},{'flexibility_mm_per_kn':'1.8'},
                    {'shear_direction':'unknown'}, {'unexpected':3}):
            with self.assertRaises(AnalysisError):DiaphragmSettings.from_dict(bad)
        self.assertFalse(DiaphragmSettings.from_dict().enabled)

    def test_square_panel_racking_matches_measured_flexibility_in_both_directions(self):
        # Independent benchmark: rigid in-plane edge collector, 1 kN shear.
        # One diagonal must carry sqrt(2) kN and deflection must be c*1 kN.
        g = LayoutGeometry.create(6000.,6000.,3000.,0.,'Skillion')
        layout = BracingLayout(g)
        model = AxialModel3D()
        points = [(0,0,3000),(6000,0,3000),(6000,6000,3000),(0,6000,3000)]
        lookup = {tuple(float(v) for v in p):model.add_node(p, fixed=i<2) for i,p in enumerate(points)}
        for i in (2,3):model.fixed.update((3*i+1,3*i+2))
        model.add_member(AxialMember('edge collector',2,3,1e8))
        enhanced, evidence = roof_diaphragm_model(model,layout,lookup,DiaphragmSettings(True,4.5))
        self.assertEqual(len(model.members),1)
        self.assertEqual(len(evidence['panels']),1)
        for sign in (1.,-1.):
            loads = np.zeros(12);loads[6]=loads[9]=sign*500.
            result = enhanced.solve(loads)
            average = (result['displacements_mm'][2][0]+result['displacements_mm'][3][0])/2
            self.assertAlmostEqual(average,sign*4.5,places=5)
            diagonals = result['members'][1:]
            active = [m for m in diagonals if m['axial_n']>1e-4]
            self.assertEqual(len(active),1)
            self.assertAlmostEqual(active[0]['axial_n']/1000,math.sqrt(2),places=6)
            self.assertTrue(all(m['axial_n']>=0 for m in diagonals))

    def test_whole_slope_panels_ignore_strip_mesh_keep_ridge_separate_and_follow_nonuniform_bays(self):
        for shape in ('Gable','Skillion'):
            g = LayoutGeometry.create(24000.,15000.,5000.,7.5,shape,
                bay_stations_mm=[0,8500,15000],end_wall_stations_mm=[0,6000,12000,18000,24000])
            layout = fixed_layout(g)
            portal = PortalReduction(frame(g),[(x,g.roof_z(x)) for x in g.roof_stations])
            model,_,_,lookup = build_braced_building(layout,portal,1500,200,200,200,1500)
            enhanced,e = roof_diaphragm_model(model,layout,lookup,DiaphragmSettings(True))
            self.assertEqual(len(enhanced.nodes),len(model.nodes))
            self.assertEqual(enhanced.fixed,model.fixed)
            self.assertEqual(len(e['panels']),4 if shape=='Gable' else 2)
            for p in e['panels']:
                self.assertEqual(p['bay_length_mm'],8500. if p['bay']==0 else 6500.)
                if shape=='Gable':self.assertAlmostEqual(p['slope_width_mm'],12000/math.cos(math.radians(7.5)))
            if shape=='Gable':
                for m in enhanced.members[len(model.members):]:
                    a,b = enhanced.nodes[m.start][0],enhanced.nodes[m.end][0]
                    self.assertTrue(max(a,b)<=12000 or min(a,b)>=12000)


class DiaphragmDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import ShapeCatalog
        cls.catalog=ShapeCatalog('Shape_Catalog.xlsx')

    def fixture(self):
        return design_fixtures.DesignMechanicsTests.small_context(SimpleNamespace(catalog=self.catalog))

    def test_sls_only_uls_cost_membership_unchanged_and_recovered_panel_shear(self):
        c,s,_ = self.fixture();criteria=DesignCriteria();rates={'shs':3.,'ub':4.,'wb':5.}
        bare = solve_selection(c,s,criteria,rates)
        context = {**c,'stressed_skin':asdict(DiaphragmSettings(True))}
        result = solve_selection(context,s,criteria,rates)
        self.assertEqual(result['member_envelopes'],bare['member_envelopes'])
        self.assertEqual(result['design']['estimated_cost'],bare['design']['estimated_cost'])
        self.assertEqual(set(result['section_checks']),set(bare['section_checks']))
        self.assertNotEqual(result['design_request_hash'],bare['design_request_hash'])
        self.assertEqual(result['stressed_skin']['bare_governing_sls']['resultant_mm'],bare['governing_sls_displacement']['resultant_mm'])
        changed=False
        for r,b in zip(result['cases'],bare['cases']):
            if r['case']['level']=='ULS':self.assertEqual(r,b);continue
            self.assertEqual(r['bare_sls']['displacements_mm'],b['displacements_mm'])
            changed |= not np.allclose(r['displacements_mm'],b['displacements_mm'])
            self.assertLess(np.max(np.abs(r['balance_force_error_n'])),1e-4)
            self.assertLess(np.max(np.abs(r['balance_moment_error_nmm'])),1e-2)
            self.assertEqual(len(r['members']),len(b['members']))
            for m in r['diaphragm_members']:
                p=next(p for p in result['stressed_skin']['panels'] if p['id']==m['panel_id'])
                self.assertAlmostEqual(m['panel_shear_kn'],m['axial_n']/1000*p['s_mm']/p['length_mm'])
                self.assertAlmostEqual(m['shear_flow_kn_per_m'],m['panel_shear_kn']/(p['n_mm']/1000))
        self.assertTrue(changed)
        # A neutral bare-model tangent must never be accepted through sheets.
        bad=copy.deepcopy(result)
        next(case for case in bad['cases'] if 'bare_sls' in case)['bare_sls']['tangent_status']='neutral_rods_required_stability_unverified'
        self.assertFalse(attach_design(bad,s,criteria,rates,context)['design']['taut_tangent_verified'])
        # Production text formatting needs desktop-only case metadata.
        for r in result['cases']:r['case'].update(source_direction='Any',cpi=.2)
        report=text_report(result)
        self.assertIn('SLS STRESSED-SKIN ROOF CONTRIBUTION',report)
        self.assertIn('Bare conventional maximum SLS resultant',report)
        self.assertIn('SLS EQUIVALENT DIAPHRAGMS',report)
        self.assertNotIn('No diaphragm or sheeting stiffness.',report)
        json.dumps(result,allow_nan=False)

    def test_explicit_direction_and_sensitivity_change_stiffness_not_uls(self):
        c,s,_=self.fixture()
        results=[]
        for direction,cvalue in (('along_slope',1.8),('along_slope',.72),('longitudinal',1.8)):
            context={**c,'stressed_skin':asdict(DiaphragmSettings(True,cvalue,direction))}
            results.append(analyse_context(context,s['strut'],s['end_post']))
        p,q,r=(result['stressed_skin']['panels'][0] for result in results)
        self.assertAlmostEqual(q['area_mm2']/p['area_mm2'],1.8/.72)
        self.assertAlmostEqual(r['implied_longitudinal_flexibility_mm_per_kn'],1.8)
        self.assertAlmostEqual(p['implied_longitudinal_flexibility_mm_per_kn'],1.8*p['s_mm']**2/p['n_mm']**2)
        self.assertTrue(all(result['cases'][0]==results[0]['cases'][0] for result in results))

    def test_auto_sizing_keeps_diaphragm_setting_in_exact_reanalysis(self):
        c,s,p=self.fixture();c={**c,'stressed_skin':asdict(DiaphragmSettings(True))}
        rates={'shs':3.,'ub':4.,'wb':5.};criteria=DesignCriteria()
        result,chosen=auto_size(c,s,p,criteria,rates,max_solves=8)
        rerun=solve_selection(c,chosen,criteria,rates)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(result['design_request_hash'],rerun['design_request_hash'])
        self.assertTrue(result['stressed_skin']['settings']['enabled'])

    def test_mixed_purlin_sizing_reanalyses_with_sls_diaphragms(self):
        from purlin_strut_regression import SharedMechanicsTests
        c,s,p,rates=SharedMechanicsTests().cost_trade_fixture()
        c={**c,'stressed_skin':asdict(DiaphragmSettings(True))}
        criteria=DesignCriteria()
        result,chosen=auto_size(c,s,p,criteria,rates,max_solves=8)
        rerun=solve_selection(c,chosen,criteria,rates)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(result['design_request_hash'],rerun['design_request_hash'])
        self.assertTrue(all('diaphragm_members' in case for case in result['cases'] if case['case']['level']=='SLS'))


class DiaphragmWorkspaceTests(unittest.TestCase):
    def test_saved_option_invalidates_exports_and_cache_restores_but_changed_c_rejects(self):
        os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PORTALCALC_LICENSE_ENFORCED']='0'
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
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
                    self.assertFalse(d.stressed_skin_check.isChecked())
                    d.run_analysis();self.assertIsNotNone(d.result)
                    d.stressed_skin_check.setChecked(True)
                    self.assertIsNone(d.result);self.assertFalse(d.export_json_button.isEnabled())
                    d.run_analysis();self.assertIsNotNone(d.result)
                    self.assertIn('SLS STRESSED-SKIN ROOF CONTRIBUTION',d.report_text)
                    self.assertIn('PRELIMINARY SLS stressed skin',d.result_summary_label.text())
                    sls=next(i for i,c in enumerate(d.result['cases']) if c['case']['level']=='SLS')
                    d.case_combo.setCurrentIndex(sls)
                    case=d.result['cases'][sls]
                    self.assertEqual(d.member_table.rowCount(),len(case['members'])+len(case['diaphragm_members']))
                    identifier=case['diaphragm_members'][0]['id']
                    d.inspect_member(identifier)
                    self.assertIn('Physical panel shear',d.member_detail.text())
                    d.case_combo.setCurrentIndex(0)
                    self.assertEqual(d.member_table.rowCount(),len(d.result['section_checks']))
                    d.case_combo.setCurrentIndex(sls)
                    d.inspect_member(identifier)
                    saved=d.snapshot();cache=d.calculation_cache()
                    with mock.patch('bracing_workspace_ui.solve_selection',side_effect=AssertionError('Restore without solving')):
                        reopened=BracingAnalysisDialog(context,w.catalog,saved,cached_calculation=cache)
                    try:
                        self.assertIsNotNone(reopened.result);self.assertTrue(reopened.stressed_skin_check.isChecked())
                        self.assertEqual(reopened.canvas.member_selected,identifier)
                        reopened.diaphragm_flexibility.setValue(.72)
                        self.assertIsNone(reopened.result)
                    finally:reopened.reject()
                    changed=copy.deepcopy(saved);changed['stressed_skin']['flexibility_mm_per_kn']=.72
                    reopened=BracingAnalysisDialog(context,w.catalog,changed,cached_calculation=cache)
                    try:self.assertIsNone(reopened.result)
                    finally:reopened.reject()
                    locked=BracingAnalysisDialog(context,w.catalog,saved,read_only=True)
                    try:
                        self.assertFalse(locked.stressed_skin_check.isEnabled())
                        self.assertFalse(locked.diaphragm_flexibility.isEnabled())
                    finally:locked.reject()
                finally:d.reject()
            finally:w.close()


if __name__=='__main__':unittest.main(verbosity=2)
