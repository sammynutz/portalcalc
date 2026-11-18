"""Local pressure geometry, independent patch-beam statics and roof-run allocation."""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import copy
from dataclasses import replace
import unittest

from portalcalc.bracing.layout import LayoutGeometry
from portalcalc.wind import WindInputs
from portalcalc.purlins.end_local import end_roof_local_cases
from portalcalc.purlins.beam import solve_run
from portalcalc.purlins.shared_design import _added_actions, _added_patch_actions, solve_shared, shared_check
from purlin_strut_regression import fixture
from portalcalc.bracing.design import DesignCriteria


class EndRoofLocalTests(unittest.TestCase):
    def cases(self, x=1200., **changes):
        values = dict(building_width_m=35.,building_length_m=40.,eave_height_m=8.,roof_pitch_deg=7.5,orientation='Any')
        values.update(changes)
        inputs = WindInputs(**values)
        g = LayoutGeometry.create(values['building_width_m']*1000,values['building_length_m']*1000,
            values['eave_height_m']*1000,values['roof_pitch_deg'],'Gable')
        return end_roof_local_cases(inputs,g,{'tributary_width_mm':1667.,'nearest_installed_x_mm':x})

    def test_local_regions_are_alternatives_with_area_and_side_ratio_limits(self):
        cases = self.cases()
        self.assertEqual({c['local_pressure']['region'] for c in cases},{'RA1','RA2','RC1'})
        for case in cases:
            d = case['local_pressure']
            self.assertEqual(d['a_m'],7.)
            self.assertLessEqual(d['effective_patch_area_m2'],d['area_limit_m2']+1e-8)
            self.assertGreaterEqual(d['effective_patch_area_m2'],d['patch_length_m']**2/4-1e-8)
            self.assertTrue(all(0 <= a < b <= 40000 for a,b,q in case['segments']))
        self.assertEqual({c['local_pressure']['region'] for c in self.cases(x=17500.)},{'RA1','RA2'})
        self.assertIn('RC2',{c['local_pressure']['region'] for c in self.cases(x=17500.,roof_pitch_deg=15.)})

    def test_rc1_corner_patch_is_an_a_half_square(self):
        """RC1 as the CSB local pressure calculator: a/2 along the purlin, rows within a/2 of an eave."""
        cases = self.cases(x=1200.)
        rc1 = [c['local_pressure'] for c in cases if c['local_pressure']['region'] == 'RC1']
        self.assertTrue(rc1)
        for d in rc1:
            self.assertLessEqual(d['patch_length_m'], .5*d['a_m']+1e-9)
            self.assertLessEqual(d['patch_start_from_end_m']+d['patch_length_m'], .5*d['a_m']+1e-9)
        # a = 7 m here: a row 4.5 m from the eave (strip 3.67-5.33 m) is outside a/2 = 3.5 m.
        self.assertNotIn('RC1', {c['local_pressure']['region'] for c in self.cases(x=4500.)})
        self.assertIn('RA1', {c['local_pressure']['region'] for c in self.cases(x=4500.)})

    def test_per_bay_section_matches_the_three_moment_equation(self):
        """Two continuous spans, only span 1 loaded: |M_B| = q L^2 I2 / (8 (I1 + I2))."""
        q, span, i1, i2 = 1., 8000., 1e6, 3e6
        load = {'name': 'span 1', 'roof_n_mm': 0., 'added_n_mm': 0., 'roof_patches': [[0., span, q]]}
        run = solve_run([span, span], [i1, i2], i1, span_type='double', loads=[load])
        support = [s for b in run['cases'][0]['bays'] for s in b['roof']['samples'] if abs(s['x_mm']-span) < 1e-6]
        self.assertTrue(support)
        for sample in support:
            self.assertAlmostEqual(abs(sample['moment_knm']), q*span**2*i2/(8*(i1+i2))/1e6, places=6)
        # Equal sections given per bay are bit-identical to one section for the run.
        lapped = dict(span_type='continuous_lapped', loads=[{'name': 'u', 'roof_n_mm': -1., 'added_n_mm': 0.}])
        self.assertEqual(solve_run([span]*5, [i1]*5, i1, **lapped)['cases'], solve_run([span]*5, i1, i1, **lapped)['cases'])
        # In a lap of unequal plies each ply takes the moment in proportion to its EI.
        mixed = solve_run([span]*3, [i2, i1, i1], i1, **lapped)['cases'][0]['bays']
        left = next(s for s in mixed[0]['roof']['samples'] if abs(s['x_mm']-span) < 1e-6)
        right = next(s for s in mixed[1]['roof']['samples'] if abs(s['x_mm']-span) < 1e-6)
        self.assertAlmostEqual(left['moment_knm']/right['moment_knm'], i2/i1, places=9)

    def test_corner_infills_split_the_widest_gaps_and_keep_the_roof_load(self):
        from portalcalc.purlins.roof import corner_infills
        rows = [{'index': i, 'x_mm': x, 'y_mm': 0., 'tributary_width_mm': w}
                for i, (x, w) in enumerate([(0., 600.), (1200., 1400.), (2800., 1600.), (4400., 1600.)])]
        widths, infills = corner_infills(rows, 2)
        # The two 1600 mm gaps are widest: each gets an infill at its midpoint.
        self.assertEqual([round(f['x_mm']) for f in infills], [2000, 3600])
        self.assertEqual([f['tributary_width_mm'] for f in infills], [800., 800.])
        self.assertEqual(widths, {0: 600., 1: 1000., 2: 800., 3: 1200.})
        # Roof width carried is unchanged: rows lost exactly what the infills took.
        self.assertAlmostEqual(sum(widths.values())+sum(f['tributary_width_mm'] for f in infills),
                               sum(r['tributary_width_mm'] for r in rows))

    def test_scaling_applies_only_outside_the_end_zones(self):
        from portalcalc.purlins.roof import _scaled_outside
        load = {'name': 'x', 'roof_patches': [[0., 10000., -2.], [10000., 30000., -1.]]}
        scaled = _scaled_outside([load], .5, [(0., 8000.), (22000., 30000.)])[0]['roof_patches']
        self.assertEqual(scaled, [[0., 8000., -2.], [8000., 10000., -1.], [10000., 22000., -.5], [22000., 30000., -1.]])

    def test_switched_off_regions_are_dropped_and_the_rest_are_unchanged(self):
        """Table 5.6 region switches (WindInputs.local_pressure_regions), all on by default."""
        from portalcalc.wind import roof_local_pressure_factor
        everything = self.cases()
        without_rc1 = self.cases(local_pressure_regions=('RA1', 'RA2', 'RC2'))
        self.assertEqual({c['local_pressure']['region'] for c in without_rc1}, {'RA1', 'RA2'})
        kept = [c for c in everything if c['local_pressure']['region'] != 'RC1']
        self.assertEqual([c['segments'] for c in without_rc1], [c['segments'] for c in kept])
        self.assertEqual(self.cases(local_pressure_regions=()), [])
        # Edge-strip factor (a = 7 m here): the largest factor among the regions still on.
        def factor(regions, area, proximity):
            inputs = WindInputs(building_width_m=35., building_length_m=40., eave_height_m=8., roof_pitch_deg=7.5,
                                local_pressure_regions=regions)
            return roof_local_pressure_factor(inputs, area, proximity)
        all_on = ('RA1', 'RA2', 'RC1', 'RC2')
        self.assertEqual(factor(all_on, 10., 1.), 2.0)
        self.assertEqual(factor(('RA1', 'RC1', 'RC2'), 10., 1.), 1.5)
        self.assertEqual(factor(('RA2',), 10., 1.), 2.0)
        self.assertEqual(factor(('RA2',), 40., 1.), 1.0)   # beyond RA2's area limit, RA1 is off
        self.assertEqual(factor(('RC1', 'RC2'), 10., 1.), 1.0)
        with self.assertRaises(ValueError):
            WindInputs(local_pressure_regions=('RA1', 'RX9')).validate()

    def test_only_external_pressure_is_amplified_and_outside_patch_returns_to_baseline(self):
        cases = [c for c in self.cases() if c['name'].startswith('ULS') and '/ Front /' in c['name']]
        ra1 = next(c for c in cases if c['local_pressure']['region']=='RA1')
        rc1 = next(c for c in cases if c['local_pressure']['region']=='RC1')
        def q(case,x):return next(v for a,b,v in case['segments'] if a <= x < b)
        m = rc1['local_pressure']
        self.assertAlmostEqual(q(rc1,100),(-2.7*m['kce']-m['cpi']*m['kci'])*m['wind_pressure_kpa']*m['tributary_width_m'])
        self.assertAlmostEqual(q(rc1,10000),q(ra1,10000))
        # Shallow buildings use the roof-specific 2h branch, not the wall a formula.
        self.assertEqual(self.cases(building_width_m=100.,building_length_m=120.,eave_height_m=1.5,roof_pitch_deg=0.)[0]['local_pressure']['a_m'],3.)
        tall = next(c for c in self.cases(eave_height_m=45.) if c['name'].startswith('ULS') and '/ Front /' in c['name'] and c['local_pressure']['region']=='RC1')
        m = tall['local_pressure']
        self.assertAlmostEqual(q(tall,100),(-3.*m['kce']-m['cpi']*m['kci'])*m['wind_pressure_kpa']*m['tributary_width_m'])

    def test_reverse_wind_mirrors_local_patches(self):
        front = next(c for c in self.cases() if c['name'].startswith('ULS') and '/ Front /' in c['name'])
        back = next(c for c in self.cases() if c['name'].startswith('ULS') and '/ Back /' in c['name'])
        mirrored = sorted([[40000-b,40000-a,q] for a,b,q in back['segments']])
        self.assertEqual(len(front['segments']),len(mirrored))
        for a,b in zip(front['segments'],mirrored):
            for x,y in zip(a,b):self.assertAlmostEqual(x,y,places=8)

    def test_partial_udl_matches_independent_support_reactions_and_peak_moment(self):
        load = {'name':'patch','roof_n_mm':0.,'added_n_mm':0.,'roof_patches':[[0.,4000.,1.]]}
        run = solve_run([8000.],6e6,6e6,loads=[load])
        case = run['cases'][0]
        roof = case['bays'][0]['roof']
        self.assertAlmostEqual(case['applied_load_n'],4000.,places=5)
        self.assertLess(abs(case['vertical_balance_error_n']),1e-5)
        self.assertAlmostEqual(roof['shear_kn'],3.,places=7)  # Rleft = 3qL/8
        self.assertAlmostEqual(roof['positive_moment_knm'],4.5,places=7)  # 9qL^2/128
        exact = _added_patch_actions([[0.,4000.,1.]],8000.,6e6,0.)
        self.assertAlmostEqual(exact['positive_moment_knm'],4.5,places=9)
        self.assertAlmostEqual(exact['shear_kn'],3.,places=9)
        self.assertAlmostEqual(exact['deflection_mm'],roof['deflection_mm'],places=6)
        uniform = _added_actions(1.,8000.,6e6,0.)
        patched = _added_patch_actions([[0.,8000.,1.]],8000.,6e6,0.)
        for key in ('positive_moment_knm','negative_moment_knm','shear_kn','deflection_mm'):
            self.assertAlmostEqual(uniform[key],patched[key],places=8)
        # A real patch boundary close to a regular mesh station must not form
        # an ill-conditioned sliver element or lose its load contribution.
        edge = 8000/3-2.06
        load['roof_patches'] = [[0.,edge,1.]]
        recovered = solve_run([8000.],6e6,6e6,loads=[load])['cases'][0]
        exact = _added_patch_actions(load['roof_patches'],8000.,6e6,0.)
        self.assertAlmostEqual(recovered['applied_load_n'],edge,places=6)
        self.assertAlmostEqual(recovered['bays'][0]['roof']['positive_moment_knm'],exact['positive_moment_knm'],places=6)

    def test_shared_and_existing_only_runs_include_local_case_without_losing_load(self):
        from portalcalc.bracing.components import StrutPlan,location_options
        from portalcalc.purlins.beam_design import with_beam_demands
        assemblies = copy.deepcopy(fixture())
        baseline = solve_shared(assemblies,[8000.,8000.],0)
        local = {'name':'ULS outward / End local synthetic','segments':[[0.,4000.,-3.],[4000.,16000.,-1.]],
                 'local_pressure':{'region':'RC1','kl':3.}}
        for assembly in assemblies:
            assembly.location['purlin']['beam_inputs']['end_roof_local_cases'] = [local]
        run = solve_shared(assemblies,[8000.,8000.],0)
        case = run['cases'][-1]
        self.assertAlmostEqual(case['applied_load_n'],-24000.,places=5)
        self.assertAlmostEqual(case['applied_load_n']+case['support_reaction_n'],0.,places=5)
        self.assertLess(abs(case['vertical_balance_error_n']),1e-5)
        component = assemblies[0].components[-1]
        entry = {'line':'0','bay':0,'error':None}
        old = shared_check(baseline,entry,component,-5.,'ULS test','ULS',150.,DesignCriteria())
        new = shared_check(run,entry,component,-5.,'ULS test','ULS',150.,DesignCriteria())
        self.assertEqual(new['axial_ratio'],old['axial_ratio'])
        self.assertGreater(new['reference_envelope_ratio'],old['reference_envelope_ratio'])
        self.assertIn('End local',new['governing_beam_case'])
        existing = {a.location['id']:location_options(a.location,[])[0] for a in assemblies}
        result = with_beam_demands({'analysis_layout':{'geometry':{'bay_stations_mm':[0,8000,16000]}}},StrutPlan(existing,None))
        self.assertIn('0',result['purlin_beams']['runs'])
        self.assertIn(local['name'],[c['name'] for c in result['purlin_beams']['runs']['0']['cases']])

    def test_roof_line_repair_coordinates_neighbour_pickup_before_pruning(self):
        from portalcalc.bracing.components import StrutPlan,location_options
        from portalcalc.bracing.design import member_assessment
        from portalcalc.bracing.mixed_sizing import repair_roof_line
        from portalcalc.purlins.beam_design import with_beam_demands
        assemblies = copy.deepcopy(fixture(active=()))
        for assembly in assemblies:
            assembly.location['purlin']['beam_inputs']['end_roof_local_cases'] = [{
                'name':'ULS outward / End local SYNTHETIC repair benchmark',
                'segments':[[0.,8000.,-4.],[8000.,16000.,-.6]],'local_pressure':{'region':'synthetic'}}]
        plan = StrutPlan({a.location['id']:a for a in assemblies},None)
        physical = [{'id':a.location['id'],'kind':'longitudinal_strut','length_mm':8000.,'axial_n':-20000.} for a in assemblies]
        result = {'analysis_layout':{'geometry':{'bay_stations_mm':[0,8000,16000]}},
            'cases':[{'case':{'name':level+' synthetic','level':level},'members':copy.deepcopy(physical)} for level in ('ULS','SLS')]}
        criteria,rates = DesignCriteria(),{'purlin':1.9,'shs':3.,'ub':4.,'wb':5.}
        result = with_beam_demands(result,plan)
        # The second bay cannot cure a shared-support moment caused by the
        # first bay's roof load by changing only its own addition.
        key = assemblies[1].location['id']
        local = [o for o in location_options(assemblies[1].location,[]) if o.arrangement in {'existing_purlins','shared_purlin','shared_purlin_pair'}]
        self.assertTrue(all(not member_assessment(with_beam_demands(result,replace(plan,members={**plan.members,key:o}),only_lines={0}),1,o,criteria)['passes'] for o in local))
        repaired,screens = repair_roof_line(result,plan,0,criteria,rates)
        self.assertIsNotNone(repaired)
        checked = with_beam_demands(result,replace(plan,members=repaired))
        self.assertTrue(all(member_assessment(checked,i,repaired[a.location['id']],criteria)['passes'] for i,a in enumerate(assemblies)))
        self.assertTrue(all(a.arrangement=='existing_purlins' for a in plan.members.values()))
        self.assertLessEqual(screens,2+2*len(local))
        impossible = copy.deepcopy(result)
        for case in impossible['cases']:
            for member in case['members']:member['axial_n'] = -2e6
        rejected,screens = repair_roof_line(impossible,plan,0,criteria,rates)
        self.assertIsNone(rejected)
        self.assertLessEqual(screens,2)


if __name__ == '__main__':
    unittest.main()
