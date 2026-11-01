"""Main-calculation mullion basis plus additional spatial-bracing axial force."""
from dataclasses import asdict
import math

from end_wall import mullion_profile_design
from bracing_components import scoped_case_checks


def mullion_assessment(result,index,profile,criteria):
    basis=result['mullion_basis'];member=result['cases'][0]['members'][index]
    face=member['id'].split('/')[1];station=int(member['id'].split('/')[-1])
    main=next(m for m in basis['mullions'] if m['index']==station)
    height=max(member['length_mm'],main['height_mm'])
    points=result['analysis_layout']['geometry']['end_wall_stations_mm']
    width=max(main['tributary_width_mm'],(points[station+1]-points[station-1])/2)
    clad=result.get('face_cladding',{}).get(face,True)
    rows=[]

    def strength_row(item,name,**extra):
        check=item['member_check'];governing=max(check.checks.values(),key=lambda c:c.ratio)
        return {'case':name,'level':'ULS','method':'main_mullion_combined','mode':governing.label,
                'ratio':governing.ratio,'actions':asdict(item['actions']),
                'checks':{k:asdict(v) for k,v in check.checks.items()},
                'section_properties':asdict(profile.check_properties()),**extra}

    # Independent main wind envelope/geometry is mandatory for a replacement
    # section. Turning off bracing SLS cannot downgrade the main mullion design.
    baseline=mullion_profile_design(profile,basis['governing_height_mm'],basis['governing_width_mm'],
        basis['strength_pressure_kpa'],basis['service_pressure_kpa'],
        effective_length_factor=basis['effective_length_factor'],moment_modifier=basis['moment_modifier'],
        deflection_divisor=basis['deflection_divisor'])
    rows.append(strength_row(baseline,'Main mullion baseline / ULS',required_baseline=True))
    rows.append({'case':'Main mullion baseline / SLS','level':'SLS','method':'main_mullion_baseline',
                 'required_baseline':True,'mode':'Main mullion deflection prerequisite',
                 'ratio':baseline['deflection_ratio'],'deflection_mm':baseline['deflection_mm'],
                 'limit_mm':baseline['deflection_limit_mm']})
    for case in result['cases']:
        force=case['members'][index]['axial_n']/1000.;level=case['case']['level']
        pressure=abs(case['case']['front_kpa'] if face=='front_end' else case['case']['back_kpa'])
        strength=max(pressure,basis['strength_pressure_kpa']) if clad else 0.
        service=max(pressure,basis['service_pressure_kpa']) if clad else 0.
        item=mullion_profile_design(profile,height,width,strength,service,axial_force_kn=force,
            effective_length_factor=(basis['effective_length_factor'] if clad else 1.)*criteria.post_ke,
            moment_modifier=basis['moment_modifier'] if clad else 1.,deflection_divisor=criteria.sls_divisor)
        evidence={'axial_kn':force,'design_height_mm':height,'analysis_length_mm':member['length_mm'],
                  'tributary_width_mm':width,'pressure_kpa':strength if level=='ULS' else service,
                  'restraint':basis['restraint'] if clad else 'Open end: full-height effective length; no clad-wall flybrace credit',
                  'basis':basis['basis']}
        if level=='ULS':
            rows.append(strength_row(item,case['case']['name'],**evidence))
        else:
            rows.append({'case':case['case']['name'],'level':'SLS','method':'main_mullion_combined',
                         'mode':'Aligned mullion local deflection','ratio':item['deflection_ratio'],
                         'deflection_mm':item['deflection_mm'],'limit_mm':item['deflection_limit_mm'],**evidence})
    for row in rows:
        if not math.isfinite(row['ratio']):row['ratio']=None
        for check in row.get('checks',{}).values():
            if not math.isfinite(check['ratio']):check['ratio']=None
    rows,governing=scoped_case_checks(result,rows,criteria)
    ratio=governing['ratio']
    return {'id':member['id'],'section':profile.name,'case':governing['case'],'mode':governing['mode'],
            'ratio':ratio,'passes':ratio is not None and ratio<=1,'case_checks':rows,
            'mullion_basis':'Main mullion strength/serviceability prerequisites plus 3D combined wind-bending and axial check'}
