"""ULS transverse pressure statics, mirroring and full-matrix verification."""
import copy
from dataclasses import replace
import math
from types import SimpleNamespace
import unittest

import numpy as np

from bracing_analysis import PortalReduction,build_braced_building,end_wall_load_vector,solve_building_case
from bracing_analysis_regression import geometry,frame,fixed_layout
from bracing_roof_loads import building_roof_cases,_snapshot
from bracing_analysis_desktop import end_wind_cases
from bracing_side_wind import apply_element_patch,roof_cpe_segments
from wind import WindInputs,calculate_wind


def fixture(g=None,inputs=None):
    g=g or geometry(end_wall_stations_mm=[0,2000,4000,6000])
    s=frame(g);macro=PortalReduction(s,[(x,g.roof_z(x)) for x in g.roof_stations])
    inputs=inputs or WindInputs(building_width_m=g.span_mm/1000,building_length_m=g.length_mm/1000,
        eave_height_m=g.eave_mm/1000,roof_pitch_deg=g.pitch_deg,roof_shape=g.roof_shape,
        orientation='Any',front_wall_clad=True,back_wall_clad=True,left_wall_clad=True,right_wall_clad=True)
    cases,basis=building_roof_cases(end_wind_cases(inputs),macro,g,inputs,{'loads':{'g_kpa':'.15','solar_kpa':'.1','q_kpa':'.25'}})
    return g,s,macro,inputs,cases,basis


class SideWindTests(unittest.TestCase):
    def test_partial_element_patch_matches_independent_cantilever_statics(self):
        from main import Node,FrameElement2D,Structure2D
        s=Structure2D();a,b=Node(1,0.,3000.),Node(2,6000.,3000.)
        for n in (a,b):s.add_node(n)
        for dof in (a.ux,a.uy,a.rz):s.add_support(dof)
        e=FrameElement2D(1,a,b,200000.,SimpleNamespace(A=1500.,I_analysis=12e6,name='Test'),group='TOP');s.add_element(e)
        macro=PortalReduction(s,[(3000.,3000.)]);equivalents={}
        apply_element_patch(s,e,.25,.75,0.,-2.,equivalents)
        state=_snapshot(s,equivalents);loaded=macro.with_loads(state['loads_n'],state['member_equivalent_loads'])
        result=loaded.recover(np.linalg.solve(macro.stiffness,loaded.equivalent_load))
        np.testing.assert_allclose(result['member_end_forces'][0]['local_n_n_nmm'],[0.,6000.,18e6,0.,0.,0.],atol=1e-6)
        self.assertAlmostEqual(sum(state['loads_n'][n.native_uy] for n in s.nodes),-6000.)

    def test_banded_end_suction_preserves_resultant_and_span_moment(self):
        g,s,macro,inputs,cases,basis=fixture()
        model,roof,dofs,lookup=build_braced_building(fixed_layout(g,end=True,both_ends=True),macro,1500,200,200,200,1500)
        bands=[(0.,1100.,-.7),(1100.,3700.,-.4),(3700.,6000.,-.2)]
        force,work=end_wall_load_vector(model,fixed_layout(g),lookup,-.7,.7,
                                        {'front_end':bands,'back_end':[(a,b,-p) for a,b,p in bands]})
        expected,moment=0.,0.;slope=math.tan(math.radians(g.pitch_deg))
        for a,b,p in bands:
            for lo,hi,intercept,m in [(a,min(b,3000.),g.eave_mm,slope),(max(a,3000.),b,g.eave_mm+6000*slope,-slope)]:
                if hi<=lo:continue
                expected+=p/1000*(intercept*(hi-lo)+m*(hi*hi-lo*lo)/2)
                moment+=p/1000*(intercept*(hi*hi-lo*lo)/2+m*(hi**3-lo**3)/3)
        front_roof=sum(force[3*i+1] for i,n in enumerate(model.nodes) if n[1]==0 and n[2]>0)
        front_moment=sum(n[0]*force[3*i+1] for i,n in enumerate(model.nodes) if n[1]==0)
        self.assertAlmostEqual(front_roof,expected/2,places=7)
        self.assertAlmostEqual(front_moment,moment,places=5)
        self.assertAlmostEqual(sum(force[1::3]),0.,places=7)
        self.assertAlmostEqual(work[0]['roof_resultant_n'],expected/2,places=7)
        with self.assertRaisesRegex(ValueError,'continuously'):
            end_wall_load_vector(model,fixed_layout(g),lookup,0.,0.,{'front_end':[(0.,2000.,1.)]})

    def test_both_directions_pressure_branches_gravity_factors_and_source_bearings(self):
        g,s,macro,inputs,cases,basis=fixture()
        family=[c for c in cases if c.get('wind_family')=='transverse']
        # 16 ULS (0.9G/1.2G + Wu) and 16 SLS (Ws, G + Ws) side-wind cases.
        self.assertEqual(len(family),32)
        transverse=[c for c in family if c['level']=='ULS']
        service=[c for c in family if c['level']=='SLS']
        self.assertEqual((len(transverse),len(service)),(16,16))
        self.assertEqual({c['g_factor'] for c in transverse},{.9,1.2})
        self.assertEqual({c['g_factor'] for c in service},{0.,1.})
        self.assertEqual({c['cpe_case'] for c in transverse},{'Left +','Left -','Right +','Right -'})
        self.assertEqual({c['cpe_case'] for c in service},{'Left +','Left -','Right +','Right -'})
        left=next(c for c in transverse if c['cpe_case']=='Left +' and c['cpi_case']=='Cpi +' and c['g_factor']==.9)
        right=next(c for c in transverse if c['cpe_case']=='Right +' and c['cpi_case']=='Cpi +' and c['g_factor']==.9)
        reflected=sorted((g.span_mm-b,g.span_mm-a,cp) for a,b,cp in left['transverse_pressure_pattern']['roof_cpe_segments_mm'])
        self.assertEqual(reflected,right['transverse_pressure_pattern']['roof_cpe_segments_mm'])
        for l,r in zip(left['portal_loads'],right['portal_loads']):
            self.assertAlmostEqual(sum(l['loads_n'][n.native_ux] for n in s.nodes),-sum(r['loads_n'][n.native_ux] for n in s.nodes),places=7)
            self.assertAlmostEqual(sum(l['loads_n'][n.native_uy] for n in s.nodes),sum(r['loads_n'][n.native_uy] for n in s.nodes),places=7)
        _,_,_,_,oriented,_=fixture(inputs=replace(inputs,orientation=27.))
        self.assertEqual({c['source_direction'] for c in oriented if c.get('wind_family')=='transverse' and c['cpe_case'].startswith('Left')},{243.})
        self.assertEqual({c['source_direction'] for c in oriented if c.get('wind_family')=='transverse' and c['cpe_case'].startswith('Right')},{63.})

    def test_pitched_downwind_pressure_is_retained_in_both_alternatives_and_drag_threshold(self):
        g,s,macro,inputs,cases,basis=fixture(geometry(pitch_deg=20.))
        w=calculate_wind(inputs)
        a,b=roof_cpe_segments(w,g,False,True),roof_cpe_segments(w,g,False,False)
        self.assertEqual(a[-1],b[-1]);self.assertNotEqual(a[0][2],b[0][2])
        self.assertLess(a[-1][2],0.)
        self.assertTrue(all(c['transverse_roof_drag']['signed_x_force_kn']==0. for c in cases if c.get('wind_family')=='transverse'))
        wide=geometry(span_mm=50000.,eave_mm=3000.,pitch_deg=0.)
        _,s,_,_,cases,_=fixture(wide)
        drag_case=next(c for c in cases if c.get('wind_family')=='transverse' and c['cpe_case']=='Left +' and c['g_factor']==.9)
        expected=drag_case['wu_kpa']*.01*16.*(50.-4*3.)
        self.assertAlmostEqual(drag_case['transverse_roof_drag']['signed_x_force_kn'],expected)

    def test_side_wind_reduced_network_matches_original_portals_and_reaches_end_braces(self):
        g,s,macro,inputs,cases,basis=fixture()
        layout=fixed_layout(g,end=True,both_ends=True)
        model,roof,dofs,lookup=build_braced_building(layout,macro,1500,200,200,200,1500)
        case=next(c for c in cases if c.get('wind_family')=='transverse' and c['cpe_case']=='Left +' and c['cpi_case']=='Cpi +' and c['g_factor']==.9)
        result=solve_building_case(model,layout,macro,dofs,lookup,case)
        network,_=end_wall_load_vector(model,layout,lookup,case['front_kpa'],case['back_kpa'],case['end_wall_pressure_segments'])
        nf=len(macro.free);count=len(roof);size=count*nf+sum(map(len,roof))
        transform=np.zeros((3*len(model.nodes),size));full=np.zeros((size,size));rhs=np.zeros(size);iy=count*nf
        for i,nodes in enumerate(roof):
            block=slice(i*nf,(i+1)*nf)
            full[block,block]=macro.k[np.ix_(macro.free,macro.free)]
            rhs[block]=np.asarray(case['portal_loads'][i]['loads_n'])[macro.free]
            for j,node in enumerate(nodes):
                transform[3*node,block]=macro.h[2*j,macro.free]
                transform[3*node+2,block]=macro.h[2*j+1,macro.free]
                transform[3*node+1,iy]=1.;iy+=1
        for m,response in zip(model.members,result['members']):
            if m.tension_only and response['state']=='slack':continue
            delta=np.subtract(model.nodes[m.end],model.nodes[m.start]);length=np.linalg.norm(delta)
            row=delta/length@(transform[3*m.end:3*m.end+3]-transform[3*m.start:3*m.start+3])
            full+=m.modulus_mpa*m.area_mm2/length*np.outer(row,row)
        original=np.linalg.solve(full,rhs+transform.T@network)
        np.testing.assert_allclose(transform@original,np.asarray(result['displacements_mm']).reshape(-1),atol=1e-7)
        self.assertLess(max(map(abs,result['balance_force_error_n'])),1e-5)
        self.assertLess(max(map(abs,result['balance_moment_error_nmm'])),.01)
        self.assertGreater(result['load_path']['peak_brace_tension_n']['end_brace'],1.)
        self.assertGreater(abs(result['load_path']['member_base_reactions_n']['front_end'][0]),1.)
        self.assertAlmostEqual(result['applied_resultant_n'][1],0.,places=7)
        self.assertTrue(all(m['axial_n']>=0 for m in result['members'] if m['tension_only']))


if __name__=='__main__':unittest.main(verbosity=2)
