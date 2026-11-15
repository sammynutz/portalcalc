"""The frame engine with plain input fields: no window and no Qt.

``HeadlessFrameEngine`` starts in the same state as a freshly opened desktop
window (same fields, same defaults, factory preset applied). Load a preset with
``apply_preset_data``, then ``generate_and_solve`` and read the results:

    engine = HeadlessFrameEngine()
    engine.apply_preset_data(preset)
    engine.generate_and_solve()
    dossier = engine.calculation_design_dossier()
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from portalcalc.frame.engine import FrameEngine
from portalcalc.frame.engine.fields import Field, field_from_schema

SCHEMA_PATH = Path(__file__).resolve().parent / "form_schema.json"


@lru_cache(maxsize=1)
def form_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class _SignalBlocker:
    """Blocks a field's change notifications until this object is released."""

    def __init__(self, field):
        self.field = field
        self.previous = field.blockSignals(True)

    def __del__(self):
        self.field.blockSignals(self.previous)


class HeadlessFrameEngine(FrameEngine):
    """Model generation, analysis, checks, sizing and reports with no user interface."""

    def __init__(self):
        self.init_state()
        self.load_catalog()
        self.load_metroll_tables()
        self._build_fields(form_schema())
        # The same engine steps the desktop runs when it finishes building its form
        # and then opens: initial reports, factory defaults, canopy section defaults.
        self.update_extra_purlin_rows_label()
        self.calculate_storage_load_report()
        self.calculate_crane_beam_report()
        self.update_derived_frame_type_label()
        self.connect_input_rules()
        self.load_factory_defaults(silent=True)
        self.apply_builtin_canopy_section_defaults()

    def _build_fields(self, schema):
        specs = schema["fields"]
        option_lists = schema["option_lists"]
        fields = {}
        deferred = []
        for name, spec in specs.items():
            if "same_as" in spec or spec.get("kind") == "lean_to_envelope":
                deferred.append((name, spec))
                continue
            if spec["kind"] == "choice":
                spec = dict(spec, items=option_lists[spec["items"]])
            fields[name] = field_from_schema(spec, fields)
        for name, spec in deferred:
            fields[name] = fields[spec["same_as"]] if "same_as" in spec else field_from_schema(spec, fields)
        # One entry per field object (some fields are reachable under two names).
        self._fields = list({id(field): field for field in fields.values() if isinstance(field, Field)}.values())
        for name, field in fields.items():
            setattr(self, name, field)
        for name, value in schema["state"].items():
            setattr(self, name, copy.deepcopy(value))

    def connect_input_rules(self):
        """Inputs whose change makes the engine recompute something.

        The desktop form wires the same rules to its widgets in
        ``portalcalc/desktop/ui_build.py``; tests/headless_engine_regression.py
        checks that the two lists agree. The order of rules on one field matters.
        """
        self.frame_load_basis_combo.currentIndexChanged.connect(self.on_frame_load_basis_changed)
        # Optimise on a symmetric building: whichever column changes, the other follows.
        self.left_column_profile_combo.currentIndexChanged.connect(self._sync_matched_columns)
        self.right_column_profile_combo.currentIndexChanged.connect(
            lambda: self._sync_matched_columns(source=self.right_column_profile_combo))
        self.crane_beam_profile_combo.currentTextChanged.connect(self.on_crane_beam_profile_changed)
        self.crane_beam_pfc_cap_combo.currentTextChanged.connect(self.on_crane_beam_profile_changed)
        self.crane_beam_rail_profile_combo.currentTextChanged.connect(self.on_crane_beam_profile_changed)
        self.storage_enabled_checkbox.toggled.connect(self.calculate_storage_load_report)
        self.storage_enabled_checkbox.toggled.connect(self.generate_and_solve)
        self.storage_load_path_combo.currentTextChanged.connect(self.calculate_storage_load_report)
        self.storage_panel_support_combo.currentTextChanged.connect(self.calculate_storage_load_report)
        self.storage_reverse_direction_checkbox.toggled.connect(self.calculate_storage_load_report)
        self.storage_reverse_direction_checkbox.toggled.connect(self.generate_and_solve)
        self.storage_rail_profile_combo.currentTextChanged.connect(self.calculate_storage_load_report)
        self.storage_stub_profile_combo.currentTextChanged.connect(self.calculate_storage_load_report)
        self.storage_panel_auto_thickness_checkbox.toggled.connect(self.calculate_storage_load_report)
        self.storage_panel_selected_thickness_input.valueChanged.connect(self.calculate_storage_load_report)
        self.longitudinal_bracing_enabled_checkbox.toggled.connect(self.generate_and_solve)
        self.longitudinal_wall_sides_combo.currentTextChanged.connect(self.generate_and_solve)
        self.roof_brace_size_combo.currentTextChanged.connect(self.generate_and_solve)
        self.wall_brace_size_combo.currentTextChanged.connect(self.generate_and_solve)
        self.end_wall_braced_bay_count_input.valueChanged.connect(self.generate_and_solve)
        self.roof_shape_combo.currentTextChanged.connect(self.update_bracing_layout_summary)
        self.left_canopy_type_combo.currentTextChanged.connect(lambda: self.update_canopy_depth_from_geometry("left"))
        self.right_canopy_type_combo.currentTextChanged.connect(lambda: self.update_canopy_depth_from_geometry("right"))

    def block_input_signals(self):
        """No rule fires while a preset is being applied (the desktop blocks its widgets the same way)."""
        return [_SignalBlocker(field) for field in self._fields]
