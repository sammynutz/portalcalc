"""Transverse ULS wind combinations for the physical 3D bracing network.

Normal roof/column loads act through the loaded portals. End-wall side suction
acts along Y with its actual X bands. No SLS diaphragm stiffness is used.
"""
import copy
from dataclasses import replace
import math

import numpy as np

from bracing_roof_loads import _snapshot, _combine
from wind import (CPI_CASE_OPTIONS, SIDE_WALL_CPE_BANDS, active_cpi_for_case,
                  action_combination_factors, area_reduction_factor, calculate_wind,
                  direction_bearing_degrees, low_pitch_cpe_values)

SOURCE='AS/NZS 1170.2:2021 Amd 2, Tables 5.2(A/B/C), 5.3(A/B/C), 5.4/5.5 and 5.9'
URL='https://webstore.ansi.org/preview-pages/SAI/preview_00-2666920554-primary.pdf'


def roof_cpe_segments(wind, geometry, right, use_max):
    """Return horizontal X bands; keep pitched downwind Cpe in both branches."""
    g=geometry
    if g.pitch_deg<10 or (g.roof_shape=='Skillion' and right):
        bands=[(max(0.,a*wind.h_m*1000),min(g.span_mm,b*wind.h_m*1000),cpmax if use_max else cpmin)
               for _,a,b,cpmax,cpmin in low_pitch_cpe_values(wind.h_m/(g.span_mm/1000))]
    elif g.roof_shape=='Gable':
        up=next(z for z in wind.roof_zones if z.name=='Upwind rafter')
        down=next(z for z in wind.roof_zones if z.name=='Downwind rafter')
        bands=[(0.,g.span_mm/2,up.cpe_max if use_max else up.cpe_min),
               (g.span_mm/2,g.span_mm,down.cpe_max)]
    else:
        up=next(z for z in wind.roof_zones if z.name=='Upwind rafter')
        bands=[(0.,g.span_mm,up.cpe_max if use_max else up.cpe_min)]
    bands=[(a,b,cp) for a,b,cp in bands if b>a]
    return sorted((g.span_mm-b,g.span_mm-a,cp) for a,b,cp in bands) if right else bands


def side_wall_segments(height_m, span_mm, right):
    bands=[(max(0.,a*height_m*1000),min(span_mm,b*height_m*1000),cp) for a,b,cp in SIDE_WALL_CPE_BANDS]
    bands=[(a,b,cp) for a,b,cp in bands if b>a]
    return sorted((span_mm-b,span_mm-a,cp) for a,b,cp in bands) if right else bands


def apply_element_patch(structure, element, start, finish, fx_n_mm, fz_n_mm, equivalents):
    """Exact constant partial UDL through beam shape functions, t in [0,1]."""
    if finish<=start:
        return
    length=element.length();c,s=math.cos(element.angle()),math.sin(element.angle())
    qx,qz=c*fx_n_mm+s*fz_n_mm,-s*fx_n_mm+c*fz_n_mm
    local=np.zeros(6)
    for gp in (-1/math.sqrt(3),1/math.sqrt(3)):
        t=(start+finish)/2+gp*(finish-start)/2
        local+=np.array([(1-t)*qx,(1-3*t*t+2*t**3)*qz,length*(t-2*t*t+t**3)*qz,
                         t*qx,(3*t*t-2*t**3)*qz,length*(-t*t+t**3)*qz])*length*(finish-start)/2
    for dof,value in zip(element.dof_indices(),element.transformation_matrix().T@local):
        structure.add_load(dof,value)
    equivalents[element.id]=equivalents.get(element.id,np.zeros(6))+local


def side_wind_cases(portal, geometry, inputs, gravity, widths):
    g=geometry;structure=copy.deepcopy(portal.structure)
    tops=[e for e in structure.elements if e.group=='TOP']
    origin=min(n.x for e in tops for n in (e.start,e.end))
    end_area=sum((b-a)*(g.roof_z(a)+g.roof_z(b))/2 for a,b in zip(g.roof_stations,g.roof_stations[1:]))/1e6
    roof_length=sum(e.length() for e in tops)
    cases=[]
    for right in (False,True):
        direction='Right to left' if right else 'Left to right'
        bearing='Any' if inputs.orientation=='Any' else ((90. if right else 270.)-direction_bearing_degrees(inputs.orientation))%360
        directional=replace(inputs,orientation=bearing,left_canopy_length_m=0.,right_canopy_length_m=0.)
        wind=calculate_wind(directional);q=wind.wu_kn_m2
        for use_max in (True,False):
            cpe_case=('Right' if right else 'Left')+(' +' if use_max else ' -')
            roof_bands=roof_cpe_segments(wind,g,right,use_max)
            end_bands=side_wall_segments(wind.h_m,g.span_mm,right)
            for cpi_case in CPI_CASE_OPTIONS:
                cpi=active_cpi_for_case(wind,cpi_case,cpe_case)
                kce,kci=action_combination_factors(wind,cpi)
                end_factor=max(area_reduction_factor(wind,'side_wall',end_area)*kce,.8)
                ends={face:[(a,b,sign*q*(cp*end_factor-cpi*kci) if clad else 0.) for a,b,cp in end_bands]
                      for face,sign,clad in [('front_end',1.,inputs.front_wall_clad),('back_end',-1.,inputs.back_wall_clad)]}
                cutoff=min(g.span_mm,4*min(wind.h_m,inputs.building_length_m)*1000)
                drag_start,drag_end=(0.,g.span_mm-cutoff) if right else (cutoff,g.span_mm)
                normal=[];frame_evidence=[]
                for index,width in enumerate(widths):
                    structure.clear_loads();equivalents={}
                    roof_factor=max(area_reduction_factor(wind,'roof',roof_length*width/1e6)*kce,.8)
                    for e in tops:
                        xa,xb=e.start.x-origin,e.end.x-origin;dx=xb-xa;dz=e.end.y-e.start.y
                        sign=1. if dx>=0 else -1.
                        # Outward roof normal has positive Z on either slope.
                        nx,nz=-sign*dz/e.length(),sign*dx/e.length()
                        for a,b,cp in roof_bands:
                            lo,hi=max(min(xa,xb),a),min(max(xa,xb),b)
                            if hi<=lo:
                                continue
                            ta,tb=sorted(((lo-xa)/dx,(hi-xa)/dx))
                            outward=q*(-cp*roof_factor+cpi*kci)*width/1000
                            apply_element_patch(structure,e,ta,tb,nx*outward,nz*outward,equivalents)
                        lo,hi=max(min(xa,xb),drag_start),min(max(xa,xb),drag_end)
                        if hi>lo:
                            ta,tb=sorted(((lo-xa)/dx,(hi-xa)/dx))
                            tangent=(1. if not right else -1.)*q*.01*width/1000
                            apply_element_patch(structure,e,ta,tb,sign*dx/e.length()*tangent,
                                                sign*dz/e.length()*tangent,equivalents)
                    walls=[]
                    for side,x,clad in [('left',0.,inputs.left_wall_clad),('right',g.span_mm,inputs.right_wall_clad)]:
                        if not clad:
                            continue
                        windward=(side=='right')==right
                        cp=.7 if windward else wind.leeward_wall_cpe
                        factor=max(area_reduction_factor(wind,'windward_wall' if windward else 'leeward_wall',
                                                         g.roof_z(x)*width/1e6)*kce,.8)
                        outward=q*(-cp*factor+cpi*kci)*width/1000
                        for e in structure.elements:
                            if 'COLUMN' in e.group.upper() and abs((e.start.x+e.end.x)/2-origin-x)<1.:
                                structure.add_uniform_global_line_load(e,(-1. if side=='left' else 1.)*outward,0.)
                        walls.append({'face':side+'_wall','cpe':cp,'external_factor':factor,
                                      'net_inward_kpa':q*(cp*factor-cpi*kci)})
                    normal.append(_snapshot(structure,equivalents))
                    frame_evidence.append({'index':index,'tributary_width_mm':float(width),
                                           'roof_external_factor':roof_factor,'walls':walls})
                peaks={face:max((p for a,b,p in bands),key=abs) for face,bands in ends.items()}
                for gf in (.9,1.2):
                    cases.append({'name':f'ULS / {direction} / {cpe_case} / {cpi_case} / {gf:g}G + Wu',
                        'level':'ULS','wind_family':'transverse','front_kpa':peaks['front_end'],
                        'back_kpa':peaks['back_end'],'end_wall_pressure_segments':ends,
                        'cpi':cpi,'cpi_case':cpi_case,'cpe_case':cpe_case,'source_direction':bearing,
                        'wu_kpa':q,'wind_pressure_scale':1.,'g_factor':gf,'q_factor':0.,
                        'portal_loads':[_combine((gf,a),(1.,b)) for a,b in zip(gravity,normal)],
                        'transverse_pressure_pattern':{'roof_cpe_segments_mm':roof_bands,'end_wall_cpe_segments_mm':end_bands,
                            'frames':frame_evidence,'end_wall_external_factor':end_factor,
                            'roof_coefficient_basis':('Table 5.3(A)' if g.pitch_deg<10 or g.roof_shape=='Skillion' and right else 'Table 5.3(B/C)'),
                            'source':SOURCE,'source_url':URL},
                        'transverse_roof_drag':{'cf':.01,'x_start_mm':drag_start,'x_end_mm':drag_end,
                            'signed_x_force_kn':(-1. if right else 1.)*q*.01*inputs.building_length_m*(drag_end-drag_start)/1000,
                            'basis':'External roof friction parallel to sheet ribs, tangent to each roof slope; windward 4*min(h,b) exclusion. Wall/underside/exposed-member drag excluded.',
                            'preliminary_open_sided':not all((inputs.left_wall_clad,inputs.right_wall_clad,inputs.front_wall_clad,inputs.back_wall_clad))},
                        'pressure_basis':{'alongwind_depth_m':inputs.building_width_m,'crosswind_breadth_m':inputs.building_length_m,
                            'd_over_b':inputs.building_width_m/inputs.building_length_m,'leeward_cpe':wind.leeward_wall_cpe,
                            'effective_surface_count':sum((inputs.left_wall_clad,inputs.right_wall_clad,inputs.front_wall_clad,inputs.back_wall_clad))+1+int(abs(cpi)>=.4),
                            'kce':kce,'kci':kci,'source':SOURCE,'url':URL,
                            'scope':'ULS side-wind strength envelope; simultaneous roof/wall/internal pressures and gravity. Conventional bracing only; transverse SLS and the production portal member checks remain separate.'}})
    return cases
