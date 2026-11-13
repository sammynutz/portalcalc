"""Saved 3D results: no repeat solve, stale-input rejection, job persistence."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from bracing_analysis_desktop import prepare_context
from bracing_analysis_regression import fixed_layout
from bracing_sizing_diagnostics import stop_for_search
from bracing_workspace_ui import BracingAnalysisDialog
from bracing_calculation_cache import unpack


class SavedCalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM']='offscreen'
        os.environ['PORTALCALC_LICENSE_ENFORCED']='0'
        cls.temp=tempfile.TemporaryDirectory()
        os.environ['APPDATA']=cls.temp.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app=QApplication.instance() or QApplication([])
        cls.window=MainWindow();cls.window.confirm_on_close=False
        cls.defaults=copy.deepcopy(cls.window.collect_preset_data())

    @classmethod
    def tearDownClass(cls):
        cls.window.close();cls.temp.cleanup()

    def setUp(self):
        w=self.window
        w.apply_preset_data(copy.deepcopy(self.defaults))
        w.bracing_location_layout=fixed_layout(w.current_bracing_layout_geometry()).to_dict()
        w.bracing_3d_settings={'mixed_struts':False}
        w.generate_and_solve()

    def dialog(self,settings=None,cache=None,context=None):
        return BracingAnalysisDialog(context or prepare_context(self.window),self.window.catalog,
                                     settings or self.window.bracing_3d_settings,cached_calculation=cache)

    def test_reopen_restores_tables_workings_and_view_without_solving(self):
        d=self.dialog()
        try:
            d.run_analysis();self.assertIsNotNone(d.result)
            d.case_combo.setCurrentIndex(5);d.visual_mode.setCurrentIndex(3);d.deformation_scale.setValue(7.)
            cache=d.calculation_cache();settings=d.snapshot()
            self.assertLess(len(cache['payload']),len(json.dumps(d.result)))
            with mock.patch('bracing_workspace_ui.solve_selection') as solve, mock.patch('bracing_workspace_ui.auto_size') as size:
                reopened=self.dialog(settings,cache)
            try:
                solve.assert_not_called();size.assert_not_called()
                self.assertEqual(reopened.result,json.loads(json.dumps(d.result)))
                self.assertEqual(reopened.case_combo.currentIndex(),5)
                self.assertEqual(reopened.visual_mode.currentData(),'deformed')
                self.assertEqual(reopened.deformation_scale.value(),7.)
                self.assertTrue(reopened.export_json_button.isEnabled())
                self.assertEqual(reopened.member_table.rowCount(),len(d.result['section_checks']))
                self.assertIn('LOAD PATH / BRACE DIRECTIONS',reopened.report_text)
                self.assertIn('Saved calculation restored',reopened.status_label.text())
                reopened.criteria_inputs['sls_divisor'].setValue(151.)
                self.assertIsNone(reopened.result)
                self.assertFalse(reopened.export_json_button.isEnabled())
            finally:reopened.reject()
        finally:d.reject()

    def test_changed_wind_rates_sections_ratings_layout_or_engine_reject_cached_result(self):
        d=self.dialog()
        try:
            d.run_analysis();cache=d.calculation_cache();settings=d.snapshot();context=d.context
            variants=[]
            wind=copy.deepcopy(context['cases']);wind[0]['front_kpa']*=1.01
            variants.append((settings,{**context,'cases':wind}))
            variants.append((settings,{**context,'cost_rates':{**context['cost_rates'],'ub':99.}}))
            source=copy.deepcopy(context['source_preset']);source['geometry']['eave_height_mm']='4000'
            variants.append((settings,{**context,'source_preset':source}))
            changed=copy.deepcopy(settings);changed['criteria']['net_area_factor']=.9
            variants.append((changed,context))
            changed=copy.deepcopy(settings);changed['wire_rope_ratings']['16'].update(
                stiffness_basis='declared_ea',ea_kn=10000.,ea_source='Fixture changed declared stiffness')
            variants.append((changed,context))
            changed=copy.deepcopy(settings);changed['roof_brace_label']='20 DIA ROD'
            if changed['roof_brace_label']==settings['roof_brace_label']:changed['roof_brace_label']='24 DIA ROD'
            variants.append((changed,context))
            for index,(saved,c) in enumerate(variants):
                with self.subTest(change=index):
                    reopened=self.dialog(saved,cache,c)
                    self.assertTrue(reopened.result is None);reopened.reject()
            from bracing_layout import BracingLayout
            changed_layout=BracingLayout.from_dict(context['nominal_layout'])
            available=next(p for p in changed_layout.panels if p not in changed_layout.pinned)
            changed_layout.toggle(available,'pinned')
            changed_context={**context,'nominal_layout':changed_layout.to_dict()}
            reopened=self.dialog(settings,cache,changed_context)
            self.assertIsNone(reopened.result);reopened.reject()
            with mock.patch('bracing_calculation_cache.engine_revision',return_value='new engine'):
                reopened=self.dialog(settings,cache)
                self.assertIsNone(reopened.result);reopened.reject()
            for corrupt in ({**cache,'payload':'invalid'}, {**cache,'result_sha256':'wrong'}, {'schema':'old'}):
                self.assertIsNone(unpack(corrupt,cache['fingerprint']))
        finally:d.reject()

    def test_undo_of_a_layout_click_restores_the_cleared_result(self):
        d=self.dialog()
        try:
            d.run_analysis();result=d.result;self.assertIsNotNone(result)
            editor=d.layout_editor
            panel=next(p for p in editor.model.panels if p not in editor.model.pinned and p not in editor.model.blocked)
            editor.toggle_panel(panel)
            self.assertIsNone(d.result)
            with mock.patch('bracing_workspace_ui.solve_selection') as solve:
                editor.restore_history(-1)
            solve.assert_not_called()
            self.assertIs(d.result,result)
            self.assertEqual(d.visual_mode.currentData(),'utilisation')
            self.assertIn('result is shown again',d.status_label.text())
            # A different layout is not the analysed one.
            editor.restore_history(1);self.assertIsNone(d.result)
        finally:d.reject()

    def test_optimiser_saved_check_reopens_in_the_workspace_with_members_inspectable(self):
        import combined_sizing
        w=self.window
        stored=combined_sizing.store_calculation(w)
        self.assertIsNotNone(w.bracing_3d_calculation_cache)
        # Reopen as MainWindow.analyse_fixed_bracing_layout does.
        w.generate_and_solve()
        context=prepare_context(w,allow_unconfigured=True)
        with mock.patch('bracing_workspace_ui.solve_selection') as solve, mock.patch('bracing_workspace_ui.auto_size') as size:
            reopened=BracingAnalysisDialog(context,w.catalog,copy.deepcopy(w.bracing_3d_settings),
                                           cached_calculation=w.bracing_3d_calculation_cache)
        try:
            solve.assert_not_called();size.assert_not_called()
            self.assertEqual(reopened.result,json.loads(json.dumps(stored)))
            self.assertNotEqual(reopened.canvas.result_mode,'layout')
            self.assertEqual(reopened.result['design']['estimated_cost'],stored['design']['estimated_cost'])
        finally:reopened.reject()

    def test_optimiser_refuses_locations_saved_for_other_dimensions(self):
        import combined_sizing, frontier_sizing
        from bracing_layout import BracingLayout
        w=self.window
        saved=BracingLayout.from_dict(w.bracing_location_layout)
        blocked=next(p for p in saved.panels if p.startswith('roof/') and p not in saved.pinned)
        saved.blocked.add(blocked)
        w.bracing_location_layout=saved.to_dict()
        w.set_bays(w.bay_lengths_mm()+[6000.0])  # 6 m longer
        w.generate_and_solve()
        # The failure it prevents: the 3D context drops the old blocks, and saving
        # a search result onto the old layout pins a blocked panel.
        context=prepare_context(w,allow_unconfigured=True)
        self.assertNotIn(blocked,context['layout'].blocked)
        with self.assertRaisesRegex(ValueError,'both blocked and pinned'):
            combined_sizing._store_bracing(w,context,{blocked},combined_sizing._start_selections(w,context,None))
        w.bracing_location_layout=saved.to_dict()
        before=copy.deepcopy(w.collect_preset_data())
        for run in (frontier_sizing.optimise,combined_sizing.run):
            with self.subTest(run=run.__module__), mock.patch('combined_sizing._size_frame') as size:
                with self.assertRaisesRegex(Exception,'dimensions have changed'):
                    run(w)
                size.assert_not_called()
                self.assertEqual(w.collect_preset_data(),before)

    def test_saved_sizing_stop_retains_diagnostic_and_original_input_status(self):
        d=self.dialog()
        try:
            d.run_analysis();result=d.result
            stopped=stop_for_search(result,result,'Fixture bounded stop')
            d.invalidate();d.display_result(stopped.result)
            reopened=self.dialog(d.snapshot(),d.calculation_cache())
            try:
                self.assertEqual(reopened.result['sizing_failure'],json.loads(json.dumps(stopped.diagnostic)))
                self.assertTrue(reopened.sizing_issue_button.isVisibleTo(reopened))
                self.assertIn('AUTO-SIZING STOPPED - INPUT SELECTIONS RETAINED',reopened.report_text)
                self.assertIn('original input selections shown',reopened.result_summary_label.text())
            finally:reopened.reject()
        finally:d.reject()

    def test_main_window_close_reopen_and_saved_job_reuse_calculation(self):
        from PySide6.QtWidgets import QDialog
        from bracing_workspace_ui import solve_selection
        w=self.window
        def first(dialog):
            dialog.run_analysis();self.assertIsNotNone(dialog.result)
            accepted=[];dialog.accepted.connect(lambda:accepted.append(True))
            dialog.show();self.app.processEvents();dialog.close()
            self.assertEqual(accepted,[True])
            return QDialog.Accepted
        def reopened(dialog):
            self.assertIsNotNone(dialog.result)
            self.assertIn('Saved calculation restored',dialog.status_label.text())
            return QDialog.Accepted
        with mock.patch('bracing_workspace_ui.solve_selection',wraps=solve_selection) as solve:
            with mock.patch.object(BracingAnalysisDialog,'exec',new=first):w.analyse_fixed_bracing_layout()
            with mock.patch.object(BracingAnalysisDialog,'exec',new=reopened):w.analyse_fixed_bracing_layout()
            self.assertEqual(solve.call_count,1)
            self.assertNotIn('calculation_3d',w.collect_preset_data()['bracing'])
            path=Path(self.temp.name)/'saved-calculation.json'
            w.write_preset_file(str(path));saved=json.loads(path.read_text())
            self.assertIn('calculation_3d',saved['bracing'])
            w.apply_preset_data(self.defaults);self.assertIsNone(w.bracing_3d_calculation_cache)
            w.apply_preset_data(saved)
            with mock.patch.object(BracingAnalysisDialog,'exec',new=reopened):w.analyse_fixed_bracing_layout()
            self.assertEqual(solve.call_count,1)
            original=copy.deepcopy(w.collect_preset_data());old_cache=copy.deepcopy(w.bracing_3d_calculation_cache)
            def cancelled(dialog):
                dialog.criteria_inputs['sls_divisor'].setValue(160.);dialog.run_analysis()
                return QDialog.Rejected
            with mock.patch.object(BracingAnalysisDialog,'exec',new=cancelled):w.analyse_fixed_bracing_layout()
            self.assertEqual(w.collect_preset_data(),original)
            self.assertEqual(w.bracing_3d_calculation_cache,old_cache)
            w.shs_cost_input.setText('99')
            def stale(dialog):
                self.assertIsNone(dialog.result)
                return QDialog.Rejected
            with mock.patch.object(BracingAnalysisDialog,'exec',new=stale):w.analyse_fixed_bracing_layout()
            self.assertIsNone(w.bracing_3d_calculation_cache)

    def test_optional_saved_results_do_not_change_service_inputs_or_digest(self):
        from portalcalc_service import prepare_request
        inputs=self.window.collect_preset_data()
        a=prepare_request({'request_id':'cache-inputs','mode':'checkSpecified','inputs':inputs})
        inputs['bracing']['calculation_3d']={'schema':'optional-results','payload':'Not an instruction or an input'}
        b=prepare_request({'request_id':'cache-inputs','mode':'checkSpecified','inputs':inputs})
        self.assertEqual(a.input_digest,b.input_digest)
        self.assertEqual(a.inputs,b.inputs)


if __name__=='__main__':unittest.main(verbosity=2)
