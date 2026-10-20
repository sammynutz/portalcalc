"""Independent storage statics and FE checks; no UI, licence or model writes.

Usage: python -B test_storage_load_path.py [PortalCalc source directory]
The AST extraction executes the actual production frame and storage design
methods without importing the desktop application or starting a calculation.
"""
import ast
from dataclasses import dataclass, replace
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import numpy as np

ROOT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path(__file__).parent
sys.path.insert(0, str(ROOT))
from storage_loads import *
from member_checks import SectionCheckProperties, MemberActions, check_member, E_STEEL_MPA

tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
namespace = dict(globals())
classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in
           {"Node", "FrameElement2D", "Structure2D", "SectionProfile"}]
window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
methods = [n for n in window.body if isinstance(n, ast.FunctionDef) and n.name in
           {"storage_rail_design_for_profile", "storage_stub_design_for_profile"}]
host = ast.ClassDef(name="DesignHost", bases=[], keywords=[], body=methods, decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=classes + [host], type_ignores=[])),
             str(ROOT / "main.py"), "exec"), namespace)
Node, FrameElement2D, Structure2D, SectionProfile, DesignHost = (
    namespace[n] for n in ("Node", "FrameElement2D", "Structure2D", "SectionProfile", "DesignHost"))
DesignHost.serviceability_limit_value = lambda self, name: 250.0
PROFILE = SectionProfile("200 UB", "UB", "I", 4000., 100000., 30e6, 3e6, 1e10,
                         Syp=300000., Szp=60000., depth=200., flange_width=100.,
                         flange_thickness=10., web_thickness=6.)


def inputs(mode="Wall panels to horizontal rail", continuous=False, **kw):
    values = dict(enabled=True, load_path_mode=mode, left_wall=True, right_wall=False,
                  panel_support_mode="Continuous over vertical stub" if continuous else "Break at vertical stub")
    values.update(kw)
    return StorageLoadInputs(**values)


def independent_beam(height, support, base_load, ei=1.0, divisions=150):
    """Euler beam oracle in metres/kN, supports at base and rail, free overhang."""
    top = max(height, support)
    ys = np.unique(np.r_[np.linspace(0, top, divisions+1), height, support])
    k = np.zeros((2*len(ys), 2*len(ys)))
    f = np.zeros(2*len(ys))
    for i, (a, b) in enumerate(zip(ys[:-1], ys[1:])):
        l = b-a
        ke = ei/l**3*np.array([[12,6*l,-12,6*l],[6*l,4*l*l,-6*l,2*l*l],
                              [-12,-6*l,12,-6*l],[6*l,2*l*l,-6*l,4*l*l]])
        # Integrate Hermite shape functions with Gauss quadrature.
        fe = np.zeros(4)
        for xi, weight in zip(*np.polynomial.legendre.leggauss(3)):
            t = (xi+1)/2
            q = base_load*max(1-(a+t*l)/height, 0)
            shape = np.array([1-3*t*t+2*t**3, l*(t-2*t*t+t**3), 3*t*t-2*t**3, l*(-t*t+t**3)])
            fe += weight*l/2*q*shape
        ix = np.arange(2*i, 2*i+4)
        k[np.ix_(ix,ix)] += ke
        f[ix] += fe
    fixed = {0, 2*int(np.argmin(abs(ys-support)))}
    free = [i for i in range(len(f)) if i not in fixed]
    d = np.zeros(len(f))
    d[free] = np.linalg.solve(k[np.ix_(free,free)], f[free])
    return ys, d[::2]


class LoadPathTests(unittest.TestCase):
    def close(self, a, b, tol=1e-8):
        self.assertTrue(math.isclose(a,b,rel_tol=tol,abs_tol=tol), f"{a} != {b}")

    def test_full_bay_rail_and_equilibrium(self):
        # Independent statics: p0=8, W=96, W*h/3=96 kNm for one 8m bay.
        for mode, continuous, stub_fraction, direct in [
            ("Wall panels to horizontal rail",False,1.,0.),
            ("Side-running panels with centre stub",False,.5,48.),
            ("Side-running panels with centre stub",True,.625,36.)]:
            r=calculate_storage_loads(inputs(mode,continuous)); rail=r.rail_loads[0]
            self.close(r.total_per_wall_kn,96*stub_fraction)
            self.close(r.rail_reaction_per_wall_kn,32*stub_fraction)
            self.close(r.base_reaction_per_wall_kn,64*stub_fraction)
            self.close(rail.direct_column_reaction_kn,direct)
            self.close(r.total_per_wall_kn+direct,96)
            self.close(rail.rail_reaction_kn*3+direct,96)
            expected=40 if continuous else 32
            self.close(rail.rail_bending_moment_knm,expected)
            design=DesignHost().storage_rail_design_for_profile(PROFILE,r)
            self.close(design["moment_knm"],expected*1.5)

    def test_bay_scaling(self):
        for mode in ["Wall panels to horizontal rail","Side-running panels with centre stub"]:
            a=calculate_storage_loads(inputs(mode,bay_spacing_m=4)).rail_loads[0]
            b=calculate_storage_loads(inputs(mode,bay_spacing_m=8)).rail_loads[0]
            self.close(b.rail_bending_moment_knm,4*a.rail_bending_moment_knm)
            self.close(b.rail_reaction_kn,2*a.rail_reaction_kn)

    def test_stub_overhang_moment_and_deflection(self):
        r=calculate_storage_loads(inputs("Side-running panels with centre stub",stored_height_m=4,rail_height_mm=1000))
        design=DesignHost().storage_stub_design_for_profile(PROFILE,r)
        # Total stub load=85.333; head=113.778; base=-28.444.
        # The free 3m overhang imposes -48 kNm at the rail, ULS=-72.
        self.close(design["moment_knm"],72.,tol=2e-5)
        self.close(design["shear_kn"],98.66666666666667)
        ys,d=independent_beam(4,1, (8/3)*4*4,
                              E_STEEL_MPA*max(PROFILE.Iyp,PROFILE.Izp)/1e9)
        self.close(design["deflection_mm"],max(abs(d))*1000,tol=2e-4)

    def test_column_distributed_load_bending(self):
        # Isolate the direct reaction with both column ends laterally pinned.
        # For a triangular q0=32 kN/m over 3m: Mmax=q0*H^2/(9sqrt(3)).
        r=calculate_storage_loads(inputs("Side-running panels with centre stub"))
        s=Structure2D(); a=Node(1,0,0); b=Node(2,0,3000)
        s.add_node(a); s.add_node(b)
        e=FrameElement2D(1,a,b,E_STEEL_MPA,PROFILE,"LEFT_COLUMN"); s.add_element(e)
        for dof in [a.ux,a.uy,b.ux]: s.add_support(dof)
        apply_storage_loads_to_columns(s,r,[a,b],[],factor=1)
        d,_,_=s.solve()
        self.close(s.element_force_summary(e,d)["moment_knm"],32*9/(9*math.sqrt(3)))

    def test_missing_rail_is_error(self):
        r=calculate_storage_loads(inputs())
        s=Structure2D(); a=Node(1,0,0); s.add_node(a)
        with self.assertRaises(ValueError): apply_storage_loads_to_columns(s,r,[a],[])

    def test_signed_reversed_load_equilibrium(self):
        for reverse in [False,True]:
            r=calculate_storage_loads(inputs("Side-running panels with centre stub",reverse_direction=reverse))
            s=Structure2D(); nodes=[Node(1,0,500),Node(2,0,1500),Node(3,0,3500)]
            for n in nodes:s.add_node(n)
            for i,(a,b) in enumerate(zip(nodes[:-1],nodes[1:])):
                s.add_element(FrameElement2D(i+1,a,b,E_STEEL_MPA,PROFILE,"LEFT_COLUMN"))
            apply_storage_loads_to_columns(s,r,nodes,[],factor=1.5,left_base_y_mm=500)
            sign=1 if reverse else -1
            self.close(sum(s.loads.get(n.ux,0) for n in nodes),sign*64e3*1.5)
            # moment of force about column base, including consistent nodal couples
            moment=sum(s.loads.get(n.ux,0)*(n.y-500)-s.loads.get(n.rz,0) for n in nodes)
            self.close(moment,sign*96e6*1.5)

    def test_panel_moments_and_deflection(self):
        for height,rail_height in [(3.,3.),(3.,5.),(4.,1.),(4.,3.)]:
            r=calculate_storage_loads(inputs(stored_height_m=height,rail_height_mm=rail_height*1000))
            rail=r.rail_loads[0]
            ys,d=independent_beam(height,rail_height,r.base_pressure_kpa,
                                  30000.*(1000.*175**3/12*.35)/1e9)
            # Use f'c selected to give exactly Ec=30000 MPa.
            defl=panel_service_deflection(r,175.,(30000/4700)**2,250.,.35)[0]
            self.close(defl,max(abs(d))*1000,tol=5e-4)
            # Numerical pressure integration and equilibrium independently locate Mmax.
            y=np.linspace(0,max(height,rail_height),10001)
            a=np.minimum(y,height)
            load_moment=r.base_pressure_kpa*(y*(a-a*a/(2*height))-(a*a/2-a**3/(3*height)))
            moment=r.base_reaction_per_wall_kn/8*y+r.rail_reaction_per_wall_kn/8*np.maximum(y-rail_height,0)-load_moment
            self.close(rail.panel_max_moment_knm_per_m,max(abs(moment)),tol=1e-6)

    def test_continuous_panel_deflection(self):
        r=calculate_storage_loads(inputs("Side-running panels with centre stub",True))
        t=(1+math.sqrt(33))/16
        expected=(t**4/24-t**3/16+t/48)*8*4000**4/(30000*(1000*175**3/12*.35))
        actual=panel_service_deflection(r,175.,(30000/4700)**2,250.,.35)[0]
        self.close(actual,expected)
        self.close(r.rail_loads[0].panel_negative_moment_level_mm,0.)

    def test_linear_element_against_beam_oracle_and_snapshot(self):
        for direction in [1,-1]:
            for divisions in [1,3,11]:
                s=Structure2D(); nodes=[Node(i+1,0,3000*i/divisions) for i in range(divisions+1)]
                for n in nodes:s.add_node(n)
                for i,(a,b) in enumerate(zip(nodes[:-1],nodes[1:])):
                    e=FrameElement2D(i+1,a,b,E_STEEL_MPA,PROFILE,"LEFT_COLUMN");s.add_element(e)
                    s.add_linear_global_line_load(e,fx_start=direction*32*(1-a.y/3000),fx_end=direction*32*(1-b.y/3000))
                for dof in [nodes[0].ux,nodes[0].uy,nodes[-1].ux]:s.add_support(dof)
                d,reactions,_=s.solve()
                self.close(reactions[nodes[0].ux],-direction*32000.)
                self.close(reactions[nodes[-1].ux],-direction*16000.)
                oracle_y,oracle_d=independent_beam(3,3,32,E_STEEL_MPA*PROFILE.I_analysis/1e9)
                peak=max(abs(s.element_global_displacement_at(e,d,t)[0]) for e in s.elements for t in np.linspace(0,1,301))
                self.close(peak,max(abs(oracle_d))*1000,tol=3e-5)
                state=s.element_load_snapshot(); s.clear_loads(); s.restore_element_load_snapshot(state)
                self.assertEqual(state,s.element_load_snapshot())
                # Uniform + triangular load must preserve both endpoint values.
                e=s.elements[0];old=e.load_components(s.uniform_load_for_element(e))
                s.add_uniform_global_line_load(e,fx_n_per_mm=2.)
                new=e.load_components(s.uniform_load_for_element(e))
                self.close(new[1]-old[1],-2.)
                self.close(new[3]-old[3],-2.)

    def test_uniform_beam_unchanged(self):
        s=Structure2D();a=Node(1,0,0);b=Node(2,8000,0)
        for n in [a,b]:s.add_node(n)
        e=FrameElement2D(1,a,b,E_STEEL_MPA,PROFILE);s.add_element(e)
        for dof in [a.ux,a.uy,b.uy]:s.add_support(dof)
        s.add_uniform_global_line_load(e,fy_n_per_mm=-4.)
        d,_,_=s.solve()
        self.close(s.element_force_summary(e,d)["moment_knm"],32.)
        self.close(abs(s.element_global_displacement_at(e,d,.5)[1]),5*4*8000**4/(384*E_STEEL_MPA*PROFILE.I_analysis))

    def test_gap_rejected_before_any_load(self):
        r=calculate_storage_loads(inputs("Side-running panels with centre stub"))
        s=Structure2D();nodes=[Node(1,0,0),Node(2,0,1000),Node(3,0,3000)]
        for n in nodes:s.add_node(n)
        s.add_element(FrameElement2D(1,nodes[0],nodes[1],E_STEEL_MPA,PROFILE,"LEFT_COLUMN"))
        with self.assertRaises(ValueError):apply_storage_loads_to_columns(s,r,nodes,[])
        self.assertEqual(s.loads,{})

    def test_storage_factors_independent_of_roof_q(self):
        from loads import LoadCombination
        i=inputs(uls_factor=1.7,sls_factor=.8)
        self.close(storage_factor_for_combination(i,LoadCombination("1.2G+1.5Q",1.2,1.5)),1.7)
        self.close(storage_factor_for_combination(i,LoadCombination("1.35G",1.35)),1.7)
        self.close(storage_factor_for_combination(i,LoadCombination("SLS: G+Q",1.,1.)),.8)
        self.close(storage_factor_for_combination(i,LoadCombination("SLS: G",1.,serviceability_component="G")),0.)
        self.close(storage_factor_for_combination(i,LoadCombination("SLS: Ws",0.,serviceability_component="Ws")),0.)


if __name__=="__main__":
    print("Source:",ROOT)
    unittest.main(verbosity=2)
