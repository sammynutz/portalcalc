"""Five-bay load-path rule, planning, constraints and acceptance regression."""
import copy
from dataclasses import replace
from itertools import combinations
import unittest
from unittest import mock

from bracing_analysis import AnalysisError
from bracing_layout import BracingLayout,LayoutGeometry
from bracing_spacing import bay_panels,max_bay_spacing,repair_spacing,spacing_assessment


def long_layout(count=20):
    g=LayoutGeometry.create(12000.,count*8000.,6000.,7.5,'Gable')
    layout=BracingLayout(g,blocked=[f'right_wall/bay/{i}' for i in range(count)])
    for bay in (0,count-1):
        roof,walls=bay_panels(layout,bay)
        layout.pinned |= roof | (walls&layout.available)
    return layout


class SpacingTests(unittest.TestCase):
    def test_end_only_long_building_gets_regular_full_roof_wall_anchors(self):
        layout=long_layout();before=copy.deepcopy(layout.to_dict())
        self.assertFalse(spacing_assessment(layout)['passes'])
        result=repair_spacing({'layout':layout},layout.pinned)
        checked=spacing_assessment(layout,selected=result)
        self.assertTrue(checked['passes']);self.assertEqual(checked['anchor_bays'],[1,6,11,16,20])
        self.assertTrue(layout.pinned<=result);self.assertTrue(result<=layout.available)
        self.assertEqual(layout.to_dict(),before)

    def test_continuous_eave_strip_is_not_an_intermediate_wall_anchor(self):
        layout=long_layout(15)
        layout.pinned |= {f'roof/bay/{b}/strip/0' for b in range(15)}
        self.assertEqual(spacing_assessment(layout)['anchor_bays'],[1,15])
        result=repair_spacing({'layout':layout},layout.pinned)
        checked=spacing_assessment(layout,selected=result)
        self.assertEqual(checked['anchor_bays'],[1,6,11,15])
        for bay in (5,10):
            roof,walls=bay_panels(layout,bay)
            self.assertTrue(roof<=result);self.assertTrue(walls&result)

    def test_blocked_preferred_bay_moves_anchor_and_impossible_run_fails(self):
        layout=long_layout(15)
        layout.blocked.add('roof/bay/5/strip/1')
        before=copy.deepcopy(layout.to_dict())
        result=repair_spacing({'layout':layout},layout.pinned)
        self.assertTrue(spacing_assessment(layout,selected=result)['passes'])
        self.assertFalse(result&layout.blocked);self.assertEqual(layout.to_dict(),before)
        for b in range(3,12):layout.blocked.add(f'roof/bay/{b}/strip/1')
        with self.assertRaisesRegex(AnalysisError,'Blocked panels prevent'):
            repair_spacing({'layout':layout},layout.pinned)

    def test_limits_edge_runs_required_panels_and_non_uniform_bays(self):
        for value in (True,False,0,-1,51,5.0,'5',None):
            with self.assertRaises(AnalysisError):max_bay_spacing({'max_braced_bay_spacing':value})
        layout=long_layout(7)
        self.assertFalse(spacing_assessment(layout)['passes']) # five unbraced bays between 1 and 7
        self.assertTrue(spacing_assessment(long_layout(6))['passes']) # four between 1 and 6
        required={'roof/bay/2/strip/1','front_end/panel/0'}
        result=repair_spacing({'layout':layout,'max_braced_bay_spacing':3},layout.pinned|required)
        self.assertTrue(required<=result);self.assertTrue(spacing_assessment(layout,3,result)['passes'])
        roof,walls=bay_panels(layout,5)
        self.assertFalse(spacing_assessment(layout,5,roof|(walls&layout.available))['passes']) # first five bays have no anchor
        g=LayoutGeometry.create(12000.,50000.,6000.,7.5,'Gable',bay_stations_mm=[0,5000,11000,18000,26000,35000,43000,50000])
        varied=BracingLayout(g,blocked=layout.blocked)
        varied.pinned=layout.pinned
        self.assertTrue(spacing_assessment(varied,3,repair_spacing({'layout':varied,'max_braced_bay_spacing':3},varied.pinned))['passes'])

    def test_added_anchor_count_matches_independent_small_exhaustive_check(self):
        layout=long_layout(12);layout.blocked.add('left_wall/bay/5')
        result=repair_spacing({'layout':layout},layout.pinned)
        actual=len(spacing_assessment(layout,selected=result)['anchor_bays'])-2
        candidates=[b for b in range(1,11) if b!=5]
        minimum=None
        for count in range(len(candidates)+1):
            for extra in combinations(candidates,count):
                anchors={0,11,*extra}
                if all(any(b in anchors for b in range(start,start+5)) for start in range(8)):
                    minimum=count;break
            if minimum is not None:break
        self.assertEqual(actual,minimum)

    def test_both_search_strategies_screen_only_spacing_compliant_layouts(self):
        from bracing_layout_search_regression import PracticalLayoutTests,LayoutSearchTests
        from bracing_layout_search import find_layout
        PracticalLayoutTests.setUpClass()
        c,s,pool,criteria,rates=PracticalLayoutTests().fixture()
        layout=long_layout(20);c={**c,'layout':layout,'nominal_layout':layout.to_dict()}
        before=copy.deepcopy(layout.to_dict())
        def screen(context,chosen,*args,**kwargs):
            self.assertTrue(spacing_assessment(context['layout'])['passes'])
            return LayoutSearchTests.checked(1000+len(context['layout'].pinned))
        def size(context,chosen,*args,**kwargs):return screen(context,chosen),chosen
        for strategy in ('practical','cost'):
            with mock.patch('bracing_layout_search.solve_selection',side_effect=screen),mock.patch('bracing_layout_search.auto_size',side_effect=size):
                result,chosen,selected=find_layout(c,s,pool,criteria,rates,strategy=strategy,max_trials=2)
            self.assertTrue(spacing_assessment(selected)['passes'])
            self.assertEqual(result['layout_search']['max_braced_bay_spacing'],5)
            self.assertEqual(layout.to_dict(),before)

    def test_rule_prevents_strength_only_pass_and_section_sizing_cannot_cure_it(self):
        from bracing_design_regression import DesignMechanicsTests
        from bracing_design import DesignCriteria,solve_selection,attach_design,auto_size
        from bracing_sizing_diagnostics import SizingStopped,result_summary
        DesignMechanicsTests.setUpClass()
        c,s,pool=DesignMechanicsTests().small_context();rates={'shs':3.,'ub':4.,'wb':5.}
        baseline=solve_selection(c,s,DesignCriteria(),rates)
        self.assertTrue(baseline['design']['passes'])
        layout=long_layout(20);context={**c,'layout':layout,'nominal_layout':layout.to_dict()}
        result=copy.deepcopy(baseline);result['analysis_layout']=layout.to_dict()
        checked=attach_design(result,s,replace(DesignCriteria(),include_serviceability=False),rates,context)
        self.assertTrue(checked['design']['member_checks_pass'])
        self.assertFalse(checked['design']['passes']);self.assertFalse(checked['design']['layout_spacing']['passes'])
        self.assertIn('Braced-bay spacing: FAIL',result_summary(checked)['text'])
        with mock.patch('bracing_design.stiffness_ceiling',side_effect=AssertionError('No section growth for spacing')):
            with self.assertRaises(SizingStopped) as caught:
                auto_size(context,s,pool,DesignCriteria(),rates,initial_result=checked)
        self.assertEqual(caught.exception.diagnostic['constraint'],'braced_bay_spacing')


if __name__=='__main__':unittest.main(verbosity=2)
