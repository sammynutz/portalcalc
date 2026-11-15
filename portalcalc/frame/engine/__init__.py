"""The 2D portal frame engine.

:class:`FrameEngine` builds the frame model from the design inputs, solves every load
combination, checks members and connections, sizes sections and writes the reports.
It has no Qt dependency: it reads inputs from field objects that only need the plain
accessors ``text()``, ``currentText()``, ``isChecked()`` and ``value()``, and talks to a
user interface through the hooks in :mod:`portalcalc.frame.engine.host`.
"""

from portalcalc.frame.engine.auto_size import AutoSizeMixin
from portalcalc.frame.engine.bracing import BracingMixin
from portalcalc.frame.engine.crane import CraneBeamMixin
from portalcalc.frame.engine.foundations import FoundationsCostingMixin
from portalcalc.frame.engine.inputs import InputsMixin
from portalcalc.frame.engine.member_checks import MemberChecksMixin
from portalcalc.frame.engine.optimiser import OptimiserMixin
from portalcalc.frame.engine.presets import PresetsMixin
from portalcalc.frame.engine.results import ResultsMixin
from portalcalc.frame.engine.secondary import SecondarySteelMixin
from portalcalc.frame.engine.serviceability import ServiceabilityMixin
from portalcalc.frame.engine.solve import SolveMixin
from portalcalc.frame.engine.state import StateMixin
from portalcalc.frame.engine.storage import StorageMixin
from portalcalc.frame.engine.web_connections import WebConnectionsMixin
from portalcalc.frame.engine.web_sizing import WebSizingMixin
from portalcalc.frame.engine.host import EngineHost


class FrameEngine(
    StateMixin,
    InputsMixin,
    PresetsMixin,
    StorageMixin,
    CraneBeamMixin,
    SecondarySteelMixin,
    FoundationsCostingMixin,
    BracingMixin,
    ServiceabilityMixin,
    SolveMixin,
    MemberChecksMixin,
    WebConnectionsMixin,
    WebSizingMixin,
    AutoSizeMixin,
    OptimiserMixin,
    ResultsMixin,
    EngineHost,
):
    """Model generation, analysis, checks, sizing and reporting for one 2D frame."""


__all__ = ["FrameEngine"]
