"""Independent checks for loaded-portal condensation and 3D roof actions."""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import copy
from dataclasses import replace
import unittest
from types import SimpleNamespace

import numpy as np

from portalcalc.bracing.analysis import PortalReduction, build_braced_building, solve_building_case, end_wall_load_vector
from bracing_analysis_regression import geometry, frame, fixed_layout
from portalcalc.bracing.roof_loads import building_roof_cases, frame_strip_integrals, _snapshot
from portalcalc.wind import WindInputs
from portalcalc.bracing.analysis_desktop import end_wind_cases


class RoofLoadTests(unittest.TestCase):
    def test_normal_roof_pressure_projection_and_open_wall_exclusion(self):
        # Short building: every roof tributary lies in the first Table 5.3(A)
        # band, h/d >= 1, Cpe=-1.3. Areas <10 m2 give Ka=1. Independent uniform
        # pressure resultants check signs, N/kN conversion and slope projection.
        g=geometry(length_mm=1000.,bay_stations_mm=[0.,500.,1000.])
        s=frame(g);macro=PortalReduction(s,[(x,g.roof_z(x)) for x in g.roof_stations])
        inputs=WindInputs(building_width_m=6.,building_length_m=1.,eave_height_m=3.,
                          roof_pitch_deg=10.,orientation='Any',front_wall_clad=True,
                          back_wall_clad=True,left_wall_clad=True,right_wall_clad=True)
        bases=end_wind_cases(inputs)
        source={'loads':{}}
        cases,_=building_roof_cases(bases,macro,g,inputs,source)
        chosen=next(c for c in cases if c['level']=='SLS' and c.get('g_factor')==0.
                    and c.get('cpi_case')=='Cpi +' and c.get('cpe_case')=='External max'
                    and 'Front to back' in c['name'])
        base=next(c for c in bases if c['name'].startswith('SLS / Front to back / Cpi +'))
        self.assertAlmostEqual(base['cpi'],.2)
        q=base['wu_kpa']*base['wind_pressure_scale']
        net_upward=q*(1.3*.9+.2)
        for state,width in zip(chosen['portal_loads'],(.25,.5,.25)):
            self.assertAlmostEqual(sum(state['loads_n'][n.native_uy] for n in s.nodes),net_upward*6*width*1000,places=6)
            self.assertAlmostEqual(sum(state['loads_n'][n.native_ux] for n in s.nodes),0.,places=6)
        # Hold the primary pressure coefficients fixed to isolate the physical
        # application mask: an unclad wall must receive no normal line load.
        opened,_=building_roof_cases(bases,macro,g,replace(inputs,right_wall_clad=False),source)
        open_case=next(c for c in opened if c['name']==chosen['name'])
        right_column=next(e for e in s.elements if e.group=='COLUMN' and e.start.x==6000.)
        self.assertTrue(all(str(right_column.id) not in state['member_equivalent_loads'] for state in open_case['portal_loads']))

    def test_frame_reactions_conserve_force_and_moment_and_mirror(self):
        stations=[0.,6000.,14000.,24000.]
        segments=[(0.,5000.,2.),(5000.,19000.,-.5),(19000.,24000.,1.3)]
        weights=frame_strip_integrals(stations,segments)
        total=sum((b-a)*q for a,b,q in segments)
        moment=sum((b-a)*q*(a+b)/2 for a,b,q in segments)
        self.assertAlmostEqual(sum(weights),total)
        self.assertAlmostEqual(np.dot(weights,stations),moment)
        mirrored=frame_strip_integrals(stations,segments,True)
        self.assertAlmostEqual(sum(mirrored),total)
        self.assertAlmostEqual(np.dot(mirrored,stations),24000*total-moment)
        np.testing.assert_allclose(frame_strip_integrals(stations,[(0,24000,1.)]),[3000,7000,9000,5000])

    def test_distributed_load_recovery_matches_cantilever_statics(self):
        from portalcalc.desktop.main_window import Node,FrameElement2D,Structure2D
        s=Structure2D();a,b=Node(1,0.,3000.),Node(2,6000.,3000.)
        for n in (a,b):s.add_node(n)
        for dof in (a.ux,a.uy,a.rz):s.add_support(dof)
        e=FrameElement2D(1,a,b,200000.,SimpleNamespace(A=1500.,I_analysis=12e6,name='Test'),group='TOP')
        s.add_element(e)
        macro=PortalReduction(s,[(3000.,3000.)])
        s.add_uniform_global_line_load(e,fy_n_per_mm=-2.)
        state=_snapshot(s)
        loaded=macro.with_loads(state['loads_n'],state['member_equivalent_loads'])
        movement=np.linalg.solve(macro.stiffness,loaded.equivalent_load)
        recovered=loaded.recover(movement)
        np.testing.assert_allclose(recovered['member_end_forces'][0]['local_n_n_nmm'],
                                   [0.,12000.,36e6,0.,0.,0.],atol=1e-6)
        self.assertAlmostEqual(recovered['support_reactions'][0]['fz_n'],12000.)
        self.assertAlmostEqual(recovered['support_reactions'][0]['m_xz_nmm'],36e6)
        # A new load state must not contaminate the unloaded stiffness macro.
        np.testing.assert_array_equal(macro.loads,np.zeros(len(macro.loads)))

    def test_loaded_network_matches_uncondensed_portal_matrix(self):
        g=geometry(end_wall_stations_mm=[0,2000,4000,6000]);layout=fixed_layout(g,end=True)
        s=frame(g);macro=PortalReduction(s,[(x,g.roof_z(x)) for x in g.roof_stations])
        model,roof,dofs,lookup=build_braced_building(layout,macro,1500,200,200,200,1500)
        states=[]
        for i in range(len(roof)):
            s.clear_loads()
            for e in s.elements:
                if e.group=='TOP':s.add_uniform_global_line_load(e,fy_n_per_mm=-.8*(.5 if i in (0,len(roof)-1) else 1.))
            states.append(_snapshot(s))
        case={'name':'Loaded independent matrix','front_kpa':1.1,'back_kpa':.3,'portal_loads':states}
        result=solve_building_case(model,layout,macro,dofs,lookup,case)
        network,_=end_wall_load_vector(model,layout,lookup,1.1,.3)
        nf=len(macro.free);npf=len(roof);ny=sum(map(len,roof));size=npf*nf+ny
        transform=np.zeros((3*len(model.nodes),size));full=np.zeros((size,size));rhs=np.zeros(size)
        iy=npf*nf
        for i,nodes in enumerate(roof):
            full[i*nf:(i+1)*nf,i*nf:(i+1)*nf]=macro.k[np.ix_(macro.free,macro.free)]
            rhs[i*nf:(i+1)*nf]=np.asarray(states[i]['loads_n'])[macro.free]
            for j,node in enumerate(nodes):
                transform[3*node,i*nf:(i+1)*nf]=macro.h[2*j,macro.free]
                transform[3*node+2,i*nf:(i+1)*nf]=macro.h[2*j+1,macro.free]
                transform[3*node+1,iy]=1.;iy+=1
        for member,response in zip(model.members,result['members']):
            if member.tension_only and response['state']=='slack':continue
            delta=np.subtract(model.nodes[member.end],model.nodes[member.start]);length=np.linalg.norm(delta)
            row=delta/length@(transform[3*member.end:3*member.end+3]-transform[3*member.start:3*member.start+3])
            full+=member.modulus_mpa*member.area_mm2/length*np.outer(row,row)
        original=np.linalg.solve(full,rhs+transform.T@network)
        np.testing.assert_allclose(transform@original,np.asarray(result['displacements_mm']).reshape(-1),atol=1e-7)
        for i,recovered in enumerate(result['portals']):
            full_d=np.zeros(len(macro.k));full_d[macro.free]=original[i*nf:(i+1)*nf]
            for e,output in zip(s.elements,recovered['member_end_forces']):
                expected=e.local_end_forces(full_d)-np.asarray(states[i]['member_equivalent_loads'].get(str(e.id),np.zeros(6)))
                np.testing.assert_allclose(output['local_n_n_nmm'],expected,atol=1e-5)
        self.assertLess(max(map(abs,result['balance_force_error_n'])),1e-5)
        self.assertLess(max(map(abs,result['balance_moment_error_nmm'])),.01)
        unloaded=solve_building_case(model,layout,macro,dofs,lookup,{'front_kpa':1.1,'back_kpa':.3})
        self.assertGreater(max(abs(a['axial_n']-b['axial_n']) for a,b in zip(result['members'],unloaded['members'])),1.)

    def test_gravity_solar_placements_pressure_patterns_and_half_tributaries(self):
        g=geometry();s=frame(g);macro=PortalReduction(s,[(x,g.roof_z(x)) for x in g.roof_stations])
        inputs=WindInputs(building_width_m=6.,building_length_m=16.,eave_height_m=3.,roof_pitch_deg=10.,orientation='Any')
        source={'loads':{'g_kpa':'.1','solar_kpa':'.2','q_kpa':'.25',
                         'roof_load_placements':{'solar':{'side':'Left','from_percent':0.,'to_percent':50.}}}}
        before=copy.deepcopy(source)
        cases,basis=building_roof_cases(end_wind_cases(inputs),macro,g,inputs,source)
        self.assertEqual(source,before)
        np.testing.assert_allclose(basis['frame_tributary_widths_mm'],[4000.,8000.,4000.])
        pure=next(c for c in cases if c['name']=='ULS / 1.35G')
        applied=sum(sum(state['loads_n'][n.native_uy] for n in s.nodes) for state in pure['portal_loads'])
        weight=sum(e.A*e.length()*1e-9*7850*9.81 for e in s.elements)*3
        expected=-1.35*(.1*6*16*1000+.2*1.5*16*1000+weight)
        self.assertAlmostEqual(applied,expected,places=6)
        # Increasing solar changes the actual 3D loads, not just purlin ratios.
        source['loads']['solar_kpa']='.4'
        changed,_=building_roof_cases(end_wind_cases(inputs),macro,g,inputs,source)
        self.assertNotEqual(pure['portal_loads'],next(c for c in changed if c['name']=='ULS / 1.35G')['portal_loads'])
        wind_cases=[c for c in cases if c.get('wind_family')=='longitudinal']
        self.assertTrue(all('portal_loads' in c for c in wind_cases))
        self.assertEqual({c['cpe_case'] for c in wind_cases},{'External max','External min'})
        self.assertEqual({c['g_factor'] for c in wind_cases if c['level']=='ULS'},{.9,1.2})
        self.assertEqual({c['g_factor'] for c in wind_cases if c['level']=='SLS'},{0.,1.})
        gravity_sls=next(c for c in cases if c['name']=='SLS / G + Q')
        self.assertEqual(gravity_sls['q_factor'],1.)


if __name__=='__main__':unittest.main(verbosity=2)
