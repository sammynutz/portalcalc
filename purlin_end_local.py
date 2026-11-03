"""Independent Table 5.6 end-roof patch cases for purlin bending only.

No case in this module changes the building's longitudinal bracing loads.
Negative local regions are alternatives, not simultaneous peak pressures.
"""
import math
from dataclasses import replace

from wind import (calculate_wind, active_cpi_for_case, CPI_CASE_OPTIONS,
                  direction_bearing_degrees, wind_pressure_scale, low_pitch_cpe_values)

SOURCE = 'AS/NZS 1170.2:2021 Amd 2, clauses 5.4.3/5.4.4, Table 5.6 and Figure 5.3'
URL = 'https://webstore.ansi.org/preview-pages/sai/preview_00-2666920554-primary.pdf'


def split_segments(segments, spans, factors):
    """Clip run-coordinate piecewise UDLs to bays and apply per-bay allocation."""
    result, offset = [], 0.
    for length, factor in zip(spans, factors):
        for a,b,q in segments:
            lo,hi = max(a,offset),min(b,offset+length)
            if hi > lo and q*factor:
                result.append([lo,hi,q*factor])
        offset += length
    return result


def end_roof_local_cases(inputs, geometry, purlin):
    width = purlin['tributary_width_mm']/1000
    length = geometry.length_mm/1000
    span = geometry.span_mm/1000
    x = purlin['nearest_installed_x_mm']/1000
    cosine = math.cos(math.radians(geometry.pitch_deg))
    plan_width = width*cosine
    cases = []
    for reverse in (False,True):
        source = 'Any' if inputs.orientation == 'Any' else ((180. if reverse else 0.)-direction_bearing_degrees(inputs.orientation))%360
        directional = replace(inputs,orientation=source,left_canopy_length_m=0.,right_canopy_length_m=0.,
            front_wall_clad=inputs.back_wall_clad if reverse else inputs.front_wall_clad,
            back_wall_clad=inputs.front_wall_clad if reverse else inputs.back_wall_clad)
        wind = calculate_wind(directional)
        h = wind.h_m
        a = min(.2*span,.2*length) if h/span >= .2 or h/length >= .2 else 2*h
        if a <= 0 or width <= 0:
            continue
        regions = [('RA1',1.5,a,a*a,width), ('RA2',2.,.5*a,.25*a*a,width)]
        left,right = x-plan_width/2,x+plan_width/2
        if geometry.pitch_deg < 10:
            overlap = max(0.,min(right,a)-max(left,0.))+max(0.,min(right,span)-max(left,span-a))
            corner_width = min(plan_width,overlap)/cosine
            corner = 'RC1'
        elif geometry.roof_shape == 'Gable':
            overlap = max(0.,min(right,span/2+a)-max(left,span/2-a))
            corner_width = min(plan_width,overlap)/cosine
            corner = 'RC2'
        else:
            corner_width,corner = 0.,'RC2'
        if corner_width > 1e-8:
            regions.append((corner,3.,a,.25*a*a,corner_width))
        cpi = max(active_cpi_for_case(wind,c,'End +') for c in CPI_CASE_OPTIONS)
        kce,kci = (.9,.9) if abs(cpi) >= .4 else (1.,1.)
        # Retain the application's End roof coefficient branch. For >=10 deg
        # the existing roof coefficient envelope is deliberately conservative;
        # RC2 is checked on either ridge-side strip as separate local cases.
        if geometry.pitch_deg < 10:
            zones = [(max(0.,lo*h),min(length,hi*h),min(cp1,cp2))
                     for _,lo,hi,cp1,cp2 in low_pitch_cpe_values(h/length)]
            cp_basis = 'Table 5.3(A), end-wind distance bands, h/d with d=building length'
        else:
            cp = min(min(z.cpe_max,z.cpe_min) for z in wind.roof_zones)
            zones = [(0.,length,cp)]
            cp_basis = 'Conservative external roof Cpe envelope for pitched-roof end patches'
        for region,kl,extent,area_limit,loaded_width in regions:
            loaded_width = min(loaded_width,2*math.sqrt(area_limit))
            patch_length = min(extent,area_limit/loaded_width,length)
            # Expand a narrow patch transversely where necessary to satisfy
            # the 4:1 side-ratio limit; only its tributary overlap loads this line.
            patch_area = max(loaded_width*patch_length,patch_length**2/4,loaded_width**2/4)
            if patch_area > area_limit+1e-8:
                continue
            end = min(extent,length)-patch_length
            first_bay = (geometry.bay_stations_mm[-1]-geometry.bay_stations_mm[-2] if reverse else geometry.bay_stations_mm[1]-geometry.bay_stations_mm[0])/1000
            starts = sorted({0.,end,max(0.,min(end,first_bay/2-patch_length/2))})
            for start in starts:
                boundaries = sorted({0.,length,start,start+patch_length,*[v for lo,hi,cp in zones for v in (lo,hi) if 0<=v<=length]})
                for level,pressure in (('ULS','ultimate'),('SLS','serviceability')):
                    q = wind.wu_kn_m2*wind_pressure_scale(wind,pressure)
                    segments = []
                    for lo,hi in zip(boundaries,boundaries[1:]):
                        mid = (lo+hi)/2
                        cp = next(cp for zlo,zhi,cp in zones if zlo <= mid < zhi)
                        local_cp = max(-3.,kl*cp) if cp < 0 else cp
                        effective_cp = cp+(local_cp-cp)*loaded_width/width if start <= mid < start+patch_length else cp
                        value = (effective_cp*kce-cpi*kci)*q*width
                        rlo,rhi = (length-hi,length-lo) if reverse else (lo,hi)
                        segments.append([rlo*1000,rhi*1000,value])
                    cases.append({'name':f'{level} outward / End local {region} / {"Back" if reverse else "Front"} / patch {start:g} m',
                        'segments':sorted(segments),'local_pressure':{'region':region,'kl':kl,'a_m':a,
                        'patch_length_m':patch_length,'patch_start_from_end_m':start,'loaded_width_m':loaded_width,
                        'effective_patch_area_m2':patch_area,'area_limit_m2':area_limit,'cpi':cpi,'kce':kce,'kci':kci,
                        'wind_pressure_kpa':q,'tributary_width_m':width,
                        'cp_basis':cp_basis,'source':SOURCE,'source_url':URL,
                        'scope':'Local purlin bending envelope only; conventional bracing wind loads unchanged'}})
    return cases
