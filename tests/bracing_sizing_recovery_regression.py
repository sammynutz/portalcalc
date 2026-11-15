"""Cost-screen bounds and transactional recovery from unstable brace downgrades."""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import copy
from types import SimpleNamespace
import unittest
from unittest import mock

from portalcalc.bracing.analysis import AnalysisError
from portalcalc.bracing.components import StrutPlan, location_options
from bracing_components_regression import location, fixture
from portalcalc.bracing.design import DesignCriteria
from portalcalc.bracing.mixed_sizing import auto_size_mixed
from portalcalc.bracing.sizing_diagnostics import SizingStopped


class SizingRecoveryTests(unittest.TestCase):
    def run_search(self, *, fast=True, budget=4, retry_fails=False, unstable=True):
        loc=location()
        profiles=[SimpleNamespace(name=name,A=area,group_name='SHS',shape_type='HOLLOW')
                  for name,area in [('Expensive SHS',2000.),('Cheaper SHS',1000.)]]
        options=location_options(loc,profiles)
        old=options[0]; cheap=options[-1]; expensive=options[-2]
        strong={'label':'Stable brace','area_mm2':300.}
        soft={'label':'Softer brace','area_mm2':100.}
        selected={'strut':StrutPlan({loc['id']:old},profiles[0]),'end_post':profiles[0],
                  **{g:strong for g in ('roof_brace','wall_brace','end_brace')}}
        initial=fixture()
        for case in initial['cases']:
            case['members'].append({**case['members'][0],'id':'wall','kind':'wall_brace'})
        initial.update(nodes=[{'label':'Fixture','point_mm':[0,0,3000]}],
            design_request={'sections':{g:'Original' for g in selected}},section_checks={})
        initial['design']={'passes':False,'estimated_cost':100.,'member_checks_pass':False,
            'taut_tangent_verified':True,'global_sls':{'ratio':.5,'case':'SLS','node':0,'movement_mm':10.,'limit_mm':20.}}
        checked=copy.deepcopy(initial);checked['design'].update(passes=True,member_checks_pass=True)
        pool={'strut':profiles,**{g:[soft,strong] for g in selected if g!='strut'}}
        assessments=[];calls=[]
        def assessment(result,index,option,criteria):
            assessments.append(option.name)
            return {'passes':option.name==cheap.name,'case_checks':[]}
        def solve(context,choice,criteria,rates):
            calls.append(choice)
            if len(calls)==1:
                raise AnalysisError('ULS side wind: Unstable model' if unstable else 'Cancelled fixture')
            if retry_fails:
                raise AnalysisError('Retained braces also unstable')
            self.assertEqual(choice['wall_brace'],strong)
            self.assertEqual(choice['strut'].members[loc['id']].name,cheap.name)
            return checked
        rates={'ub':3.,'shs':3.,'wb':3.,'purlin':3.}
        with mock.patch('portalcalc.bracing.design.solve_selection',side_effect=solve), \
             mock.patch('portalcalc.bracing.design.stiffness_ceiling',return_value=1e10), \
             mock.patch('portalcalc.bracing.design.member_assessment',side_effect=assessment), \
             mock.patch('portalcalc.bracing.design.group_assessments',return_value=[{'passes':True}]), \
             mock.patch('portalcalc.bracing.mixed_sizing.location_options',return_value=[expensive,cheap,old]), \
             mock.patch('portalcalc.bracing.mixed_sizing.policy_eligible',return_value=True), \
             mock.patch('portalcalc.bracing.mixed_sizing.candidate_rank',return_value=0), \
             mock.patch('portalcalc.bracing.mixed_sizing.selection_score',return_value=(0,100.)), \
             mock.patch('portalcalc.bracing.mixed_sizing.reduce_strut_brace_cost',side_effect=lambda c,b,*a:(b,[],0)), \
             mock.patch('portalcalc.bracing.design.reduce_verified_cost',side_effect=lambda c,b,*a:(b,[],0,0)), \
             mock.patch('portalcalc.purlins.beam_design.with_beam_demands',side_effect=lambda r,*a,**kw:r):
            try:
                answer=auto_size_mixed({},selected,pool,DesignCriteria(),rates,
                    max_solves=budget,fast=fast,initial_result=initial)
            except SizingStopped as error:
                answer=error
        self.assertNotIn(expensive.name,assessments)
        self.assertIs(selected['strut'].members[loc['id']],old)
        self.assertIs(selected['wall_brace'],strong)
        self.assertFalse(initial['design']['passes'])
        return answer,calls

    def test_rejected_downgrade_recovers_without_expensive_local_screens(self):
        for fast in (True,False):
            with self.subTest(fast=fast):
                answer,calls=self.run_search(fast=fast)
                result,chosen=answer
                self.assertTrue(result['design']['passes'])
                self.assertEqual(len(calls),2)
                self.assertEqual(result['sizing_search']['exact_solves'],3)
                records=result['sizing_search']['stability_recovery_trials']
                self.assertEqual(len(records),1)
                self.assertTrue(records[0]['verified'])
                self.assertTrue(records[0]['passes'])
                self.assertEqual(chosen['wall_brace']['label'],'Stable brace')

    def test_retry_consumes_budget_and_never_adopts_failed_candidates(self):
        for budget,retry_fails,unstable,attempts in [(2,False,True,1),(3,True,True,2),(3,False,False,1)]:
            with self.subTest(budget=budget,retry_fails=retry_fails,unstable=unstable):
                answer,calls=self.run_search(budget=budget,retry_fails=retry_fails,unstable=unstable)
                self.assertIsInstance(answer,SizingStopped)
                self.assertEqual(len(calls),attempts)
                self.assertFalse(answer.result['design']['passes'])
                self.assertEqual(answer.result['design_request']['sections']['wall_brace'],'Original')


if __name__=='__main__':
    unittest.main()
