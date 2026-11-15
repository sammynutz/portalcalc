"""Optional checked-result cache, separate from engineering inputs and undo."""
import base64
import copy
from functools import lru_cache
import hashlib
import json
import zlib

import numpy as np
from portalcalc.app_metadata import APP_VERSION
from portalcalc.resources import source_file_hashes

SCHEMA = 'portalcalc.bracing-calculation-cache.v1'
MAX_RESULT_BYTES = 64*1024*1024


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode('utf-8')


@lru_cache(maxsize=1)
def engine_revision():
    # Source builds can change without a release/version increment. In packaged
    # builds the explicit revision also invalidates caches when the engine changes.
    digest = hashlib.sha256(b'bracing-cache-engine-2026-11-14-v27-end-wall-tiers')
    digest.update(APP_VERSION.encode())
    digest.update(np.__version__.encode())
    for name, sha256 in source_file_hashes().items():
        digest.update(name.encode())
        digest.update(sha256.encode())
    return digest.hexdigest()


def fingerprint(context, settings, selections, criteria):
    from portalcalc.bracing.design import candidate_properties
    from portalcalc.bracing.spacing import max_bay_spacing
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
            # Display overrides are dropped above, so the crane H/n must be captured here.
            'crane_racking_basis':context.get('crane_racking_basis'),
            'crane_runway_area_mm2':context.get('crane_runway_area_mm2'),
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
