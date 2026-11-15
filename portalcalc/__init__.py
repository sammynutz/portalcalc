"""PortalCalc: structural analysis and design engine for BOLT steel buildings.

Call the engine through :mod:`portalcalc.service` (request in, dossier out);
the desktop application lives in :mod:`portalcalc.desktop`.
"""

import os

# The solvers use small dense matrices, where multi-threaded BLAS costs more
# than it saves, and the envelope solver already runs load combinations in
# parallel. These must be set before numpy is first imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
