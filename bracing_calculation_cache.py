"""Optional checked-result cache, separate from engineering inputs and undo."""
import base64
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import zlib

import numpy as np
from app_metadata import APP_VERSION

SCHEMA = 'portalcalc.bracing-calculation-cache.v1'
MAX_RESULT_BYTES = 64*1024*1024


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode('utf-8')


@lru_cache(maxsize=1)
def engine_revision():
    # Source builds can change without a release/version increment. In packaged
    # builds the explicit revision also invalidates caches when the engine changes.
    digest = hashlib.sha256(b'bracing-cache-engine-2026-11-03-v23-response-screening')
    digest.update(APP_VERSION.encode())
    digest.update(np.__version__.encode())
    folder = Path(__file__).parent
    names = ('bracing_analysis.py','bracing_analysis_desktop.py','bracing_roof_loads.py','bracing_side_wind.py','bracing_spacing.py','bracing_design.py',
             'bracing_components.py','bracing_mixed_sizing.py','bracing_stiffness_screen.py','bracing_policy.py',
             'bracing_schedule.py','bracing_layout.py','bracing_layout_search.py','bracing_calculation_cache.py','bracing_diaphragm.py',
             'purlin_combined.py','purlin_beam.py','purlin_beam_design.py',
             'purlin_shared_design.py','purlin_strut_tables.py','purlin_end_local.py','member_checks.py',
             'bracing_sizing_diagnostics.py','bracing_mullion_design.py','end_wall.py','costing.py','loads.py','wind.py','main.py')
    for name in names:
        path = folder/name
        if path.is_file():
            digest.update(name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def fingerprint(context, settings, selections, criteria):
    from bracing_design import candidate_properties
    from bracing_spacing import max_bay_spacing
    source = copy.deepcopy(context['source_preset'])
    # Engineering settings/actual properties are captured explicitly below.
    # View choices and result payloads must not enter an input/source hash.
    for key in ('analysis_3d','calculation_3d','layout'):
        source.get('bracing',{}).pop(key,None)
    source.pop('display',None)
    inputs = {k:v for k,v in settings.items() if k not in
              {'invert_orbit_horizontal','invert_orbit_vertical','show_cladding_overlay'}}
    portal = context['portal']
    data = {'engine':engine_revision(),'source_inputs':source,'settings':inputs,
            'layout':context['layout'].to_dict(),'cases':context['cases'],
            'portal_k':portal.k.tolist(),'portal_h':portal.h.tolist(),
            'portal_free':portal.free.tolist(),'portal_points':portal.points,
            'rates':context['cost_rates'],'cladding':context.get('face_cladding'),
            'installed_purlins':context.get('installed_purlins'),
            'mullion_basis':context.get('mullion_basis'),
            'global_sls_basis':context.get('global_sls_basis'),
            'stressed_skin':context.get('stressed_skin'),
            'max_braced_bay_spacing':max_bay_spacing(context),
            'table_sha256':context.get('purlin_strut_tables',{}).get('sha256'),
            'sections':{g:candidate_properties(c,criteria) for g,c in selections.items()}}
    return hashlib.sha256(encoded(data)).hexdigest()


def pack(result, signature, view=None):
    if result is None:
        return None
    raw = encoded(result)
    if len(raw)>MAX_RESULT_BYTES:
        return None
    return {'schema':SCHEMA,'fingerprint':signature,'result_sha256':hashlib.sha256(raw).hexdigest(),
            'encoding':'zlib-json-base64','payload':base64.b64encode(zlib.compress(raw)).decode('ascii'),
            'view':copy.deepcopy(view or {})}


def unpack(cache, signature):
    """Fail closed for stale, corrupt, oversized or incompatible saved results."""
    if not isinstance(cache,dict) or cache.get('schema')!=SCHEMA or cache.get('fingerprint')!=signature:
        return None
    if cache.get('encoding')!='zlib-json-base64':
        return None
    try:
        payload = cache['payload']
        if not isinstance(payload,str) or len(payload)>MAX_RESULT_BYTES*2:
            return None
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(payload,validate=True),MAX_RESULT_BYTES+1)
        if len(raw)>MAX_RESULT_BYTES or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            return None
        if hashlib.sha256(raw).hexdigest()!=cache['result_sha256']:
            return None
        result = json.loads(raw)
        if (not isinstance(result,dict) or result.get('schema')!='portalcalc.fixed-bracing-analysis.v1' or not result.get('cases')
                or not result.get('section_checks') or not isinstance(result.get('design'),dict)):
            return None
        return result
    except (ValueError,TypeError,KeyError,zlib.error,UnicodeError):
        return None
