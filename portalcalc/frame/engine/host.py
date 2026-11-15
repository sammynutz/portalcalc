"""Hooks between the frame engine and whatever hosts it.

The engine calls these where a user interface may want to react: repaint, show a
message, show or hide input groups. The defaults do nothing, so the engine runs
without a UI; the desktop window (portalcalc.desktop) overrides every one.
"""


class EngineHost:
    """Headless defaults for the engine's user-interface hooks."""

    def apply_ui_theme(self):
        """Refresh part of the user interface; nothing to do without one."""

    def block_input_signals(self):
        """Suspend input-change signals while a preset is applied; returns objects to keep alive meanwhile."""
        return []

    def notify(self, level, title, text):
        """Show a message; ``level`` is "information", "warning" or "critical"."""

    def optimisation_progress_begin(self):
        """Open a progress display for an optimiser run."""
        self.optimisation_progress_dialog = None

    def process_ui_events(self):
        """Let the user interface repaint during a long calculation."""

    def record_model_snapshot(self):
        """Record the current inputs as an undo step."""

    def refresh_plot(self):
        """Redraw the frame diagram."""

    def refresh_settings_menu_checks(self):
        """Refresh part of the user interface; nothing to do without one."""

    def set_label_rich_text(self, label, rich):
        """Switch a result label between rich and plain text."""

    def update_base_spring_visibility(self):
        """Refresh part of the user interface; nothing to do without one."""

    def update_canopy_section_visibility(self, left_has_canopy, right_has_canopy, left_has_lean_to, right_has_lean_to):
        """Refresh part of the user interface; nothing to do without one."""

    def update_current_canopy_visibility(self):
        """Refresh part of the user interface; nothing to do without one."""

    def update_eave_restraint_visibility(self):
        """Refresh part of the user interface; nothing to do without one."""

    def update_frame_system_visibility(self):
        """Refresh part of the user interface; nothing to do without one."""

    def update_internal_column_visibility(self):
        """Refresh part of the user interface; nothing to do without one."""

    def update_web_section_visibility(self):
        """Refresh part of the user interface; nothing to do without one."""
