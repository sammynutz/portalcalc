"""Layout constraint integrity, exact acceptance, cancellation and workspace adoption."""
import copy
import unittest
from unittest import mock

from bracing_analysis import AnalysisError
from bracing_design import DesignCriteria, solve_selection
import bracing_design_regression as design_tests
from bracing_layout import BracingLayout, LayoutGeometry
from bracing_layout_search import (find_cost_layout as find_layout, find_layout as find_practical_layout,
                                   layout_context, LayoutSearchCancelled,
                                   LayoutSearchStopped, structured_seeds, coherent_changes, practical_seeds)


class LayoutSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        design_tests.DesignMechanicsTests.setUpClass()

    def fixture(self):
        fixture = design_tests.DesignMechanicsTests()
        fixture.catalog = design_tests.DesignMechanicsTests.catalog
        context, selected, candidates = fixture.small_context()
        context['layout'] = BracingLayout(context['layout'].geometry,
                                        blocked=['right_wall/bay/0'])
        context['nominal_layout'] = context['layout'].to_dict()
        return context, selected, candidates, DesignCriteria(), {'shs': 3., 'ub': 4., 'wb': 5.}

    def test_blank_selection_search_preserves_constraints_and_matches_exact_rerun(self):
        context, selected, pool, criteria, rates = self.fixture()
        original = copy.deepcopy(context['nominal_layout'])
        required = {'left_wall/bay/0'}
        result, chosen, layout = find_layout(context, selected, pool, criteria, rates,
                                            required=required, max_trials=16, max_section_solves=6)
        self.assertTrue(result['design']['passes'])
        self.assertTrue(required <= layout.pinned)
        self.assertFalse(layout.blocked & layout.pinned)
        self.assertEqual(context['layout'].to_dict(), original)
        self.assertEqual(context['nominal_layout'], original)
        self.assertEqual(result['analysis_layout']['pinned_panels'], sorted(layout.pinned))
        rerun = solve_selection(layout_context(context, layout.pinned), chosen, criteria, rates)
        self.assertTrue(rerun['design']['passes'])
        self.assertAlmostEqual(result['design']['estimated_cost'], rerun['design']['estimated_cost'])
        self.assertAlmostEqual(result['design']['global_sls']['ratio'], rerun['design']['global_sls']['ratio'])
        evidence = result['layout_search']
        self.assertLessEqual(evidence['layout_trials'], 16)
        for trial in evidence['trials']:
            self.assertTrue(required <= set(trial['selected_panels']))
            self.assertFalse(layout.blocked & set(trial['selected_panels']))
            if trial['adopted']:
                self.assertTrue(trial['passes'])
        self.assertLess(len(layout.pinned), len(layout.available))

    def test_bad_constraints_and_all_blocked_do_not_solve(self):
        c, s, pool, criteria, rates = self.fixture()
        with mock.patch('bracing_layout_search.auto_size') as size:
            for required in ({'unknown'}, {'right_wall/bay/0'}):
                with self.assertRaises(AnalysisError):
                    find_layout(c, s, pool, criteria, rates, required=required)
            c['layout'] = BracingLayout(c['layout'].geometry, blocked=set(c['layout'].panels))
            with self.assertRaises(LayoutSearchStopped):
                find_layout(c, s, pool, criteria, rates)
            size.assert_not_called()

    def test_failed_trials_are_not_adopted_and_budget_is_honoured(self):
        c, s, pool, criteria, rates = self.fixture()
        before = c['layout'].to_dict()
        with mock.patch('bracing_layout_search.auto_size', side_effect=AnalysisError('mechanism')) as size:
            with self.assertRaises(LayoutSearchStopped) as raised:
                find_layout(c, s, pool, criteria, rates, max_trials=1)
        self.assertEqual(size.call_count, 1)
        self.assertEqual(raised.exception.evidence['layout_trials'], 1)
        self.assertEqual(c['layout'].to_dict(), before)
        self.assertFalse(raised.exception.evidence['trials'][0]['adopted'])

    def test_diaphragms_cannot_rescue_blocked_conventional_wall_load_paths(self):
        c, s, pool, criteria, rates = self.fixture()
        blocked = {p for p in c['layout'].panels if p.startswith(('left_wall/', 'right_wall/'))}
        c['layout'] = BracingLayout(c['layout'].geometry, blocked=blocked)
        c['nominal_layout'] = c['layout'].to_dict()
        c['face_cladding'] = {'left_wall': True, 'right_wall': True}
        c['stressed_skin'] = {'enabled': True, 'side_walls_enabled': True, 'end_walls_enabled': True}
        with self.assertRaises(LayoutSearchStopped) as raised:
            find_layout(c, s, pool, criteria, rates, max_trials=4, max_section_solves=3)
        self.assertFalse(any(row['adopted'] for row in raised.exception.evidence['trials']))
        self.assertFalse(c['layout'].pinned)

    def test_cancellation_inside_section_sizing_escapes_failure_handler(self):
        c, s, pool, criteria, rates = self.fixture()
        def cancel(*args, **kwargs):
            args[5](0, 12, 'cancel now')
        def progress(step, total, message):
            if message.endswith('cancel now'):
                raise LayoutSearchCancelled('cancelled')
        with mock.patch('bracing_layout_search.auto_size', side_effect=cancel):
            with self.assertRaises(LayoutSearchCancelled):
                find_layout(c, s, pool, criteria, rates, progress=progress)
        self.assertFalse(c['layout'].pinned)

    def multi_bay_fixture(self, count=3):
        c, s, pool, criteria, rates = self.fixture()
        g = LayoutGeometry.create(6000, 8000*count, 3000, 10, 'Gable', end_wall_stations_mm=[0, 6000])
        c['layout'] = BracingLayout(g, blocked=[f'right_wall/bay/{i}' for i in range(count)],
            pinned=['left_wall/bay/0', 'roof/bay/0/strip/0', 'roof/bay/1/strip/1'])
        c['nominal_layout'] = c['layout'].to_dict()
        return c, s, pool, criteria, rates

    @staticmethod
    def checked(cost, passes=True):
        return {'design': {'passes': passes, 'estimated_cost': cost,
                          'global_sls': {'ratio': .5 if passes else 2.},
                          'taut_tangent_verified': passes}, 'limitations': [], 'cases': [{'members': []}]}

    def test_compares_resized_seeds_after_first_pass_and_reuses_duplicate_checks(self):
        c, s, pool, criteria, rates = self.multi_bay_fixture()
        first = set(c['layout'].pinned)
        ends = {p for p in c['layout'].available if '/bay/' in p and int(p.split('/')[2]) in (0, 2)}
        calls = []
        def sized(context, chosen, *args, **kwargs):
            selected = set(context['layout'].pinned)
            calls.append((frozenset(selected), 'size'))
            return self.checked(100. if selected == ends else 300.), chosen
        def solve(context, *args):
            calls.append((frozenset(context['layout'].pinned), 'solve'))
            return self.checked(400., False)
        with mock.patch('bracing_layout_search.auto_size', side_effect=sized), mock.patch('bracing_layout_search.solve_selection', side_effect=solve):
            result, chosen, layout = find_layout(c, s, pool, criteria, rates)
        self.assertEqual(layout.pinned, ends)
        self.assertNotEqual(layout.pinned, first)
        self.assertEqual(result['design']['estimated_cost'], 100.)
        self.assertGreaterEqual(result['layout_search']['structured_seed_trials'], 2)
        self.assertEqual(len(calls), len(set(calls)))
        self.assertLessEqual(len(calls), 48)
        self.assertGreater(result['layout_search']['duplicate_trials_skipped'], 0)

    def test_bay_relocation_crosses_a_failed_deletion_barrier(self):
        c, s, pool, criteria, rates = self.multi_bay_fixture(4)
        start = set(c['layout'].pinned)
        target = (start-{'roof/bay/1/strip/1'}) | {'roof/bay/2/strip/1'}
        required = {'left_wall/bay/0'}
        def sized(context, chosen, *args, **kwargs):
            selected = set(context['layout'].pinned)
            if selected not in (start, target):
                raise AnalysisError('No checked sizing for this fixture topology')
            return self.checked(100. if selected == target else 200.), chosen
        def solve(context, *args):
            selected = set(context['layout'].pinned)
            return self.checked(100. if selected == target else 200., selected in (start, target))
        with mock.patch('bracing_layout_search.auto_size', side_effect=sized), mock.patch('bracing_layout_search.solve_selection', side_effect=solve):
            result, chosen, layout = find_layout(c, s, pool, criteria, rates, required=required)
        self.assertEqual(layout.pinned, target)
        self.assertEqual(result['design']['estimated_cost'], 100.)
        self.assertTrue(any(t['reason'].startswith('Move bay') and t['adopted'] for t in result['layout_search']['trials']))
        for t in result['layout_search']['trials']:
            self.assertTrue(required <= set(t['selected_panels']))
            self.assertFalse(set(t['selected_panels']) & c['layout'].blocked)

    def test_structured_proposals_preserve_required_and_blocked_panels(self):
        c, *_ = self.multi_bay_fixture(4)
        layout = c['layout']
        layout.blocked.add('roof/bay/2/strip/1')
        required = {'left_wall/bay/0'}
        proposals = [*structured_seeds(layout, required), *coherent_changes(layout, set(layout.pinned), required)]
        self.assertGreater(len(proposals), 6)
        for reason, selected in proposals:
            self.assertTrue(required <= selected, reason)
            self.assertTrue(selected <= layout.available, reason)

    def test_extra_braces_can_win_after_coupled_resizing_even_if_static_cost_is_higher(self):
        c, s, pool, criteria, rates = self.multi_bay_fixture()
        start = set(c['layout'].pinned)
        completed = start | {'roof/bay/0/strip/1', 'roof/bay/1/strip/0'}
        def sized(context, chosen, *args, **kwargs):
            selected = set(context['layout'].pinned)
            return self.checked(100. if selected == completed else 200. if selected == start else 400.), chosen
        def solve(context, *args):
            selected = set(context['layout'].pinned)
            # Extra braces initially cost more; only re-sizing reveals savings.
            return self.checked(250., selected == completed)
        with mock.patch('bracing_layout_search.auto_size', side_effect=sized), mock.patch('bracing_layout_search.solve_selection', side_effect=solve):
            result, chosen, layout = find_layout(c, s, pool, criteria, rates)
        self.assertEqual(layout.pinned, completed)
        self.assertGreater(len(layout.pinned), len(start))
        self.assertEqual(result['design']['estimated_cost'], 100.)
        self.assertTrue(any(t['reason']=='Re-size bay alternative' and t['adopted'] for t in result['layout_search']['trials']))

    def test_real_search_matches_or_improves_independently_resized_bay_alternatives(self):
        from bracing_design import auto_size
        c, s, pool, criteria, rates = self.multi_bay_fixture()
        verified = []
        for reason, selected in structured_seeds(c['layout'], set()):
            try:
                result, chosen = auto_size(layout_context(c, selected), s, pool, criteria, rates, max_solves=6)
            except AnalysisError:
                continue
            if result['design']['passes']:
                verified.append(result['design']['estimated_cost'])
        self.assertTrue(verified)
        result, chosen, layout = find_layout(c, s, pool, criteria, rates, max_trials=36, max_section_solves=6)
        self.assertTrue(result['design']['passes'])
        self.assertLessEqual(result['design']['estimated_cost'], min(verified)+1e-8)
        rerun = solve_selection(layout_context(c, layout.pinned), chosen, criteria, rates)
        self.assertTrue(rerun['design']['passes'])
        self.assertAlmostEqual(result['design']['estimated_cost'], rerun['design']['estimated_cost'])


class LayoutSearchWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        design_tests.WorkspaceTests.setUpClass()
        cls.app, cls.window = design_tests.WorkspaceTests.app, design_tests.WorkspaceTests.window

    @classmethod
    def tearDownClass(cls):
        design_tests.WorkspaceTests.tearDownClass()

    def setUp(self):
        self.window.apply_preset_data(copy.deepcopy(design_tests.WorkspaceTests.defaults))
        self.window.generate_and_solve()

    def dialog(self, read_only=False):
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        return BracingAnalysisDialog(prepare_context(self.window, allow_unconfigured=True),
            self.window.catalog, {'mixed_struts': False}, read_only=read_only)

    def test_empty_layout_can_search_and_failure_is_transactional(self):
        d = self.dialog()
        try:
            self.assertTrue(d.layout_search_button.isEnabled())
            self.assertFalse(d.run_button.isEnabled())
            self.assertFalse(d.auto_button.isEnabled())
            self.assertFalse(d.keep_current_panels.isChecked())
            self.assertEqual(d.layout_strategy.currentData(), 'practical')
            self.assertEqual(d.layout_trial_budget.value(), 6)
            d.layout_editor.toggle_panel('right_wall/bay/0')
            before, settings = d.layout_snapshot(), d.snapshot()
            with mock.patch('bracing_workspace_ui.find_layout', side_effect=LayoutSearchCancelled('cancelled')):
                d.run_layout_search()
            self.assertEqual(d.layout_snapshot(), before)
            self.assertEqual(d.snapshot(), settings)
            self.assertIsNone(d.result)
            self.assertFalse(d.export_json_button.isEnabled())
            self.assertTrue(d.layout_search_button.isEnabled())
        finally:
            d.reject()
        d = self.dialog(read_only=True)
        self.assertFalse(d.layout_search_button.isEnabled())
        self.assertFalse(d.layout_strategy.isEnabled())
        d.reject()

    def test_legacy_budget_migrates_to_fast_and_detailed_mode_remains_explicit(self):
        from bracing_workspace_ui import BracingAnalysisDialog
        from bracing_analysis_desktop import prepare_context
        context = prepare_context(self.window, allow_unconfigured=True)
        d = BracingAnalysisDialog(context, self.window.catalog,
            {'mixed_struts': False, 'layout_search': {'max_trials': 48, 'keep_selected': True}})
        try:
            self.assertEqual(d.layout_strategy.currentData(), 'practical')
            self.assertEqual(d.layout_trial_budget.value(), 6)
            self.assertTrue(d.keep_current_panels.isChecked())
            d.layout_strategy.setCurrentIndex(d.layout_strategy.findData('cost'))
            self.assertEqual(d.layout_trial_budget.value(), 48)
            settings = d.snapshot()
            restored = BracingAnalysisDialog(context, self.window.catalog, settings)
            self.assertEqual(restored.layout_strategy.currentData(), 'cost')
            self.assertEqual(restored.layout_trial_budget.value(), 48)
            restored.reject()
        finally:
            d.reject()

    def test_installation_rate_saves_and_invalidates_old_cost_cache(self):
        from bracing_workspace_ui import BracingAnalysisDialog
        d = design_tests.WorkspaceTests.dialog(self)
        before = copy.deepcopy(self.window.collect_preset_data())
        try:
            self.assertEqual(d.panel_installation_input.value(), 50.)
            d.run_analysis()
            self.assertIsNotNone(d.result)
            count = len(d.layout_snapshot()['pinned_panels'])
            self.assertEqual(d.result['design']['installation_cost'], count*50.)
            self.assertIn('Panel installation:', d.report_text)
            cache = d.calculation_cache()
            d.panel_installation_input.setValue(75.)
            self.assertIsNone(d.result)
            self.assertEqual(d.snapshot()['installation_cost_per_panel'], 75.)
            self.assertEqual(d.current_context()['cost_rates']['braced_panel_installation'], 75.)
            self.assertFalse(d.restore_calculation(cache))
            restored = BracingAnalysisDialog(d.current_context(), self.window.catalog, d.snapshot())
            self.assertEqual(restored.panel_installation_input.value(), 75.)
            restored.reject()
            self.assertEqual(self.window.collect_preset_data(), before)
        finally:
            d.reject()

    def test_passing_search_adopts_undoable_layout_and_cache_without_changing_job(self):
        from bracing_analysis_regression import fixed_layout
        d = self.dialog()
        before_job = copy.deepcopy(self.window.collect_preset_data())
        try:
            d.show()
            self.app.processEvents()
            blocked = 'right_wall/bay/1'
            d.layout_editor.toggle_panel(blocked)
            before = d.layout_snapshot()
            context = d.current_context(require_selected=False)
            selected = fixed_layout(context['layout'].geometry).pinned - {blocked}
            trial = layout_context(context, selected)
            chosen = d.selections(context)
            result = solve_selection(trial, chosen, d.criteria(), context['cost_rates'])
            # Adoption transaction is tested with a real analysis payload. The
            # engine's passing-only acceptance is exercised separately above.
            with mock.patch('bracing_workspace_ui.find_layout', return_value=(result, chosen, trial['layout'])) as search:
                d.run_layout_search()
            self.assertEqual(search.call_args.kwargs['required'], set())
            self.assertEqual(d.layout_editor.model.pinned, selected)
            self.assertEqual(d.layout_editor.model.blocked, {blocked})
            self.assertIsNotNone(d.result)
            cache = d.calculation_cache()
            self.assertIsNotNone(cache)
            self.assertTrue(d.run_button.isEnabled())
            self.assertTrue(d.layout_editor.save_button.isHidden())
            self.assertEqual(self.window.collect_preset_data(), before_job)
            from bracing_workspace_ui import BracingAnalysisDialog
            reopened = BracingAnalysisDialog(trial, self.window.catalog, d.snapshot(), cached_calculation=cache)
            self.assertIsNotNone(reopened.result)
            self.assertEqual(reopened.result['analysis_layout']['pinned_panels'], sorted(selected))
            reopened.reject()
            d.layout_editor.restore_history(-1)
            self.assertEqual(d.layout_snapshot(), before)
            self.assertIsNone(d.result)
            d.keep_current_panels.setChecked(True)
            d.layout_editor.model.pinned = set(selected)
            with mock.patch('bracing_workspace_ui.find_layout', side_effect=LayoutSearchCancelled('cancelled')) as search:
                d.run_layout_search()
            self.assertEqual(search.call_args.kwargs['required'], selected)
        finally:
            d.reject()


class PracticalLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(design_tests.DesignMechanicsTests, 'catalog'):
            design_tests.DesignMechanicsTests.setUpClass()

    def fixture(self, width=24000):
        helper = LayoutSearchTests()
        c, s, pool, criteria, rates = helper.fixture()
        g = LayoutGeometry.create(width-492.5, 40000, 6000, 7.5, 'Gable', end_wall_stations_mm=[0,(width-492.5)/3,2*(width-492.5)/3,width-492.5])
        nominal = LayoutGeometry.create(width, 40000, 6000, 7.5, 'Gable', end_wall_stations_mm=[0,width/3,2*width/3,width])
        c['layout'] = BracingLayout(g, blocked=[f'right_wall/bay/{i}' for i in range(5)])
        c['nominal_layout'] = BracingLayout(nominal, blocked=c['layout'].blocked).to_dict()
        return c, s, pool, criteria, rates

    def test_wide_one_sided_order_uses_nominal_width_and_retains_constraints(self):
        c, *_ = self.fixture()
        c['layout'].blocked.add('front_end/panel/2')
        required = {'roof/bay/2/strip/0'}
        proposals = list(practical_seeds(c, required))
        self.assertIn('supported left-eave roof girder', proposals[0][0])
        self.assertIn('front_end/panel/1', proposals[0][1])
        self.assertIn('back_end/panel/2', proposals[0][1])
        self.assertIn('left_wall/bay/0', proposals[0][1])
        self.assertIn('left_wall/bay/4', proposals[0][1])
        self.assertEqual(len(proposals), len({frozenset(p) for label,p in proposals}))
        for reason, selected in proposals:
            self.assertTrue(required <= selected)
            self.assertTrue(selected <= c['layout'].available)

    def test_continuous_eave_girder_follows_supported_side_and_never_spans_a_block(self):
        c, *_ = self.fixture()
        required = {'roof/bay/2/strip/2'}
        patterns = [(label,p) for label,p in practical_seeds(c, required) if 'continuous' in label]
        self.assertIn('supported left-eave', patterns[0][0])
        self.assertTrue({f'roof/bay/{b}/strip/0' for b in range(5)} <= patterns[0][1])
        self.assertTrue(required <= patterns[0][1])
        self.assertTrue({'left_wall/bay/0','left_wall/bay/4'} <= patterns[0][1])
        for bay in (0,4):
            self.assertTrue({f'roof/bay/{bay}/strip/{s}' for s in range(4)} <= patterns[0][1])
        c['layout'].blocked.add('roof/bay/2/strip/0')
        patterns = [(label,p) for label,p in practical_seeds(c, required) if 'continuous' in label]
        self.assertFalse(any('left-eave' in label or 'both eaves' in label for label,p in patterns))
        self.assertTrue(all(p <= c['layout'].available for label,p in patterns))
        c['layout'] = BracingLayout(c['layout'].geometry, blocked=[f'left_wall/bay/{b}' for b in range(5)])
        patterns = [(label,p) for label,p in practical_seeds(c, set()) if 'continuous' in label]
        self.assertIn('supported right-eave', patterns[0][0])
        self.assertTrue({f'roof/bay/{b}/strip/3' for b in range(5)} <= patterns[0][1])

    def test_four_bay_input_compares_a_continuous_girder_before_whole_bay_deletion(self):
        c, s, pool, criteria, rates = self.fixture()
        c['layout'].pinned = {p for p in c['layout'].available if '/bay/' in p and int(p.split('/')[2]) in (0,1,3,4)} | {'front_end/panel/2','back_end/panel/2'}
        def sized(context, chosen, *args, **kwargs):
            return LayoutSearchTests.checked(1000+50*len(context['layout'].pinned)), chosen
        def screen(context, chosen, *args):
            return sized(context, chosen)[0]
        with mock.patch('bracing_layout_search.solve_selection', side_effect=screen), mock.patch('bracing_layout_search.auto_size', side_effect=sized) as size:
            result, chosen, layout = find_practical_layout(c,s,pool,criteria,rates)
        self.assertEqual(size.call_count, 1)
        self.assertFalse(result['layout_search']['trials'][0]['section_sizing'])
        self.assertIn('Verify current layout', result['layout_search']['trials'][0]['reason'])
        self.assertIn('continuous supported left-eave', result['layout_search']['trials'][1]['reason'])
        self.assertEqual(len(layout.pinned), 15)
        full_bays = {b for b in range(5) if all(f'roof/bay/{b}/strip/{strip}' in layout.pinned for strip in range(4))}
        self.assertEqual(full_bays, {0,4})
        self.assertTrue({f'roof/bay/{b}/strip/0' for b in range(5)} <= layout.pinned)

    def test_35m_geometry_prioritises_deeper_band_and_paired_anchors(self):
        c, *_ = self.fixture(35000)
        g = LayoutGeometry.create(34507.5, 40000, 6000, 7.5, 'Gable',
            end_wall_stations_mm=[i*34507.5/5 for i in range(6)])
        c['layout'] = BracingLayout(g, blocked=c['layout'].blocked)
        reason, panels = next(practical_seeds(c, set()))
        self.assertIn('2-strip supported left-eave', reason)
        self.assertIn('paired end anchors', reason)
        self.assertEqual(len(panels), 24)
        self.assertTrue({f'roof/bay/{b}/strip/{s}' for b in range(5) for s in (0,1)} <= panels)
        self.assertTrue({f'{face}/panel/{i}' for face in ('front_end','back_end') for i in (0,4)} <= panels)
        c['layout'].blocked.add('roof/bay/2/strip/1')
        for label, selected in practical_seeds(c, set()):
            self.assertNotIn('2-strip supported left-eave', label)
            self.assertTrue(selected <= c['layout'].available)

    def test_compact_failure_advances_family_and_stops_at_first_improvement(self):
        c, s, pool, criteria, rates = self.fixture()
        rates['braced_panel_installation'] = 80.
        c['layout'].pinned = set(c['layout'].available)
        before = c['layout'].to_dict()
        with mock.patch('bracing_layout_search.solve_selection', return_value=LayoutSearchTests.checked(5000.)), mock.patch(
                'bracing_layout_search.auto_size', side_effect=[AnalysisError('SLS fails'), (LayoutSearchTests.checked(4000.),s)]) as size:
            result, chosen, layout = find_practical_layout(c,s,pool,criteria,rates)
        self.assertEqual(size.call_count, 2)
        self.assertEqual(result['layout_search']['layout_trials'], 3)
        self.assertEqual(result['layout_search']['compact_comparison']['patterns_checked'], 2)
        self.assertEqual(result['layout_search']['winning_trial'], 3)
        self.assertEqual(result['design']['estimated_cost'], 4000.)
        self.assertEqual(c['layout'].to_dict(), before)
        self.assertLess(len(layout.pinned), len(c['layout'].pinned))

    def test_compact_family_is_bounded_and_all_failures_keep_checked_incumbent(self):
        c, s, pool, criteria, rates = self.fixture()
        rates['braced_panel_installation'] = 80.
        c['layout'].pinned = set(c['layout'].available)
        with mock.patch('bracing_layout_search.solve_selection', return_value=LayoutSearchTests.checked(5000.)), mock.patch(
                'bracing_layout_search.auto_size', side_effect=AnalysisError('SLS fails')) as size:
            result, chosen, layout = find_practical_layout(c,s,pool,criteria,rates)
        self.assertEqual(size.call_count, 4)
        self.assertEqual(result['layout_search']['layout_trials'], 5)
        self.assertEqual(result['layout_search']['compact_comparison']['patterns_checked'], 4)
        self.assertFalse(result['layout_search']['compact_comparison']['adopted'])
        self.assertEqual(layout.pinned, c['layout'].pinned)
        self.assertEqual(result['design']['estimated_cost'], 5000.)

    def test_existing_two_strip_girder_is_not_compacted_to_a_narrow_strip(self):
        c, s, pool, criteria, rates = self.fixture(35000)
        rates['braced_panel_installation'] = 80.
        g = LayoutGeometry.create(34507.5, 40000, 6000, 7.5, 'Gable',
            end_wall_stations_mm=[i*34507.5/5 for i in range(6)])
        c['layout'] = BracingLayout(g, blocked=c['layout'].blocked)
        c['layout'].pinned = next(p for label,p in practical_seeds(c,set()) if '2-strip' in label and 'full end restraint' in label)
        girder = {f'roof/bay/{b}/strip/{j}' for b in range(5) for j in (0,1)}
        with mock.patch('bracing_layout_search.solve_selection', return_value=LayoutSearchTests.checked(5000.)), mock.patch(
                'bracing_layout_search.auto_size', side_effect=AnalysisError('SLS fails')):
            result, chosen, layout = find_practical_layout(c,s,pool,criteria,rates)
        self.assertGreater(result['layout_search']['layout_trials'], 1)
        for trial in result['layout_search']['trials']:
            self.assertTrue(girder <= set(trial['selected_panels']))

    def test_stops_at_first_demand_sized_pass_without_layout_polish(self):
        c, s, pool, criteria, rates = self.fixture()
        rates['braced_panel_installation'] = 0.
        screen = LayoutSearchTests.checked(200.)
        checked = LayoutSearchTests.checked(180.)
        checked['sizing_search'] = {'exact_solves': 2, 'fast': True}
        with mock.patch('bracing_layout_search.solve_selection', return_value=screen) as solve, mock.patch('bracing_layout_search.auto_size', return_value=(checked,s)) as size:
            result, chosen, layout = find_practical_layout(c, s, pool, criteria, rates)
        self.assertEqual(solve.call_count, 1)
        self.assertEqual(size.call_count, 1)
        self.assertTrue(size.call_args.kwargs['fast'])
        self.assertIs(size.call_args.kwargs['initial_result'], screen)
        self.assertEqual(size.call_args.kwargs['max_solves'], 12)
        self.assertEqual(result['layout_search']['layout_trials'], 1)
        self.assertEqual(result['layout_search']['strategy'], 'practical')
        self.assertFalse(result['layout_search']['section_polish'])
        self.assertTrue(result['layout_search']['stopped_after_pass'])

    def test_installation_can_favour_fewer_panels_with_larger_material_cost(self):
        c, s, pool, criteria, rates = self.fixture()
        c['layout'].pinned = next(p for label,p in practical_seeds(c,set()) if label == 'End bays with distributed end-wall restraint')
        def sized(context, chosen, *args, **kwargs):
            count = len(context['layout'].pinned)
            materials = 1000. if count == 16 else 1020.
            result = LayoutSearchTests.checked(materials+count*50.)
            result['sizing_search'] = {'exact_solves': 2, 'fast': True}
            return result, chosen
        def screen(context, chosen, *args):
            return sized(context, chosen)[0]
        with mock.patch('bracing_layout_search.solve_selection', side_effect=screen), mock.patch('bracing_layout_search.auto_size', side_effect=sized) as size:
            result, chosen, layout = find_practical_layout(c, s, pool, criteria, rates)
        self.assertEqual(size.call_count, 1)
        self.assertEqual(len(layout.pinned), 15)
        self.assertEqual(result['design']['estimated_cost'], 1770.)
        self.assertTrue(result['layout_search']['compact_comparison']['adopted'])
        self.assertEqual(result['layout_search']['winning_trial'], 2)
        self.assertTrue(all(not layout.blocked & set(t['selected_panels']) for t in result['layout_search']['trials']))

    def test_failed_compact_check_retains_passing_incumbent_and_honours_budget(self):
        c, s, pool, criteria, rates = self.fixture()
        c['layout'].pinned = set(c['layout'].available)
        with mock.patch('bracing_layout_search.solve_selection', return_value=LayoutSearchTests.checked(1800.)), mock.patch('bracing_layout_search.auto_size', side_effect=AnalysisError('SLS fails')) as size:
            result, chosen, layout = find_practical_layout(c, s, pool, criteria, rates, max_trials=2)
        self.assertEqual(size.call_count, 1)
        self.assertEqual(layout.pinned, c['layout'].pinned)
        self.assertEqual(result['design']['estimated_cost'], 1800.)
        self.assertFalse(result['layout_search']['compact_comparison']['adopted'])
        self.assertEqual(result['layout_search']['layout_trials'], 2)
        with mock.patch('bracing_layout_search.solve_selection', return_value=LayoutSearchTests.checked(200.)), mock.patch('bracing_layout_search.auto_size', return_value=(LayoutSearchTests.checked(1800.), s)) as size:
            result, chosen, layout = find_practical_layout(c, s, pool, criteria, rates, max_trials=1)
        self.assertEqual(size.call_count, 0)
        self.assertNotIn('compact_comparison', result['layout_search'])

    def test_severe_sway_tries_end_walls_before_repeated_member_growth(self):
        c, s, pool, criteria, rates = self.fixture(21000)
        rates['braced_panel_installation'] = 0.
        def screen(context, *args):
            ends = any(p.startswith(('front_end/', 'back_end/')) for p in context['layout'].pinned)
            return LayoutSearchTests.checked(150., ends)
        checked = LayoutSearchTests.checked(150.)
        with mock.patch('bracing_layout_search.solve_selection', side_effect=screen), mock.patch('bracing_layout_search.auto_size', return_value=(checked,s)) as size:
            result, chosen, layout = find_practical_layout(c, s, pool, criteria, rates)
        self.assertEqual(size.call_count, 1)
        self.assertEqual(result['layout_search']['layout_trials'], 2)
        self.assertIn('end-wall restraint', result['layout_search']['trials'][0]['decision'])
        self.assertFalse(result['layout_search']['trials'][0]['section_sizing'])

    def test_real_practical_sizing_passes_exact_recheck_without_cost_polish(self):
        c, s, pool, criteria, rates = LayoutSearchTests().fixture()
        result, chosen, layout = find_practical_layout(c, s, pool, criteria, rates)
        self.assertTrue(result['design']['passes'])
        self.assertTrue(result['sizing_search']['fast'])
        self.assertEqual(result['sizing_search']['cost_reduction_trials'], [])
        self.assertLessEqual(result['sizing_search']['exact_solves'], 12)
        rerun = solve_selection(layout_context(c, layout.pinned), chosen, criteria, rates)
        self.assertTrue(rerun['design']['passes'])
        self.assertAlmostEqual(result['design']['estimated_cost'], rerun['design']['estimated_cost'])

    def test_targeted_sway_uses_upgrade_cost_and_never_grows_posts(self):
        from bracing_design import fast_sway_proposal
        from bracing_design_regression import rod
        helper = design_tests.DesignMechanicsTests()
        helper.catalog = design_tests.DesignMechanicsTests.catalog
        c, s, pool = helper.small_context()
        c = layout_context(c, c['layout'].pinned | {p for p in c['layout'].available if p.startswith(('front_end/', 'back_end/'))})
        criteria, rates = DesignCriteria(), {'shs': 3., 'ub': 4., 'wb': 5.}
        checked = solve_selection(c, s, criteria, rates)
        checked['design']['global_sls']['ratio'] = 1.1  # exercise a sway-only adjustment
        for g in ('roof_brace', 'wall_brace', 'end_brace'):
            pool[g] = [rod(16), rod(20), rod(24)]
        tried = set()
        proposed, group = fast_sway_proposal(c, checked, s, pool, criteria, rates, tried)
        self.assertEqual(group, 'end_brace')
        self.assertEqual(proposed['end_brace']['label'], '20 DIA ROD')
        self.assertIs(proposed['strut'], s['strut'])
        self.assertIs(proposed['end_post'], s['end_post'])
        proposed, group = fast_sway_proposal(c, checked, proposed, pool, criteria, rates, tried)
        self.assertEqual(group, 'wall_brace')
        self.assertIs(proposed['end_post'], s['end_post'])
        # More than one sweep is permitted, up to the priced catalogue ceiling.
        for _ in range(2):
            next_choice, group = fast_sway_proposal(c, checked, proposed, pool, criteria, rates, tried)
            self.assertGreater(next_choice[group]['area_mm2'], proposed[group]['area_mm2'])
            self.assertIs(next_choice['end_post'], s['end_post'])
            proposed = next_choice
        self.assertIsNone(fast_sway_proposal(c, checked, proposed, pool, criteria, rates, tried))

    def test_cancel_and_failed_search_leave_constraints_untouched(self):
        c, s, pool, criteria, rates = self.fixture()
        before = c['layout'].to_dict()
        with mock.patch('bracing_layout_search.solve_selection', side_effect=AnalysisError('blocked load path')) as solve:
            with self.assertRaises(LayoutSearchStopped):
                find_practical_layout(c, s, pool, criteria, rates, max_trials=2)
        self.assertEqual(solve.call_count, 2)
        self.assertEqual(c['layout'].to_dict(), before)
        def cancel(*args):
            raise LayoutSearchCancelled('cancelled')
        with self.assertRaises(LayoutSearchCancelled):
            find_practical_layout(c, s, pool, criteria, rates, progress=cancel)
        self.assertEqual(c['layout'].to_dict(), before)


class BracedPanelCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(design_tests.DesignMechanicsTests, 'catalog'):
            design_tests.DesignMechanicsTests.setUpClass()

    def test_count_crossed_pair_once_exclude_sheets_and_do_not_multiply_by_cases(self):
        from bracing_design import panel_installation_cost
        members = [{'id': 'brace/'+panel+'/'+str(i), 'kind': kind, 'axial_n': 0.}
                   for panel, kind in [('roof/bay/0/strip/0', 'roof_brace'), ('left_wall/bay/0', 'wall_brace'), ('front_end/panel/0', 'end_brace')]
                   for i in (0, 1)]
        members += [{'id': 'diaphragm/roof/0/0', 'kind': 'roof_diaphragm'},
                    {'id': 'strut/0', 'kind': 'longitudinal_strut'}]
        result = {'cases': [{'members': members} for _ in range(8)]}
        priced = panel_installation_cost(result, {})
        self.assertEqual(priced['panel_count'], 3)
        self.assertEqual(priced['total'], 150.)
        self.assertEqual(panel_installation_cost(result, {'braced_panel_installation': 0})['total'], 0.)
        for invalid in (-1, float('nan'), float('inf'), True, None):
            with self.assertRaises(AnalysisError):
                panel_installation_cost(result, {'braced_panel_installation': invalid})

    def test_full_design_total_and_request_include_allowance_without_changing_checks(self):
        helper = design_tests.DesignMechanicsTests()
        helper.catalog = design_tests.DesignMechanicsTests.catalog
        context, selected, candidates = helper.small_context()
        rates = {'shs': 3., 'ub': 4., 'wb': 5.}
        before = solve_selection(context, selected, DesignCriteria(), {**rates, 'braced_panel_installation': 0})
        after = solve_selection(context, selected, DesignCriteria(), rates)
        count = len(context['layout'].pinned)
        self.assertEqual(after['design']['installed_braced_panel_count'], count)
        self.assertEqual(after['design']['installation_cost'], count*50.)
        self.assertAlmostEqual(after['design']['estimated_cost'], before['design']['estimated_cost']+count*50.)
        self.assertEqual(after['design']['materials_and_fittings_cost'], before['design']['estimated_cost'])
        self.assertEqual(after['section_checks'], before['section_checks'])
        self.assertEqual(after['design_request']['cost_rates']['braced_panel_installation'], 50.)


if __name__ == '__main__':
    unittest.main()
