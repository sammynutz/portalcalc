"""SLS-only equivalent roof and wall diagonals using adopted flexibilities.

Flexibility is adopted, never inferred from panel geometry or a nominal rod.
Units: mm, N, MPa; the entered panel flexibility is in mm/kN.
"""
import copy
from dataclasses import asdict, dataclass
import math

from bracing_analysis import AnalysisError, AxialMember, positive


NOTE = 'Allowance for Stressed-Skin Diaphragm Action, Revision B, 2 November 2026, sections 5-9'
BASIS = ('PRELIMINARY SLS roof stressed-skin model using adopted panel flexibility. '
         'One full roof-slope panel per bay, crossed tension-only equivalent diagonals; '
         'each diagonal has the full Aeq. Conventional bracing remains installed and '
         'is solved independently without sheets for ULS and bare-model stability. '
         'No ridge-flashing shear transfer is credited. Continuous '
         'roof sheets and transfer through structural purlin/portal/collector joints '
         'are assumed; screw, sheet, collector and end-transfer capacities/slip remain unchecked. '
         'Rev B adopts provisional reference c=3.0 mm/kN for an 8.5 m by 12.104 m '
         'crest-fixed roof panel with along-slope shear. The selected reference-geometry '
         'mode explicitly applies the note\'s empirical a/b^2 proxy to total panel '
         'flexibility; this is a documented screening assumption, not a tested general '
         'scaling law or a complete ECCS component calculation. Direct-c overrides '
         'remain available. Current applied wind is longitudinal; transverse-wind '
         'response is outside this analysis.')
WALL_BASIS = ('WALL SENSITIVITY ASSUMPTION: independent trial panel flexibilities for '
              'clad side and end walls. Rev B reference c=6.0 mm/kN applies to a '
              '7 m wide by 6 m high pan-fixed wall; it is a provisional engineering '
              'input, not a measured characteristic or verified lower-bound stiffness. '
              'Side-wall shear is longitudinal; end-wall shear is transverse and supplies '
              'no direct longitudinal resistance. Side panels span one building bay; '
              'end panels span adjacent end-wall mullion stations. Actual corner-to-base '
              'diagonal lengths are each calibrated to 1/c for horizontal top-edge slip, '
              'including unequal corner heights. No extra gable infill stiffness is '
              'credited above the line joining the panel top corners. Continuous cladding, '
              'existing roof/column collectors and fixed ground anchorage are assumed. '
              'Open faces are excluded. Wall openings within a clad face, connection slip '
              'and sheet/fastener/collector/anchor capacities require separate assessment. '
              'Conventional bare-model stability and ULS remain independent prerequisites.')


def reference_calibration(family):
    """Rev B measured benchmarks and author-selected allowances, in mm/kN."""
    if family == 'roof':
        geometry = (8500./12104.**2)/(6000./6000.**2)
        raw = 4.5*geometry*1.5*1.2
        return {'source':NOTE, 'family':'roof', 'reference_a_mm':8500., 'reference_b_mm':12104.,
            'adopted_reference_c_mm_per_kn':3., 'raw_reference_c_mm_per_kn':raw,
            'measured_test_c_mm_per_kn':4.5, 'test_a_mm':6000., 'test_b_mm':6000.,
            'reference_geometry_factor':geometry,
            'judgement_factors':{'support_detail_allowance':1.5,'boundary_allowance':1.2},
            'assumed_assembly':'0.42 BMT, 29 mm ribs, 190.5 mm pitch, every crest fixed, no sidelap screws; approximately 1.5 m purlin spacing',
            'evidence':'Mahendran and Moor (1999), 6 m by 6 m crest-fixed roof test; geometry proxy and allowances are engineering judgement'}
    interpolation = 2.753+(29.-17.4)/(43.-17.4)*(4.854-2.753)
    geometry = (7000./6000.**2)/(3000./3000.**2)
    thickness = (.50/.42)**2.5
    pitch = (190.5/175.1)**2.5
    return {'source':NOTE, 'family':'wall', 'reference_a_mm':7000., 'reference_b_mm':6000.,
        'adopted_reference_c_mm_per_kn':6.,
        'raw_reference_c_mm_per_kn':interpolation*geometry*thickness*pitch*1.2*1.2,
        'measured_test_maxima_mm_per_kn':{'LTP20':2.753,'LTP45':4.854},
        'measured_profile_depths_mm':{'LTP20':17.4,'LTP45':43.},
        'test_a_mm':3000.,'test_b_mm':3000.,'interpolated_c_mm_per_kn':interpolation,
        'reference_geometry_factor':geometry,'thickness_factor':thickness,'pitch_factor':pitch,
        'assumed_reference_pitch_mm':175.1,'profile_distortion_exponent':2.5,
        'judgement_factors':{'C_girt_allowance':1.2,'boundary_allowance':1.2},
        'assumed_assembly':'0.42 BMT, 29 mm ribs, 190.5 mm pitch, every pan fixed, no sidelap screws; C20015 girts at 1.5 m',
        'evidence':'Lendvai and Joo no-seam trough tests; rib-depth interpolation, reference pitch, total-flexibility exponents and allowances are empirical/judgement inputs'}


def panel_flexibility(settings,family,a_mm,b_mm):
    """a is across ribs and b along ribs, independently of shear direction."""
    a = positive(a_mm,'Panel dimension perpendicular to ribs')
    b = positive(b_mm,'Panel dimension parallel to ribs')
    baseline = getattr(settings, {'roof':'flexibility_mm_per_kn',
        'side_wall':'side_wall_flexibility_mm_per_kn','end_wall':'end_wall_flexibility_mm_per_kn'}[family])
    ref = reference_calibration('roof' if family=='roof' else 'wall')
    factor = (a/b**2)/(ref['reference_a_mm']/ref['reference_b_mm']**2)
    c = baseline*factor if settings.calibration_mode=='rev_b_geometry' else baseline
    return c, {'mode':settings.calibration_mode,'a_perpendicular_to_ribs_mm':a,
        'b_parallel_to_ribs_mm':b,'geometry_factor':factor,
        'reference_c_input_mm_per_kn':baseline,'adopted_panel_c_mm_per_kn':c,
        'reference':ref,
        'status':('PROVISIONAL empirical total-flexibility scaling; renewed review for different assembly/details'
                  if settings.calibration_mode=='rev_b_geometry' else
                  'PROVISIONAL direct panel c override; geometry factor is reported but not applied')}


@dataclass(frozen=True)
class DiaphragmSettings:
    enabled: bool = False
    flexibility_mm_per_kn: float = 3.
    shear_direction: str = 'along_slope'
    side_walls_enabled: bool = False
    side_wall_flexibility_mm_per_kn: float = 6.
    end_walls_enabled: bool = False
    end_wall_flexibility_mm_per_kn: float = 6.
    calibration_mode: str = 'rev_b_geometry'

    @property
    def active(self):
        return self.enabled or self.side_walls_enabled or self.end_walls_enabled

    def validate(self):
        for name in ('enabled', 'side_walls_enabled', 'end_walls_enabled'):
            if not isinstance(getattr(self,name), bool):
                raise AnalysisError(f'Stressed-skin {name} must be true or false')
        for name in ('flexibility_mm_per_kn', 'side_wall_flexibility_mm_per_kn',
                     'end_wall_flexibility_mm_per_kn'):
            value = getattr(self,name)
            if not isinstance(value, (int, float)):
                raise AnalysisError('Diaphragm shear flexibility must be a number in mm/kN')
            positive(value, name+' (mm/kN)')
        if self.shear_direction not in ('along_slope', 'longitudinal'):
            raise AnalysisError('Unknown diaphragm shear-displacement direction')
        if self.calibration_mode not in ('rev_b_geometry','direct_c'):
            raise AnalysisError('Unknown diaphragm calibration mode')
        if self.calibration_mode=='rev_b_geometry' and self.shear_direction!='along_slope':
            raise AnalysisError('Rev B roof calibration defines c for along-slope shear; use direct c for another direction')
        return self

    @classmethod
    def from_dict(cls, value=None):
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise AnalysisError('Stressed-skin settings must be an object')
        try:
            # Retain explicitly saved legacy panel c values as direct inputs.
            # The UI's Use Rev B baselines action opts an older job into the
            # new documented scaling instead of silently reinterpreting it.
            return cls(**{**value,'calibration_mode':value.get('calibration_mode','direct_c')}).validate()
        except TypeError:
            raise AnalysisError('Unsupported stressed-skin settings') from None


def equivalent_diagonal(s_mm, n_mm, flexibility_mm_per_kn, modulus_mpa=200000.):
    s = positive(s_mm, 'Panel shear-direction dimension')
    n = positive(n_mm, 'Panel perpendicular dimension')
    c = positive(flexibility_mm_per_kn, 'Diaphragm shear flexibility (mm/kN)')
    e = positive(modulus_mpa, 'Equivalent diagonal modulus')
    length = math.hypot(s, n)
    # E/1000 is kN/mm2. Each crossed diagonal gets this full area.
    area = length**3 / ((e/1000)*c*s*s)
    return {'s_mm': s, 'n_mm': n, 'length_mm': length, 'area_mm2': area,
            'equivalent_diameter_mm': math.sqrt(4*area/math.pi),
            'modulus_mpa': e, 'ea_kn': e*area/1000,
            'flexibility_mm_per_kn': c, 'shear_projection': s/length,
            'shear_stiffness_kn_per_mm': 1/c}


def roof_diaphragm_model(model, layout, point_nodes, settings):
    """Add whole-slope panels, independent of the bracing/mullion strip mesh.

    Existing structural ridge nodes remain connected; no sheet crosses the
    ridge and no additional ridge link or ground support is introduced.
    """
    settings.validate()
    enhanced = copy.copy(model)
    enhanced.members = list(model.members)
    g = layout.geometry
    slopes = [(0., g.span_mm/2), (g.span_mm/2, g.span_mm)] if g.roof_shape == 'Gable' else [(0., g.span_mm)]
    panels = []
    for bay, (y0, y1) in enumerate(zip(g.bay_stations_mm, g.bay_stations_mm[1:])):
        for slope, (x0, x1) in enumerate(slopes):
            width = math.hypot(x1-x0, g.roof_z(x1)-g.roof_z(x0))
            s, n = (width, y1-y0) if settings.shear_direction == 'along_slope' else (y1-y0, width)
            c,calibration = panel_flexibility(settings,'roof',y1-y0,width)
            props = equivalent_diagonal(s,n,c)
            identifier = f'diaphragm/roof/bay/{bay}/slope/{slope}'
            corners = [(x0,y0,g.roof_z(x0)), (x1,y0,g.roof_z(x1)),
                       (x1,y1,g.roof_z(x1)), (x0,y1,g.roof_z(x0))]
            indices = [point_nodes[tuple(round(float(v),6) for v in p)] for p in corners]
            ids = []
            for i, (a,b) in enumerate(((indices[0],indices[2]), (indices[1],indices[3]))):
                member_id = f'{identifier}/{i}'
                enhanced.add_member(AxialMember(member_id, a, b, props['area_mm2'],
                    props['modulus_mpa'], tension_only=True, kind='roof_diaphragm', panel_id=identifier))
                ids.append(member_id)
            panels.append({'id': identifier, 'bay': bay, 'slope': slope,
                           'shear_direction': settings.shear_direction, 'member_ids': ids,
                           'slope_width_mm': width, 'bay_length_mm': y1-y0,'calibration':calibration,
                           'implied_longitudinal_flexibility_mm_per_kn':
                               props['length_mm']**3/(props['ea_kn']*(y1-y0)**2), **props})
    return enhanced, {'settings': asdict(settings), 'source': NOTE, 'basis': BASIS, 'panels': panels}


def stressed_skin_model(model, layout, point_nodes, settings, face_cladding):
    """Add enabled roof/wall families, with fixed bases and no new free nodes."""
    settings.validate()
    if settings.enabled:
        enhanced, evidence = roof_diaphragm_model(model,layout,point_nodes,settings)
        for panel in evidence['panels']:
            panel.update(family='roof', face='roof', longitudinal_projection=
                         panel['bay_length_mm']/panel['length_mm'])
    else:
        enhanced = copy.copy(model)
        enhanced.members = list(model.members)
        evidence = {'settings':asdict(settings), 'source':NOTE, 'basis':'', 'panels':[]}
    # The roof helper shares immutable geometry. Walls need new fixed base
    # nodes, so take private copies before adding any of them.
    enhanced.nodes = list(model.nodes)
    enhanced.node_labels = list(model.node_labels)
    enhanced.fixed = set(model.fixed)
    lookup = dict(point_nodes)
    for panel in layout.panels.values():
        side = panel.face in ('left_wall','right_wall')
        end = panel.face in ('front_end','back_end')
        if not ((side and settings.side_walls_enabled) or (end and settings.end_walls_enabled)):
            continue
        if not face_cladding.get(panel.face,False):
            continue
        family = 'side_wall' if side else 'end_wall'
        direction = 'longitudinal' if side else 'transverse'
        a,b = panel.diagonals[0]
        horizontal = abs(b[1]-a[1]) if side else abs(b[0]-a[0])
        mean_height = sum(abs(b[2]-a[2]) for a,b in panel.diagonals)/2
        c,calibration = panel_flexibility(settings,family,horizontal,mean_height)
        calibration['wall_height_basis'] = 'Actual rectangular height, or mean of the two top-corner heights for a sloping panel'
        # layout panel diagonals already use the two physical top corners,
        # not the intervening gable apex of a five-vertex end-wall polygon.
        props, ids = [], []
        identifier = 'diaphragm/'+panel.id
        for i,(a,b) in enumerate(panel.diagonals):
            indices=[]
            for point in (a,b):
                key = tuple(round(float(v),6) for v in point)
                if key not in lookup:
                    if abs(point[2])>1e-6:
                        raise AnalysisError('Wall diaphragm top must connect to an existing roof collector node')
                    lookup[key] = enhanced.add_node(point,f'{panel.face} diaphragm anchor',fixed=True)
                indices.append(lookup[key])
            s = abs(b[1]-a[1]) if side else abs(b[0]-a[0])
            n = abs(b[2]-a[2])
            diagonal = equivalent_diagonal(s,n,c)
            diagonal['longitudinal_projection'] = abs(b[1]-a[1])/diagonal['length_mm']
            props.append(diagonal)
            member_id = f'{identifier}/{i}'
            ids.append(member_id)
            enhanced.add_member(AxialMember(member_id,*indices,diagonal['area_mm2'],
                diagonal['modulus_mpa'],tension_only=True,kind='wall_diaphragm',panel_id=identifier))
        mean_height = sum(p['n_mm'] for p in props)/2
        # Mean-height properties are a report reference only: the solver uses
        # each actual diagonal's independently calibrated area and length.
        reference = equivalent_diagonal(props[0]['s_mm'],mean_height,c)
        evidence['panels'].append({**reference, 'id':identifier, 'family':family, 'face':panel.face,
            'shear_direction':direction, 'member_ids':ids, 'diagonal_properties':dict(zip(ids,props)),
            'calibration':calibration,
            'longitudinal_projection': props[0]['longitudinal_projection'],
            'implied_longitudinal_flexibility_mm_per_kn':c if side else None})
    if settings.side_walls_enabled or settings.end_walls_enabled:
        evidence['basis'] = (evidence['basis']+' '+WALL_BASIS).strip()
    evidence['active'] = settings.active
    evidence['panel_counts'] = {family:sum(p['family']==family for p in evidence['panels'])
                                for family in ('roof','side_wall','end_wall')}
    evidence['reference_calibrations'] = {family:reference_calibration(family) for family in ('roof','wall')}
    evidence['excluded_open_faces'] = [face for face in ('left_wall','right_wall','front_end','back_end')
        if not face_cladding.get(face,False) and
        (settings.side_walls_enabled if face.endswith('wall') else settings.end_walls_enabled)]
    return enhanced,evidence


def align_fixed_nodes(model, enhanced):
    """Keep case node arrays aligned; added anchors have no bare-model links."""
    for i in range(len(model.nodes),len(enhanced.nodes)):
        if not all(dof in enhanced.fixed for dof in range(3*i,3*i+3)):
            raise AnalysisError('A diaphragm introduced an unsupported free node')
        model.add_node(enhanced.nodes[i],enhanced.node_labels[i],fixed=True)


def recover_diaphragm(result, panels, conventional_count, bare_result):
    """Keep surrogate members outside purchased/checkable member groups."""
    panel_by_id = {p['id']: p for p in panels}
    diagonals = result['members'][conventional_count:]
    result['members'] = result['members'][:conventional_count]
    recovered = {p['id']: {'id':p['id'], 'shear_direction':p['shear_direction'],
                          'net_shear_kn':0., 'net_shear_flow_kn_per_m':0.,
                          'net_longitudinal_shear_kn':0.} for p in panels}
    for member in diagonals:
        p = panel_by_id[member['panel_id']]
        props = p.get('diagonal_properties',{}).get(member['id'],p)
        shear = member['axial_n']/1000*props['shear_projection']
        # Opposite diagonals contribute opposite panel-shear signs. They can
        # both be taut under panel extension, which must not be called shear.
        sign = 1. if member['id']==p['member_ids'][0] else -1.
        member.update(panel_shear_kn=shear, shear_direction=p['shear_direction'],
                      shear_flow_kn_per_m=shear/(props['n_mm']/1000),
                      signed_panel_shear_kn=sign*shear,
                      longitudinal_shear_kn=member['axial_n']/1000*props.get('longitudinal_projection',
                          p.get('bay_length_mm',0.)/p['length_mm']))
        panel = recovered[p['id']]
        panel['net_shear_kn'] += sign*shear
        panel['net_longitudinal_shear_kn'] += sign*member['longitudinal_shear_kn']
    for p in panels:
        recovered[p['id']]['net_shear_flow_kn_per_m'] = recovered[p['id']]['net_shear_kn']/(p['n_mm']/1000)
    result['diaphragm_members'] = diagonals
    result['diaphragm_panels'] = list(recovered.values())
    result['bare_sls'] = {'displacements_mm': bare_result['displacements_mm'],
                          'tangent_status': bare_result['tangent_status']}
    return result
