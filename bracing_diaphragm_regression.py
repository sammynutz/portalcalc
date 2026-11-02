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
from bracing_diaphragm import (DiaphragmSettings, equivalent_diagonal, roof_diaphragm_model,
                               stressed_skin_model, recover_diaphragm, panel_flexibility, reference_calibration)
from bracing_layout import BracingLayout, LayoutGeometry


class DiaphragmMechanicsTests(unittest.TestCase):
    def test_rev_b_calibration_reproduces_reference_values_and_evidence(self):
        settings=DiaphragmSettings()
        self.assertEqual(settings.calibration_mode,'rev_b_geometry')
        self.assertEqual(settings.flexibility_mm_per_kn,3.)
        self.assertEqual(settings.side_wall_flexibility_mm_per_kn,6.)
        roof=reference_calibration('roof');wall=reference_calibration('wall')
        self.assertAlmostEqual(roof['raw_reference_c_mm_per_kn'],2.820,places=3)
        self.assertAlmostEqual(wall['raw_reference_c_mm_per_kn'],5.941,places=3)
        self.assertEqual(roof['measured_test_c_mm_per_kn'],4.5)
        self.assertAlmostEqual(wall['interpolated_c_mm_per_kn'],3.705,places=3)
        c,record=panel_flexibility(settings,'roof',8500.,12104.)
        self.assertEqual(c,3.);self.assertEqual(record['geometry_factor'],1.)
        props=equivalent_diagonal(12104.,8500.,c)
        self.assertAlmostEqual(props['ea_kn'],7361.,delta=2.)
        self.assertAlmostEqual(props['area_mm2'],36.81,places=2)
        c,record=panel_flexibility(settings,'side_wall',7000.,6000.)
        self.assertEqual(c,6.)
        props=equivalent_diagonal(7000.,6000.,c)
        self.assertAlmostEqual(props['ea_kn'],2666.,delta=2.)
        self.assertAlmostEqual(props['area_mm2'],13.33,places=2)
        json.dumps({'roof':roof,'wall':wall,'panel':record},allow_nan=False)

    def test_rev_b_panel_geometry_changes_c_separately_from_equivalent_member_geometry(self):
        settings=DiaphragmSettings()
        self.assertEqual(panel_flexibility(settings,'roof',17000.,12104.)[0],6.)
        self.assertEqual(panel_flexibility(settings,'roof',8500.,24208.)[0],.75)
        self.assertAlmostEqual(panel_flexibility(settings,'side_wall',8000.,6000.)[0],6.*8/7)
        self.assertEqual(panel_flexibility(settings,'end_wall',7000.,12000.)[0],1.5)
        direct=DiaphragmSettings(flexibility_mm_per_kn=4.5,calibration_mode='direct_c')
        self.assertEqual(panel_flexibility(direct,'roof',17000.,24208.)[0],4.5)
        # New defaults never silently reinterpret a saved old c/direction.
        legacy=DiaphragmSettings.from_dict({'enabled':True,'flexibility_mm_per_kn':1.8,'shear_direction':'longitudinal'})
        self.assertEqual(legacy.calibration_mode,'direct_c')
        self.assertEqual(legacy.flexibility_mm_per_kn,1.8)
        for invalid in ('unknown',None,True):
            with self.assertRaises(AnalysisError):DiaphragmSettings(calibration_mode=invalid).validate()
        with self.assertRaises(AnalysisError):DiaphragmSettings(shear_direction='longitudinal').validate()

    def test_actual_panel_calibration_follows_bay_slope_and_mean_wall_height(self):
        g=LayoutGeometry.create(24000.,15000.,6000.,7.5,'Gable',bay_stations_mm=[0,8500,15000],
            end_wall_stations_mm=[0,6000,12000,18000,24000])
        layout=fixed_layout(g);portal=PortalReduction(frame(g),[(x,g.roof_z(x)) for x in g.roof_stations])
        model,_,_,lookup=build_braced_building(layout,portal,1500.,200.,200.,200.,1500.)
        enhanced,e=stressed_skin_model(model,layout,lookup,DiaphragmSettings(True,side_walls_enabled=True,end_walls_enabled=True),
            dict.fromkeys(('left_wall','right_wall','front_end','back_end'),True))
        for p in e['panels']:
            record=p['calibration'];a=record['a_perpendicular_to_ribs_mm'];b=record['b_parallel_to_ribs_mm']
            if p['family']=='roof':
                expected=3.*(a/b**2)/(8500./12104.**2)
            else:
                expected=6.*(a/b**2)/(7000./6000.**2)
            self.assertAlmostEqual(p['flexibility_mm_per_kn'],expected)
            for member_id,props in p.get('diagonal_properties',{}).items():
                member=next(m for m in enhanced.members if m.id==member_id)
                self.assertAlmostEqual(member.area_mm2,props['length_mm']**3/(200.*expected*props['s_mm']**2))

    def test_wall_settings_validate_and_old_roof_settings_leave_walls_off(self):
        settings=DiaphragmSettings.from_dict({'enabled':True,'flexibility_mm_per_kn':1.8,'shear_direction':'along_slope'})
        self.assertFalse(settings.side_walls_enabled);self.assertFalse(settings.end_walls_enabled)
        self.assertTrue(settings.active)
        self.assertTrue(DiaphragmSettings(side_walls_enabled=True).active)
        for field in ('side_walls_enabled','end_walls_enabled'):
            for value in ('false',1,None):
                with self.assertRaises(AnalysisError):DiaphragmSettings.from_dict({field:value})
        for field in ('side_wall_flexibility_mm_per_kn','end_wall_flexibility_mm_per_kn'):
            for value in (True,0,-1.,float('nan'),float('inf'),'1.8',None):
                with self.assertRaises(AnalysisError):DiaphragmSettings.from_dict({field:value})

    def test_wall_racking_matches_adopted_c_reverses_and_handles_sloping_end(self):
        for side in (True,False):
            # Two top corners with a stiff real collector; the wall helper
            # adds the two anchored bases. Unequal end-wall heights exercise
            # actual per-diagonal conversion rather than a mean-height rod.
            g=LayoutGeometry.create(6000.,8000.,3000.,10. if not side else 0.,'Skillion')
            points=[(0,0,g.roof_z(0)),(0,8000,g.roof_z(0))] if side else [(0,0,g.roof_z(0)),(6000,0,g.roof_z(6000))]
            model=AxialModel3D();lookup={tuple(round(float(v),6) for v in p):model.add_node(p) for p in points}
            axis=1 if side else 0
            model.fixed.update(d for i in (0,1) for d in range(3*i,3*i+3) if d%3!=axis)
            model.add_member(AxialMember('top collector',0,1,1e8))
            for c in (.72,1.8,4.5):
                settings=DiaphragmSettings(side_walls_enabled=side,end_walls_enabled=not side,
                    side_wall_flexibility_mm_per_kn=c,end_wall_flexibility_mm_per_kn=c,calibration_mode='direct_c')
                clad={'left_wall':True} if side else {'front_end':True}
                enhanced,e=stressed_skin_model(model,BracingLayout(g),lookup,settings,clad)
                self.assertEqual(len(e['panels']),1)
                self.assertEqual(len(model.nodes),2)  # helper leaves the bare model untouched
                p=e['panels'][0]
                self.assertEqual(p['shear_direction'],'longitudinal' if side else 'transverse')
                if not side:
                    areas=[v['area_mm2'] for v in p['diagonal_properties'].values()]
                    self.assertNotAlmostEqual(areas[0],areas[1])
                for sign in (1.,-1.):
                    loads=np.zeros(3*len(enhanced.nodes));loads[axis]=loads[3+axis]=sign*500.
                    r=enhanced.solve(loads)
                    movement=sum(r['displacements_mm'][i][axis] for i in (0,1))/2
                    self.assertAlmostEqual(movement,sign*c,places=5)
                    members=[m for m in r['members'] if m['kind']=='wall_diaphragm']
                    self.assertEqual(sum(m['axial_n']>1e-4 for m in members),1)
                    bare={'displacements_mm':[],'tangent_status':'positive_with_taut_rods'}
                    r=recover_diaphragm(r,e['panels'],1,bare)
                    self.assertAlmostEqual(r['diaphragm_panels'][0]['net_shear_kn'],sign,places=6)
                    self.assertAlmostEqual(r['diaphragm_panels'][0]['net_longitudinal_shear_kn'],sign if side else 0.,places=6)

    def test_open_faces_add_no_members_or_anchors(self):
        g=LayoutGeometry.create(6000.,8000.,3000.,10.,'Gable')
        m=AxialModel3D();m.add_node((0,0,3000),fixed=True)
        enhanced,e=stressed_skin_model(m,BracingLayout(g),{},
            DiaphragmSettings(side_walls_enabled=True,end_walls_enabled=True),{})
        self.assertEqual(enhanced.members,[]);self.assertEqual(enhanced.nodes,m.nodes)
        self.assertEqual(e['panel_counts'],{'roof':0,'side_wall':0,'end_wall':0})
        self.assertEqual(len(e['excluded_open_faces']),4)

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
        enhanced, evidence = roof_diaphragm_model(model,layout,lookup,DiaphragmSettings(True,4.5,calibration_mode='direct_c'))
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

    def test_wall_only_sensitivity_preserves_uls_costs_and_bare_stability(self):
        c,s,_=self.fixture();c={**c,'face_cladding':dict.fromkeys(('left_wall','right_wall','front_end','back_end'),True)}
        rates={'shs':3.,'ub':4.,'wb':5.};criteria=DesignCriteria()
        bare=solve_selection(c,s,criteria,rates)
        results=[]
        for value in (.72,1.8,4.5):
            ctx={**c,'stressed_skin':asdict(DiaphragmSettings(side_walls_enabled=True,end_walls_enabled=True,
                side_wall_flexibility_mm_per_kn=value,end_wall_flexibility_mm_per_kn=value))}
            r=solve_selection(ctx,s,criteria,rates);results.append(r)
            self.assertTrue(r['stressed_skin']['active']);self.assertFalse(r['stressed_skin']['settings']['enabled'])
            self.assertEqual(r['member_envelopes'],bare['member_envelopes'])
            self.assertEqual(r['design']['estimated_cost'],bare['design']['estimated_cost'])
            self.assertEqual(set(r['section_checks']),set(bare['section_checks']))
            self.assertEqual(r['stressed_skin']['panel_counts'],{'roof':0,'side_wall':2,'end_wall':2})
            for case,base in zip(r['cases'],bare['cases']):
                self.assertLess(np.max(np.abs(case['balance_force_error_n'])),1e-4)
                self.assertLess(np.max(np.abs(case['balance_moment_error_nmm'])),1e-2)
                if case['case']['level']=='ULS':
                    self.assertEqual(case['members'],base['members'])
                    self.assertTrue(np.allclose(case['displacements_mm'][:len(base['displacements_mm'])],base['displacements_mm'],atol=1e-10))
                    self.assertNotIn('diaphragm_members',case)
                else:
                    path=case['load_path']
                    self.assertNotIn('diaphragm',path['member_base_reactions_n'])
                    self.assertLess(abs(path['roof_to_member_base_y_residual_n']),1e-4)
                    self.assertIn('left_wall',path['diaphragm_base_reactions_n'])
            sls=next(case for case in r['cases'] if case['case']['level']=='SLS')
            sls['bare_sls']['tangent_status']='neutral_rods_required_stability_unverified'
            self.assertFalse(attach_design(copy.deepcopy(r),s,criteria,rates,ctx)['design']['taut_tangent_verified'])
            json.dumps(r,allow_nan=False)
        movements=[r['stressed_skin']['enhanced_max_longitudinal_mm'] for r in results]
        self.assertLess(movements[0],movements[1]);self.assertLess(movements[1],movements[2])
        self.assertEqual(len({r['design_request_hash'] for r in results}),3)

    def test_walls_cannot_rescue_an_unstable_conventional_uls_model(self):
        c,s,_=self.fixture()
        layout=BracingLayout(c['layout'].geometry)
        layout.toggle(next(p.id for p in layout.panels.values() if p.face=='front_end'),'pinned')
        context={**c,'layout':layout,'stressed_skin':asdict(DiaphragmSettings(side_walls_enabled=True)),
                 'face_cladding':dict.fromkeys(('left_wall','right_wall','front_end','back_end'),True)}
        with self.assertRaisesRegex(AnalysisError,'Unstable|ill-conditioned'):
            solve_selection(context,s,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})

    def test_end_walls_have_no_direct_longitudinal_credit_and_open_side_is_excluded(self):
        c,s,_=self.fixture();c={**c,'face_cladding':{'left_wall':True,'right_wall':False,'front_end':True,'back_end':False}}
        settings=DiaphragmSettings(side_walls_enabled=True,end_walls_enabled=True)
        r=analyse_context({**c,'stressed_skin':asdict(settings)},s['strut'],s['end_post'])
        self.assertEqual(r['stressed_skin']['panel_counts'],{'roof':0,'side_wall':1,'end_wall':1})
        self.assertEqual(set(r['stressed_skin']['excluded_open_faces']),{'right_wall','back_end'})
        for case in r['cases']:
            for m in case.get('diaphragm_members',[]):
                if '/front_end/' in m['id']:self.assertEqual(m['longitudinal_shear_kn'],0.)
        settings=DiaphragmSettings(end_walls_enabled=True)
        r=analyse_context({**c,'stressed_skin':asdict(settings)},s['strut'],s['end_post'])
        for case in r['cases']:
            if 'bare_sls' in case:
                # Coupled X/Z actions can indirectly change the roof-brace
                # response. The wall itself must supply exactly zero Y force.
                self.assertTrue(all(v[1]==0. for v in case['load_path']['diaphragm_base_reactions_n'].values()))

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
            context={**c,'stressed_skin':asdict(DiaphragmSettings(True,cvalue,direction,calibration_mode='direct_c'))}
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
        c={**c,'stressed_skin':asdict(DiaphragmSettings(True,side_walls_enabled=True,end_walls_enabled=True)),
           'face_cladding':dict.fromkeys(('left_wall','right_wall','front_end','back_end'),True)}
        criteria=DesignCriteria()
        result,chosen=auto_size(c,s,p,criteria,rates,max_solves=8)
        rerun=solve_selection(c,chosen,criteria,rates)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(result['design_request_hash'],rerun['design_request_hash'])
        self.assertTrue(all('diaphragm_members' in case for case in result['cases'] if case['case']['level']=='SLS'))


class DiaphragmWorkspaceTests(unittest.TestCase):
    def test_rev_b_action_migrates_legacy_direct_inputs_and_exports_panel_calibration(self):
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
                saved={'mixed_struts':False,'stressed_skin':{'enabled':True,'flexibility_mm_per_kn':1.8,
                    'shear_direction':'along_slope','side_walls_enabled':True,'side_wall_flexibility_mm_per_kn':6.}}
                d=BracingAnalysisDialog(context,w.catalog,saved)
                try:
                    self.assertEqual(d.diaphragm_calibration_mode.currentData(),'direct_c')
                    d.run_analysis();self.assertIsNotNone(d.result)
                    d.rev_b_button.click();self.assertIsNone(d.result)
                    self.assertEqual(d.diaphragm_calibration_mode.currentData(),'rev_b_geometry')
                    self.assertEqual(d.diaphragm_flexibility.value(),3.)
                    self.assertEqual(d.side_wall_flexibility.value(),6.)
                    self.assertEqual(d.end_wall_flexibility.value(),6.)
                    self.assertFalse(d.diaphragm_direction.isEnabled())
                    d.run_analysis();self.assertIsNotNone(d.result,d.status_label.text())
                    self.assertIn('REV B REFERENCE CALIBRATION RECORDS',d.report_text)
                    self.assertIn('geometry factor',d.report_text)
                    roof=next(p for p in d.result['stressed_skin']['panels'] if p['family']=='roof')
                    c=roof['flexibility_mm_per_kn']
                    self.assertNotEqual(c,3.)  # actual default panel differs from the reference
                    d.diaphragm_flexibility.setValue(4.5)
                    self.assertIsNone(d.result);d.run_analysis()
                    softened=next(p for p in d.result['stressed_skin']['panels'] if p['family']=='roof')
                    self.assertAlmostEqual(softened['flexibility_mm_per_kn'],c*1.5)
                    saved=d.snapshot();cache=d.calculation_cache()
                    restored=BracingAnalysisDialog(context,w.catalog,saved,cached_calculation=cache)
                    try:
                        self.assertIsNotNone(restored.result)
                        self.assertEqual(restored.diaphragm_calibration_mode.currentData(),'rev_b_geometry')
                    finally:restored.reject()
                finally:d.reject()
            finally:w.close()

    def test_wall_only_controls_export_cache_and_independent_c_values(self):
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
                    self.assertFalse(d.side_wall_check.isChecked());self.assertFalse(d.end_wall_check.isChecked())
                    d.side_wall_check.setChecked(True);d.end_wall_check.setChecked(True)
                    d.diaphragm_calibration_mode.setCurrentIndex(d.diaphragm_calibration_mode.findData('direct_c'))
                    d.side_wall_flexibility.setValue(2.5);d.end_wall_flexibility.setValue(5.)
                    d.run_analysis();self.assertIsNotNone(d.result,d.status_label.text())
                    self.assertFalse(d.stressed_skin_check.isChecked())
                    self.assertEqual(d.result['stressed_skin']['panel_counts']['roof'],0)
                    self.assertGreater(d.result['stressed_skin']['panel_counts']['side_wall'],0)
                    self.assertIn('side-wall trial c 2.5',d.result_summary_label.text())
                    self.assertIn('end-wall trial c 5',d.result_summary_label.text())
                    self.assertIn('Side walls ON: trial c = 2.5',d.report_text)
                    sls=next(i for i,c in enumerate(d.result['cases']) if c['case']['level']=='SLS')
                    d.case_combo.setCurrentIndex(sls)
                    wall=next(m for m in d.result['cases'][sls]['diaphragm_members'] if '/left_wall/' in m['id'])
                    d.inspect_member(wall['id']);self.assertIn('Adopted c = 2.5',d.member_detail.text())
                    self.assertIn('Stiffness only',[d.member_table.item(row,4).text() for row in range(d.member_table.rowCount())])
                    d.visual_mode.setCurrentIndex(3)
                    self.assertFalse(d.canvas.grab().isNull())
                    saved=d.snapshot();cache=d.calculation_cache()
                    with mock.patch('bracing_workspace_ui.solve_selection',side_effect=AssertionError('Must restore cached walls')):
                        restored=BracingAnalysisDialog(context,w.catalog,saved,cached_calculation=cache)
                    try:
                        self.assertIsNotNone(restored.result)
                        self.assertTrue(restored.side_wall_check.isChecked());self.assertTrue(restored.end_wall_check.isChecked())
                        self.assertEqual(restored.side_wall_flexibility.value(),2.5)
                        self.assertEqual(restored.end_wall_flexibility.value(),5.)
                    finally:restored.reject()
                    changed=copy.deepcopy(saved);changed['stressed_skin']['end_wall_flexibility_mm_per_kn']=10.
                    stale=BracingAnalysisDialog(context,w.catalog,changed,cached_calculation=cache)
                    try:self.assertIsNone(stale.result)
                    finally:stale.reject()
                    for widget in (d.side_wall_flexibility,d.end_wall_flexibility):
                        widget.setValue(widget.value()*2)
                        self.assertIsNone(d.result);self.assertFalse(d.export_json_button.isEnabled())
                        d.run_analysis();self.assertIsNotNone(d.result)
                    d.side_wall_check.setChecked(False)
                    self.assertIsNone(d.result)
                    locked=BracingAnalysisDialog(context,w.catalog,saved,read_only=True)
                    try:
                        self.assertFalse(locked.side_wall_check.isEnabled());self.assertFalse(locked.end_wall_check.isEnabled())
                        self.assertFalse(locked.side_wall_flexibility.isEnabled());self.assertFalse(locked.end_wall_flexibility.isEnabled())
                    finally:locked.reject()
                finally:d.reject()
            finally:w.close()

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
