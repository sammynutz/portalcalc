"""Transverse ULS/SLS wind combinations for the physical 3D bracing network.

Normal roof/column loads act through the loaded portals. End-wall side suction
acts along Y with its actual X bands. No SLS diaphragm stiffness is used.
SLS cases repeat the ULS pressure patterns at serviceability pressure, as Ws
and G + Ws, matching the main portal's serviceability combinations.
"""
import copy
from dataclasses import replace
import math

import numpy as np

from portalcalc.bracing.roof_loads import _snapshot, _combine, canopy_parts, fully_open
from portalcalc.wind import (CPI_CASE_OPTIONS, SIDE_WALL_CPE_BANDS, active_cpi_for_case,
                  action_combination_factors, area_reduction_factor, calculate_wind,
                  direction_bearing_degrees, free_roof_cpn, low_pitch_cpe_values, open_canopy_cpi,
                  wind_pressure_scale)

# Level, wind pressure type and G factors. SLS 0G is the main portal's Ws-only
# sway combination; G + Ws follows the main serviceability set.
LEVELS=(('ULS','ultimate',(.9,1.2)),('SLS','serviceability',(0.,1.)))

SOURCE='AS/NZS 1170.2:2021 Amd 2, Tables 5.2(A/B/C), 5.3(A/B/C), 5.4/5.5 and 5.9'
URL='https://webstore.ansi.org/preview-pages/SAI/preview_00-2666920554-primary.pdf'


def roof_cpe_segments(wind, geometry, right, use_max, left_ext=0., right_ext=0., split_x=None):
    """Return horizontal X bands; keep pitched downwind Cpe in both branches.

    ``left_ext``/``right_ext`` are open canopy plan widths beyond the eaves: as
    in the 2D frame, the roof is then continuous from canopy tip to canopy tip,
    with zones measured from the windward tip and, for a pitched gable, the
    upwind/downwind split at ``split_x`` (half the total roof slope length).
    X is from the main left eave, so canopy bands run from -left_ext to
    span+right_ext. Without canopies the bands are unchanged.
    """
    g=geometry
    if left_ext>1e-9 or right_ext>1e-9:
        return _continuous_roof_segments(wind,g,right,use_max,left_ext,right_ext,split_x)
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


def _continuous_roof_segments(wind, g, right, use_max, left_ext, right_ext, split_x):
    lo,hi=-left_ext,g.span_mm+right_ext
    if g.roof_shape!='Gable':
        raise ValueError('Canopies on a skillion roof are not supported in the 3D model')
    if g.pitch_deg<10:
        # Distances from the windward canopy tip; h/d from the main span, as 2D.
        width=hi-lo
        bands=[(max(0.,a*wind.h_m*1000),min(width,b*wind.h_m*1000),cpmax if use_max else cpmin)
               for _,a,b,cpmax,cpmin in low_pitch_cpe_values(wind.h_m/(g.span_mm/1000))]
        bands=[(a,b,cp) for a,b,cp in bands if b>a]
        return sorted((hi-b,hi-a,cp) for a,b,cp in bands) if right else [(lo+a,lo+b,cp) for a,b,cp in bands]
    split=g.span_mm/2 if split_x is None else split_x
    up=next(z for z in wind.roof_zones if z.name=='Upwind rafter')
    down=next(z for z in wind.roof_zones if z.name=='Downwind rafter')
    upwind=up.cpe_max if use_max else up.cpe_min
    bands=[(split,hi,upwind),(lo,split,down.cpe_max)] if right else [(lo,split,upwind),(split,hi,down.cpe_max)]
    return sorted((a,b,cp) for a,b,cp in bands if b>a)


def half_slope_x(elements, origin):
    """X (from the main left eave) halfway along the continuous roof slope."""
    pieces=sorted(((min(e.start.x,e.end.x),max(e.start.x,e.end.x),e.length()) for e in elements))
    half=sum(length for _,_,length in pieces)/2
    run=0.
    for a,b,length in pieces:
        if run+length>=half and length>0:
            return a+(b-a)*(half-run)/length-origin
        run+=length
    return pieces[-1][1]-origin


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


def _patch_normal(structure, element, origin, bands, q_width, factor, internal, equivalents):
    """Normal pressure q*(-cp*factor + internal) over each X band of a roof member."""
    xa,xb=element.start.x-origin,element.end.x-origin;dx=xb-xa;dz=element.end.y-element.start.y
    if abs(dx)<1e-9:
        return
    sign=1. if dx>=0 else -1.
    nx,nz=-sign*dz/element.length(),sign*dx/element.length()
    for a,b,cp in bands:
        lo,hi=max(min(xa,xb),a),min(max(xa,xb),b)
        if hi<=lo:
            continue
        ta,tb=sorted(((lo-xa)/dx,(hi-xa)/dx))
        outward=q_width*(-cp*factor+internal)
        apply_element_patch(structure,element,ta,tb,nx*outward,nz*outward,equivalents)


def _wall_strip_targets(tops, eave_strips):
    """Per side: the eave node, the wall cladding height above it and the roof beyond it.

    The eave is the cladding pitching point, above the top chord node, and the
    roof runs past the column centreline to the wall cladding line. Wind on
    both reaches the frame at the eave node, as on the 2D frame's eave strips
    (frame.solver.apply_eave_strip_loads for the wall, and the 2D roof wind
    strip at the eave node).
    """
    targets={}
    for strip in eave_strips or ():
        for e in tops:
            node=next((n for n in (e.start,e.end) if abs(n.x-strip['x'])<1e-6 and abs(n.y-strip['y'])<1e-6),None)
            if node is not None:
                targets[strip['side'].lower()]={'node':node,'element':e,'wall_mm':max(float(strip['wall_mm']),0.),
                                                'roof_mm':max(float(strip.get('roof_mm',0.)),0.)}
                break
    return targets


def side_wind_cases(portal, geometry, inputs, gravity, widths, source=None, eave_strips=None):
    g=geometry;structure=copy.deepcopy(portal.structure)
    tops=[e for e in structure.elements if e.group=='TOP']
    strip_targets=_wall_strip_targets(tops,eave_strips)
    origin=min(n.x for e in tops for n in (e.start,e.end))
    apex_x=max((n for e in tops for n in (e.start,e.end)),key=lambda n:n.y).x-origin
    canopies=canopy_parts(structure,source)
    free_roof=fully_open(inputs)
    left_ext=sum(p['plan_mm'] for p in canopies if p['side']=='left')
    right_ext=sum(p['plan_mm'] for p in canopies if p['side']=='right')
    split_x=(half_slope_x(tops+[e for p in canopies for e in p['elements']],origin)
             if canopies and g.roof_shape=='Gable' and g.pitch_deg>=10 else None)
    end_area=sum((b-a)*(g.roof_z(a)+g.roof_z(b))/2 for a,b in zip(g.roof_stations,g.roof_stations[1:]))/1e6
    roof_length=sum(e.length() for e in tops)
    cases=[]
    for right in (False,True):
        direction='Right to left' if right else 'Left to right'
        bearing='Any' if inputs.orientation=='Any' else ((90. if right else 270.)-direction_bearing_degrees(inputs.orientation))%360
        directional=replace(inputs,orientation=bearing,left_canopy_length_m=0.,right_canopy_length_m=0.)
        wind=calculate_wind(directional)
        for use_max in (True,False):
            cpe_case=('Right' if right else 'Left')+(' +' if use_max else ' -')
            roof_bands=roof_cpe_segments(wind,g,right,use_max,left_ext,right_ext,split_x)
            end_bands=side_wall_segments(wind.h_m,g.span_mm,right)
            # Open canopy undersides: the shared single-opening rule (as 2D).
            canopy_cpi={part['side']:open_canopy_cpi(wind,cpe_case,part['side'],inputs.building_length_m,
                                                     g.eave_mm/1000)[0] for part in canopies}
            for cpi_case,(level,pressure_type,g_factors) in ((c,l) for c in CPI_CASE_OPTIONS for l in LEVELS):
                scale=wind_pressure_scale(wind,pressure_type);q=wind.wu_kn_m2*scale
                # A fully open structure is a free roof in 2D: net Cpn, no Cpi.
                cpi=0. if free_roof else active_cpi_for_case(wind,cpi_case,cpe_case)
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
                    for part in canopies:
                        if free_roof:
                            windward=(part['side']=='right')==right
                            cpn=free_roof_cpn(part['pitch_deg'],wind.h_over_d,'windward' if windward else 'leeward',
                                              part['underside'],use_max)
                            for e in part['elements']:
                                _patch_normal(structure,e,origin,[(-math.inf,math.inf,cpn)],q*width/1000,1.,0.,equivalents)
                        else:
                            factor=max(area_reduction_factor(wind,'roof',part['slope_mm']*width/1e6)*kce,.8)
                            internal=canopy_cpi[part['side']]*action_combination_factors(wind,canopy_cpi[part['side']])[1]
                            for e in part['elements']:
                                _patch_normal(structure,e,origin,roof_bands,q*width/1000,factor,internal,equivalents)
                    for e in tops:
                        xa,xb=e.start.x-origin,e.end.x-origin;dx=xb-xa;dz=e.end.y-e.start.y
                        sign=1. if dx>=0 else -1.
                        # Outward roof normal has positive Z on either slope.
                        nx,nz=-sign*dz/e.length(),sign*dx/e.length()
                        if free_roof:
                            # Net free-roof coefficient (B.5, empty under) on the
                            # windward/leeward slope, as the 2D frame applies it.
                            windward=((xa+xb)/2<=apex_x)!=right
                            cpn=free_roof_cpn(g.pitch_deg,wind.h_over_d,'windward' if windward else 'leeward','empty',use_max)
                            outward=-cpn*q*width/1000
                            apply_element_patch(structure,e,0.,1.,nx*outward,nz*outward,equivalents)
                        for a,b,cp in ([] if free_roof else roof_bands):
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
                    walls=[];strips=[]
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
                        if side in strip_targets and strip_targets[side]['wall_mm']>1e-9:
                            # Wall cladding above the eave node, to the pitching point.
                            fx=(-1. if side=='left' else 1.)*outward*strip_targets[side]['wall_mm']
                            structure.add_load(strip_targets[side]['node'].ux,fx)
                            strips.append({'side':side,'wall_mm':strip_targets[side]['wall_mm'],'fx_n':fx})
                        walls.append({'face':side+'_wall','cpe':cp,'external_factor':factor,
                                      'net_inward_kpa':q*(cp*factor-cpi*kci)})
                    for side,target in strip_targets.items():
                        # Roof beyond the column centreline, to the cladding line, at the
                        # eave node: the end roof band's pressure, normal to the end panel
                        # (as 2D). A canopy roof starting at the eave carries its own.
                        if target['roof_mm']<=1e-9 or any(p['side']==side for p in canopies):
                            continue
                        e=target['element'];node=target['node']
                        xa,xb=e.start.x-origin,e.end.x-origin;dx=xb-xa;dz=e.end.y-e.start.y
                        sign=1. if dx>=0 else -1.
                        nx,nz=-sign*dz/e.length(),sign*dx/e.length()
                        x=node.x-origin
                        if free_roof:
                            windward=((xa+xb)/2<=apex_x)!=right
                            cpn=free_roof_cpn(g.pitch_deg,wind.h_over_d,'windward' if windward else 'leeward','empty',use_max)
                            outward=-cpn*q*width/1000
                        else:
                            cp=next((c for a,b,c in roof_bands if (a<=x<b if side=='left' else a<x<=b)),None)
                            if cp is None:
                                continue
                            outward=q*(-cp*roof_factor+cpi*kci)*width/1000
                        force=outward*target['roof_mm']
                        structure.add_load(node.ux,nx*force)
                        structure.add_load(node.uy,nz*force)
                        strips.append({'side':side,'roof_mm':target['roof_mm'],'fx_n':nx*force,'fz_n':nz*force})
                    normal.append(_snapshot(structure,equivalents))
                    frame_evidence.append({'index':index,'tributary_width_mm':float(width),
                                           'roof_external_factor':roof_factor,'walls':walls,
                                           **({'eave_wall_strips':strips} if strips else {})})
                peaks={face:max((p for a,b,p in bands),key=abs) for face,bands in ends.items()}
                for gf in g_factors:
                    label=(f'{gf:g}G + Wu' if level=='ULS' else 'G + Ws' if gf else 'Ws')
                    cases.append({'name':f'{level} / {direction} / {cpe_case} / {cpi_case} / {label}',
                        'level':level,'wind_family':'transverse','front_kpa':peaks['front_end'],
                        'back_kpa':peaks['back_end'],'end_wall_pressure_segments':ends,
                        'cpi':cpi,'cpi_case':cpi_case,'cpe_case':cpe_case,'source_direction':bearing,
                        # ULS retains its historic record: wu_kpa is the applied q.
                        'wu_kpa':q if level=='ULS' else wind.wu_kn_m2,'wind_pressure_scale':1. if level=='ULS' else scale,
                        'g_factor':gf,'q_factor':0.,
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
                            'scope':('ULS side-wind strength envelope; simultaneous roof/wall/internal pressures and gravity. Conventional bracing only; the production portal member checks remain separate.'
                                if level=='ULS' else
                                'SLS side-wind movement at serviceability pressure; same patterns as ULS. Conventional bare bracing only, no diaphragm credit. Per-frame eave sway is reported against the main in-plane criterion; the production 2D portal check remains the acceptance.')}})
    return cases
