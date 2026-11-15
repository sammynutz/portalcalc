"""Preset files, recent files, saved defaults and undo/redo history."""

import copy
import json
import os
import shutil

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QSpinBox,
)

from portalcalc.app_metadata import APP_SETTINGS_NAME
from portalcalc.frame.common import resource_path


RECENT_PRESET_LIMIT = 8


class PresetFilesMixin:
    """Open/save workflow and model history."""

    def default_preset_dir(self):
        from portalcalc.licensing import app_data_dir

        path = os.path.join(str(app_data_dir()), "presets")
        os.makedirs(path, exist_ok=True)
        bundled_path = resource_path("presets")
        if os.path.isdir(bundled_path) and os.path.abspath(bundled_path) != os.path.abspath(path):
            for name in os.listdir(bundled_path):
                if not name.lower().endswith(".json"):
                    continue
                source = os.path.join(bundled_path, name)
                target = os.path.join(path, name)
                if not os.path.exists(target):
                    try:
                        shutil.copy2(source, target)
                    except OSError:
                        pass
        return path

    def default_settings_path(self):
        return os.path.join(self.default_preset_dir(), "portal_frame_defaults.json")

    def recent_preset_settings(self):
        return QSettings("Central Steel Build", APP_SETTINGS_NAME)

    def recent_preset_paths(self):
        settings = self.recent_preset_settings()
        value = settings.value("recentPresetFiles", [])
        if isinstance(value, str):
            paths = [value]
        else:
            paths = list(value or [])
        cleaned = []
        seen = set()
        for path in paths:
            full_path = os.path.abspath(str(path))
            key = os.path.normcase(full_path)
            if key in seen or not os.path.isfile(full_path):
                continue
            cleaned.append(full_path)
            seen.add(key)
            if len(cleaned) >= RECENT_PRESET_LIMIT:
                break
        if cleaned != paths:
            settings.setValue("recentPresetFiles", cleaned)
        return cleaned

    def add_recent_preset_path(self, path):
        if not path:
            return
        full_path = os.path.abspath(str(path))
        key = os.path.normcase(full_path)
        paths = [item for item in self.recent_preset_paths() if os.path.normcase(item) != key]
        paths.insert(0, full_path)
        paths = paths[:RECENT_PRESET_LIMIT]
        self.recent_preset_settings().setValue("recentPresetFiles", paths)
        self.refresh_open_recent_menu()

    def clear_recent_preset_paths(self):
        self.recent_preset_settings().setValue("recentPresetFiles", [])
        self.refresh_open_recent_menu()

    def recent_preset_label(self, path):
        folder = os.path.basename(os.path.dirname(path))
        file_name = os.path.basename(path)
        return f"{file_name} ({folder})" if folder else file_name

    def refresh_open_recent_menu(self):
        menu = getattr(self, "open_recent_menu", None)
        if menu is None:
            return
        menu.clear()
        paths = self.recent_preset_paths()
        if not paths:
            empty_action = QAction("No recent files", self)
            empty_action.setEnabled(False)
            menu.addAction(empty_action)
            return
        for path in paths:
            action = QAction(self.recent_preset_label(path), self)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, preset_path=path: self.load_recent_preset(preset_path))
            menu.addAction(action)
        menu.addSeparator()
        clear_action = QAction("&Clear Recent Files", self)
        clear_action.triggered.connect(self.clear_recent_preset_paths)
        menu.addAction(clear_action)

    def write_preset_file(self, path):
        path = str(path)
        if not path.lower().endswith(".json"):
            path += ".json"
        data = self.collect_preset_data()
        if self.bracing_3d_calculation_cache is not None:
            # Optional result data is saved with the job, but never included in
            # source-input hashes, model history or another calculation's inputs.
            data.setdefault('bracing',{})['calculation_3d'] = self.bracing_3d_calculation_cache
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        self.current_preset_path = path
        self.add_recent_preset_path(path)
        self.mark_model_clean()
        self.update_window_title()
        return path

    def model_snapshot(self):
        if not hasattr(self, "span_input"):
            return None
        return json.dumps(self.collect_preset_data(), sort_keys=True, separators=(",", ":"))

    def model_is_dirty(self):
        if self._clean_model_snapshot is None:
            return False
        return self.model_snapshot() != self._clean_model_snapshot

    def mark_model_clean(self):
        self._clean_model_snapshot = self.model_snapshot()
        self.update_window_title()

    def setup_model_history(self):
        def queue_snapshot(*_):
            QTimer.singleShot(0, self.record_model_snapshot)

        for widget in self.findChildren(QLineEdit):
            widget.editingFinished.connect(queue_snapshot)
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(queue_snapshot)
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(queue_snapshot)
        for widget in self.findChildren(QRadioButton):
            widget.toggled.connect(queue_snapshot)
        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(queue_snapshot)
        self.record_model_snapshot()

    def record_model_snapshot(self):
        if self._history_suspended:
            return
        snapshot = self.model_snapshot()
        if snapshot is None or (self._model_history and snapshot == self._model_history[self._model_history_index]):
            self.update_history_actions()
            self.update_window_title()
            return
        del self._model_history[self._model_history_index + 1:]
        self._model_history.append(snapshot)
        self._model_history_index = len(self._model_history) - 1
        self.update_history_actions()
        self.update_window_title()

    def update_history_actions(self):
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self._model_history_index > 0)
            self.redo_action.setEnabled(self._model_history_index + 1 < len(self._model_history))

    def restore_model_history(self, index):
        if index < 0 or index >= len(self._model_history):
            return
        self._history_suspended = True
        try:
            self.apply_preset_data(json.loads(self._model_history[index]))
            self._model_history_index = index
            self.generate_and_solve()
        finally:
            self._history_suspended = False
        self.update_history_actions()
        self.update_window_title()

    def undo_model_change(self):
        self.restore_model_history(self._model_history_index - 1)

    def redo_model_change(self):
        self.restore_model_history(self._model_history_index + 1)

    def new_preset(self):
        if not self.confirm_model_replacement("starting a new model"):
            return
        self.clear_analysis_state()
        self.auto_web_profile_assignments = {}
        self.auto_web_assignment_basis = {}
        self.auto_web_joint_doubler_sides = {}
        self.web_split_failures = {}
        self._last_crane_auto_select_signature = None
        self.current_preset_path = None
        self.update_window_title()
        if not self.load_default_inputs(silent=True):
            data = copy.deepcopy(self.factory_default_data) if self.factory_default_data else None
            if data is not None:
                self.apply_preset_data(data)
            else:
                self.load_factory_defaults(silent=True)
        self.generate_and_solve()
        self._model_history = []
        self._model_history_index = -1
        self.record_model_snapshot()
        self.mark_model_clean()

    def save_preset(self, show_message=True):
        path = self.current_preset_path
        if path:
            try:
                path = self.write_preset_file(path)
            except OSError as exc:
                QMessageBox.critical(self, "Save preset failed", str(exc))
                return False
            if show_message:
                QMessageBox.information(self, "Preset saved", f"Saved preset:\n{path}")
            return True
        return self.save_preset_as(show_message=show_message)

    def save_preset_as(self, show_message=True):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save portal frame preset",
            os.path.join(self.default_preset_dir(), "portal_frame_preset.json"),
            "Portal Frame Preset (*.json);;All Files (*)",
        )
        if not path:
            return False
        try:
            path = self.write_preset_file(path)
        except OSError as exc:
            QMessageBox.critical(self, "Save preset failed", str(exc))
            return False
        if show_message:
            QMessageBox.information(self, "Preset saved", f"Saved preset:\n{path}")
        return True

    def load_preset_path(self, path):
        if not self.confirm_model_replacement("opening another model"):
            return False
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            self.apply_preset_data(data)
            self.current_preset_path = path
            self.add_recent_preset_path(path)
            self._model_history = []
            self._model_history_index = -1
            self.record_model_snapshot()
            self.mark_model_clean()
            self.update_window_title()
            self.generate_and_solve()
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Load preset failed", str(exc))
            return False
        return True

    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load portal frame preset",
            self.default_preset_dir(),
            "Portal Frame Preset (*.json);;All Files (*)",
        )
        if not path:
            return
        self.load_preset_path(path)

    def load_recent_preset(self, path):
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Recent file missing", f"This recent preset could not be found:\n{path}")
            self.refresh_open_recent_menu()
            return
        self.load_preset_path(path)

    def confirm_model_replacement(self, action_text):
        if not self.model_is_dirty():
            return True
        response = QMessageBox.question(
            self,
            "Save current model?",
            f"Do you want to save this preset before {action_text}?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if response == QMessageBox.Save:
            return self.save_preset(show_message=False)
        return response == QMessageBox.Discard

    def save_default_inputs(self):
        path = self.default_settings_path()
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(self.collect_preset_data(), file, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, "Save defaults failed", str(exc))
            return
        QMessageBox.information(self, "Defaults saved", f"Saved default inputs:\n{path}")

    def load_default_inputs(self, silent=False):
        path = self.default_settings_path()
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            self.apply_preset_data(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if not silent:
                QMessageBox.critical(self, "Load defaults failed", str(exc))
            return False
        return True

    def reset_default_inputs(self):
        if self.factory_default_data:
            self.apply_preset_data(self.factory_default_data)
            self.generate_and_solve()
        path = self.default_settings_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            QMessageBox.critical(self, "Reset defaults failed", str(exc))
            return
        QMessageBox.information(self, "Defaults reset", "Default inputs have been reset to the factory defaults.")
