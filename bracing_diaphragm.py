"""SLS-only equivalent roof diagonals from the supplied stressed-skin note.

Flexibility is adopted, never inferred from panel geometry or a nominal rod.
Units: mm, N, MPa; the entered panel flexibility is in mm/kN.
"""
import copy
from dataclasses import asdict, dataclass
import math

from bracing_analysis import AnalysisError, AxialMember, positive


NOTE = 'Technical Note - Allowance for Stressed-Skin Diaphragm Action in Portal-Frame SLS Analysis, sections 4, 6, 8 and 9'
BASIS = ('PRELIMINARY SLS roof stressed-skin model using adopted panel flexibility. '
         'One full roof-slope panel per bay, crossed tension-only equivalent diagonals; '
         'each diagonal has the full Aeq. Conventional bracing remains installed and '
         'is solved independently without sheets for ULS and bare-model stability. '
         'No wall diaphragm or ridge-flashing shear transfer is credited. Continuous '
         'roof sheets and transfer through structural purlin/portal/collector joints '
         'are assumed; screw, sheet, collector and end-transfer capacities/slip remain unchecked. '
         'The note\'s 1.5-1.8 mm/kN range is an engineering judgement for its specific '
         'crest-fixed assembly and along-slope shear direction, not a geometry scaling '
         'rule or a validated longitudinal stiffness. Current applied wind is longitudinal; '
         'transverse portal sway is outside this analysis.')


@dataclass(frozen=True)
class DiaphragmSettings:
    enabled: bool = False
    flexibility_mm_per_kn: float = 1.8
    shear_direction: str = 'along_slope'

    def validate(self):
        if not isinstance(self.enabled, bool):
            raise AnalysisError('Stressed-skin enabled must be true or false')
        if not isinstance(self.flexibility_mm_per_kn, (int, float)):
            raise AnalysisError('Diaphragm shear flexibility must be a number in mm/kN')
        positive(self.flexibility_mm_per_kn, 'Diaphragm shear flexibility (mm/kN)')
        if self.shear_direction not in ('along_slope', 'longitudinal'):
            raise AnalysisError('Unknown diaphragm shear-displacement direction')
        return self

    @classmethod
    def from_dict(cls, value=None):
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise AnalysisError('Stressed-skin settings must be an object')
        try:
            return cls(**value).validate()
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
            props = equivalent_diagonal(s, n, settings.flexibility_mm_per_kn)
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
                           'slope_width_mm': width, 'bay_length_mm': y1-y0,
                           'implied_longitudinal_flexibility_mm_per_kn':
                               props['length_mm']**3/(props['ea_kn']*(y1-y0)**2), **props})
    return enhanced, {'settings': asdict(settings), 'source': NOTE, 'basis': BASIS, 'panels': panels}


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
        shear = member['axial_n']/1000*p['shear_projection']
        # Opposite diagonals contribute opposite panel-shear signs. They can
        # both be taut under panel extension, which must not be called shear.
        sign = 1. if member['id']==p['member_ids'][0] else -1.
        member.update(panel_shear_kn=shear, shear_direction=p['shear_direction'],
                      shear_flow_kn_per_m=shear/(p['n_mm']/1000),
                      signed_panel_shear_kn=sign*shear,
                      longitudinal_shear_kn=member['axial_n']/1000*p['bay_length_mm']/p['length_mm'])
        panel = recovered[p['id']]
        panel['net_shear_kn'] += sign*shear
        panel['net_shear_flow_kn_per_m'] += sign*member['shear_flow_kn_per_m']
        panel['net_longitudinal_shear_kn'] += sign*member['longitudinal_shear_kn']
    result['diaphragm_members'] = diagonals
    result['diaphragm_panels'] = list(recovered.values())
    result['bare_sls'] = {'displacements_mm': bare_result['displacements_mm'],
                          'tangent_status': bare_result['tangent_status']}
    return result
