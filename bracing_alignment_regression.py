"""Eave tributary geometry and shared main/3D mullion checks and adoption."""
import copy
from dataclasses import replace
import math
import os
import tempfile
import unittest
from unittest import mock

from bracing_analysis_desktop import eave_tributary_width,prepare_context
from bracing_design import DesignCriteria,member_assessment,solve_selection
from bracing_analysis_regression import fixed_layout
from end_wall import mullion_profile_design


class GeometryAndMullionTests(unittest.TestCase):
    def test_eave_half_actual_span_and_offset_no_unconditional_600_cap(self):
        self.assertEqual(eave_tributary_width([(0,0),(1200,0),(2500,0)],(0,0),(3000,0))['width_mm'],600.)
        self.assertEqual(eave_tributary_width([(2300,0),(3500,0)],(3500,0),(2000,0))['width_mm'],600.)
        self.assertEqual(eave_tributary_width([(200,0),(1400,0)],(0,0),(3000,0))['width_mm'],800.)
        self.assertEqual(eave_tributary_width([(0,0),(1500,0)],(0,0),(3000,0))['width_mm'],750.)
        self.assertEqual(eave_tributary_width([(0,0),(800,0)],(0,0),(800,0))['width_mm'],400.)
        self.assertIsNone(eave_tributary_width([(0,0),(3000,0)],(0,0),(1500,0)))

    def test_shared_mullion_actions_match_beam_statics_and_include_bracing_compression(self):
        from main import ShapeCatalog
        p=ShapeCatalog('Shape_Catalog.xlsx').profile_by_name['250*12.5 SHS']
        r=mullion_profile_design(p,8000.,6000.,.7,.4,axial_force_kn=-25.)
        self.assertAlmostEqual(r['moment_knm'],.7*6*8**2/8)
        self.assertAlmostEqual(r['shear_kn'],.7*6*8/2)
        self.assertAlmostEqual(r['effective_length_mm'],4000.)
        self.assertEqual(r['actions'].moment_modifier,1.75)
        weight=1.2*p.A*1e-6*7850*9.80665*8/1000
        self.assertAlmostEqual(r['actions'].compression_kn,25+weight)
        self.assertAlmostEqual(r['deflection_mm'],5*(.4*6)*8000**4/(384*200000*p.I_analysis))
        tension=mullion_profile_design(p,8000.,6000.,.7,.4,axial_force_kn=25.)
        self.assertEqual(tension['actions'].tension_kn,25.)
        self.assertAlmostEqual(tension['actions'].compression_kn,weight)


class AlignmentWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PORTALCALC_LICENSE_ENFORCED']='0'
        cls.temp=tempfile.TemporaryDirectory();os.environ['APPDATA']=cls.temp.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app=QApplication.instance() or QApplication([])
        cls.w=MainWindow();cls.w.confirm_on_close=False;cls.defaults=copy.deepcopy(cls.w.collect_preset_data())

    @classmethod
    def tearDownClass(cls):cls.w.close();cls.temp.cleanup()

    def setUp(self):
        w=self.w;w.apply_preset_data(copy.deepcopy(self.defaults))
        w.bracing_location_layout=fixed_layout(w.current_bracing_layout_geometry(),end=True).to_dict()
        w.bracing_3d_settings={'mixed_struts':False,'end_post_section':'100*3 SHS'}
        w.generate_and_solve()

    def result(self):
        c=prepare_context(self.w);p=self.w.catalog.profile_by_name['250*12.5 SHS']
        s={'strut':p,'end_post':p,**{g:c[g] for g in ('roof_brace','wall_brace','end_brace')}}
        return c,s,solve_selection(c,s,DesignCriteria(),c['cost_rates'])

    def test_eave_aligned_struts_full_width_apex_half_width_and_loaded_area_pressures(self):
        w=self.w;before=copy.deepcopy(w.collect_preset_data());c=prepare_context(w)
        for line in ('0',str(len(c['layout'].geometry.roof_stations)-1)):
            data=c['installed_purlins'][line]
            self.assertAlmostEqual(data['tributary_width_mm'],w.purlin_check['spacing_mm'],places=4)
            self.assertAlmostEqual(data['eave_tributary']['adjacent_sheet_span_mm'],1200.,places=4)
            self.assertAlmostEqual(data['eave_tributary']['sheet_edge_tributary_width_mm'],600.,places=4)
            width_m=w.purlin_check['spacing_mm']/1000
            self.assertAlmostEqual(data['eave_tributary']['loaded_area_m2'],width_m*8,places=5)
            self.assertAlmostEqual(data['beam_inputs']['outward_kn_m'],data['eave_tributary']['wind_upward_kpa']*width_m)
            self.assertAlmostEqual(data['beam_inputs']['permanent_kn_m'],w.purlin_check['permanent_kpa']*width_m)
            self.assertEqual(data['bending_span_mm'],8000.)
            self.assertIn('First inboard purlin',data['tributary_width_source'])
            self.assertGreater(abs(data['node_offset_mm']),1000.)
            self.assertLess(data['nearest_installed_x_mm'],c['layout'].geometry.span_mm)
            self.assertGreater(data['nearest_installed_x_mm'],0.)
        apex=next(d for d in c['installed_purlins'].values() if d['installed_count']==2)
        self.assertAlmostEqual(apex['tributary_width_mm'],600.,places=4)
        inner=[d for d in c['installed_purlins'].values() if not d['eave_tributary'] and d['installed_count']==1]
        self.assertTrue(inner)
        self.assertTrue(all(d['tributary_width_mm']==w.purlin_check['spacing_mm'] for d in inner))
        self.assertEqual(w.collect_preset_data(),before)

    def test_main_baseline_matches_exact_main_actions_and_combined_rows_have_moment_and_axial(self):
        c,s,r=self.result();index=next(i for i,m in enumerate(r['cases'][0]['members']) if m['kind']=='end_post')
        main=self.w.end_wall_mullion_design
        profile=main['selected']['profile']
        checked=member_assessment(r,index,profile,DesignCriteria())
        baseline=next(row for row in checked['case_checks'] if row['case']=='Main mullion baseline / ULS')
        self.assertEqual(baseline['actions']['moment_knm'],main['selected']['actions'].moment_knm)
        self.assertEqual(baseline['actions']['effective_length_y_mm'],main['selected']['effective_length_mm'])
        self.assertEqual(baseline['ratio'],main['selected']['strength_ratio'])
        changed=copy.deepcopy(r)
        for case in changed['cases']:case['members'][index]['axial_n']=-20000.
        checked=member_assessment(changed,index,s['end_post'],DesignCriteria(net_area_factor=.2,tension_distribution_factor=.1))
        row=next(row for row in checked['case_checks'] if not row.get('required_baseline') and row['level']=='ULS')
        self.assertGreater(row['actions']['moment_knm'],0.)
        self.assertGreater(row['actions']['compression_kn'],20.)
        self.assertAlmostEqual(row['actions']['effective_length_y_mm'],row['design_height_mm']/2)
        self.assertEqual(row['actions']['moment_modifier'],1.75)
        self.assertEqual(row['section_properties'],__import__('dataclasses').asdict(s['end_post'].check_properties()))

    def test_rating_entries_separate_same_section_span_with_different_roof_duty(self):
        from bracing_components import strut_locations
        context=prepare_context(self.w)
        locations=strut_locations(context)
        by_key={}
        for location in locations:
            data=location['purlin']
            by_key.setdefault(data['basis_key'],set()).add(data['bending_key'])
        self.assertTrue(all(len(snapshots)==1 for snapshots in by_key.values()))
        apex=next(loc for loc in locations if loc['purlin']['installed_count']==2)
        same_section=[loc for loc in locations if loc['purlin']['section']==apex['purlin']['section']
                      and loc['purlin']['tributary_width_mm']!=apex['purlin']['tributary_width_mm']]
        self.assertTrue(same_section)
        self.assertTrue(all(loc['purlin']['basis_key']!=apex['purlin']['basis_key'] for loc in same_section))

    def test_legacy_ratings_migrate_only_when_the_frozen_roof_duty_matches(self):
        from bracing_components import strut_locations
        context=prepare_context(self.w)
        location=strut_locations(context)[0]
        key=location['purlin']['basis_key'];legacy_key=key.split('|roof_duty_sha256=')[0]
        rating={'basis_key':legacy_key,'tension_kn':125.,'source':'Synthetic unchanged roof duty',
                'beam_capacity_key':location['purlin']['bending_key']}
        migrated=next(loc for loc in strut_locations(context,{legacy_key:rating}) if loc['id']==location['id'])
        self.assertEqual(migrated['purlin']['axial_rating']['tension_kn'],125.)
        self.assertEqual(migrated['purlin']['axial_rating']['basis_key'],key)
        changed={**rating,'beam_capacity_key':'previous half-width duty'}
        rejected=next(loc for loc in strut_locations(context,{legacy_key:changed}) if loc['id']==location['id'])
        self.assertEqual(rejected['purlin']['axial_rating'],{})

    def test_main_serviceability_prerequisite_survives_strength_only_and_open_end_has_no_flybrace_credit(self):
        c,s,r=self.result();index=next(i for i,m in enumerate(r['cases'][0]['members']) if m['kind']=='end_post')
        changed=copy.deepcopy(r);changed['mullion_basis']['service_pressure_kpa']*=1000
        checked=member_assessment(changed,index,s['end_post'],DesignCriteria(include_serviceability=False))
        self.assertFalse(checked['passes'])
        baseline=next(row for row in checked['case_checks'] if row['case']=='Main mullion baseline / SLS')
        self.assertTrue(baseline['included_in_design']);self.assertGreater(baseline['ratio'],1.)
        changed=copy.deepcopy(r);face=changed['cases'][0]['members'][index]['id'].split('/')[1]
        changed['face_cladding'][face]=False
        checked=member_assessment(changed,index,s['end_post'],DesignCriteria())
        row=next(row for row in checked['case_checks'] if not row.get('required_baseline') and row['level']=='ULS')
        self.assertEqual(row['actions']['effective_length_y_mm'],row['design_height_mm'])
        self.assertEqual(row['actions']['moment_knm'],0.)

    def test_adopted_checked_mullion_updates_main_costing_presets_and_cached_reopen_cancel_does_not(self):
        from PySide6.QtWidgets import QDialog
        from bracing_workspace_ui import BracingAnalysisDialog
        w=self.w;name='250*12.5 SHS'
        def adopted(d):
            self.assertEqual(d.post_combo.currentData(),w.end_wall_mullion_design['selected']['section'])
            d.post_combo.setCurrentIndex(d.post_combo.findData(name));d.run_analysis()
            self.assertTrue(all(v['passes'] for k,v in d.result['section_checks'].items() if k.startswith('post/')))
            return QDialog.Accepted
        with mock.patch.object(BracingAnalysisDialog,'exec',new=adopted):w.analyse_fixed_bracing_layout()
        self.assertEqual(w.bracing_3d_settings['adopted_mullion_section'],name)
        self.assertEqual(w.end_wall_mullion_design['selected']['section'],name)
        self.assertEqual(w.costing_summary['end_wall_mullion_item']['section'],name)
        self.assertIn('Adopted',w.end_wall_mullion_design['section_source'])
        def reopened(d):
            self.assertIsNotNone(d.result);self.assertEqual(d.post_combo.currentData(),name)
            return QDialog.Rejected
        with mock.patch('bracing_workspace_ui.solve_selection',side_effect=AssertionError('Must restore cached result')):
            with mock.patch.object(BracingAnalysisDialog,'exec',new=reopened):w.analyse_fixed_bracing_layout()
        saved=copy.deepcopy(w.collect_preset_data());cache=copy.deepcopy(w.bracing_3d_calculation_cache)
        def cancelled(d):
            d.post_combo.setCurrentIndex(d.post_combo.findData('100*3 SHS'));return QDialog.Rejected
        with mock.patch.object(BracingAnalysisDialog,'exec',new=cancelled):w.analyse_fixed_bracing_layout()
        self.assertEqual(w.collect_preset_data(),saved);self.assertEqual(w.bracing_3d_calculation_cache,cache)
        w.apply_preset_data(saved);w.generate_and_solve()
        self.assertEqual(w.end_wall_mullion_design['selected']['section'],name)


if __name__=='__main__':unittest.main(verbosity=2)
