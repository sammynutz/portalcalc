"""Engine state: results held between a solve and its checks, and the data tables."""

import json
import os

from portalcalc.frame.common import resource_path
from portalcalc.frame.sections import ShapeCatalog
from portalcalc.loads import build_load_combinations
from portalcalc.wind import CPE_CASE_OPTIONS, CPI_CASE_OPTIONS


class StateMixin:
    """Initialises and clears the calculation state; loads the section and purlin tables."""

    def init_state(self):
        """Calculation state and results; nothing here needs the widgets."""
        self.catalog = None
        self.metroll_tables = None
        self.structure = None
        self.displacements = None
        self.reactions = None
        self.force_vector = None
        self.base_nodes = None
        self.support_types = None
        self.load_arrows = None
        self.single_load_arrows = {}
        # 3D building loads (BUILDING_LOADS_3D_PLAN.md phase 4): None checks the
        # single "frame anywhere" 2D loads; "building" or "legacy_2d" checks
        # every distinct gridline's loads instead.
        self.building_load_mode = None
        self.building_model_override = None
        self.building_load_gridlines = None  # one sizing group's gridlines, or None for all
        self.building_frame_loads = None
        self._building_loads_cache = None
        self.design_envelope_actions = None
        self.member_check_restraints = None
        self.purlin_layout = None
        self.purlin_check = None
        self.canopy_purlin_checks = {}
        self.extra_purlin_rows = 0
        self.wall_girt_check = None
        self.end_wall_mullion_design = None
        self.end_wall_girt_check = None
        self.storage_results = None
        self.storage_rail_design = None
        self.storage_panel_design = None
        self.costing_summary = None
        self.foundation_summary = None
        self.wall_cladding_segments = []
        self.concept_roof_top_nodes = []
        self.concept_main_top_nodes = []
        self.concept_left_lean_top_nodes = []
        self.concept_right_lean_top_nodes = []
        self.concept_building_length_m = 0.0
        self.concept_bay_size_m = 0.0
        self.concept_wall_states = {}
        self.serviceability_height_mm = None
        self.serviceability_span_mm = None
        self.serviceability_importance_level = None
        self.serviceability_crane_active = False
        self.serviceability_limit_overrides = {}
        self.analysis_info = None
        self.auto_size_report_text = ""
        self.auto_web_profile_assignments = {}
        self.auto_web_assignment_basis = {}
        self.auto_web_joint_doubler_sides = {}
        self.web_split_failures = {}
        self._panel_count_override = None
        self._panel_topology_cache_signature = None
        self._panel_topology_cache_count = None
        self._suppress_panel_topology_search = False
        self.panel_topology_search_report = ""
        self.active_eave_x_restraint = "None"
        self.active_eave_x_spring_kn_mm = 0.0
        self.eave_deflection_limit_override = None
        self.governing_column_deflection_overlay = None
        self.deflection_hover_overlays = {}
        self._active_deflection_hover_label = None
        self._deflection_hover_active = False
        self.eave_bracing_info = None
        self.longitudinal_bracing_design = None
        self.bracing_location_layout = None
        self.bracing_3d_settings = None
        self.bracing_3d_calculation_cache = None
        # Adopted 3D frame restraint record (bracing_restraint), its last status
        # and why the last checked 3D result was not adopted, if it was not.
        self.bracing_3d_restraint = None
        self.bracing_3d_restraint_status = None
        self.bracing_3d_restraint_note = None
        # Optimiser-only trial sway restraint (kN/mm); None in normal use.
        self.trial_sway_restraint_kn_mm = None
        self.load_combinations = build_load_combinations(CPE_CASE_OPTIONS, CPI_CASE_OPTIONS)
        self.factory_default_data = None
        self.factory_defaults_loaded_path = ""
        self.factory_defaults_load_status = "Not loaded"
        self.current_preset_path = None
        self.confirm_on_close = True
        self.configurator_review = None
        self.configurator_review_result_path = None
        self.configurator_review_finalized = False
        self.configurator_review_auto_optimise_pending = False
        self.configurator_review_banner = None
        self.configurator_review_status_label = None
        self._model_history = []
        self._model_history_index = -1
        self._history_suspended = False
        self._clean_model_snapshot = None
        self.optimisation_load_vector_cache = None

    def clear_analysis_state(self):
        self.structure = None
        self.displacements = None
        self.reactions = None
        self.force_vector = None
        self.base_nodes = None
        self.support_types = None
        self.load_arrows = None
        self.single_load_arrows = {}
        self.design_envelope_actions = None
        self.governing_column_deflection_overlay = None
        self.deflection_hover_overlays = {}
        self._active_deflection_hover_label = None
        self._deflection_hover_active = False
        self.member_check_restraints = None
        self.purlin_layout = None
        self.purlin_check = None
        self.canopy_purlin_checks = {}
        self.wall_girt_check = None
        self.end_wall_mullion_design = None
        self.end_wall_girt_check = None
        self.storage_results = None
        self.storage_rail_design = None
        self.storage_panel_design = None
        self.costing_summary = None
        self.foundation_summary = None
        self.wall_cladding_segments = []
        self.concept_roof_top_nodes = []
        self.concept_main_top_nodes = []
        self.concept_left_lean_top_nodes = []
        self.concept_right_lean_top_nodes = []
        self.concept_building_length_m = 0.0
        self.concept_bay_size_m = 0.0
        self.concept_wall_states = {}
        self.serviceability_height_mm = None
        self.serviceability_span_mm = None
        self.serviceability_importance_level = None
        self.serviceability_crane_active = False
        self.analysis_info = None
        self.active_eave_x_restraint = "None"
        self.active_eave_x_spring_kn_mm = 0.0
        self.eave_bracing_info = None
        self.bracing_3d_restraint_status = None
        self.longitudinal_bracing_design = None
        if getattr(self, "end_wall_layout_label", None) is not None:
            self.update_end_wall_results()
        if getattr(self, "longitudinal_bracing_status_label", None) is not None:
            self.update_longitudinal_bracing_results()
        if not getattr(self, "preserve_auto_size_report", False):
            self.auto_size_report_text = ""

    def load_catalog(self):
        catalog_path = resource_path("Shape_Catalog.xlsx")
        if not os.path.exists(catalog_path):
            raise FileNotFoundError(f"Could not find Shape_Catalog.xlsx at {catalog_path}")
        self.catalog = ShapeCatalog(catalog_path)

    def load_metroll_tables(self):
        tables_path = resource_path("metroll_megaspan_capacity_tables.json")
        if os.path.exists(tables_path):
            with open(tables_path, "r", encoding="utf-8") as file:
                self.metroll_tables = json.load(file)
