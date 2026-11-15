"""The engine with no window must behave exactly like the desktop window.

``HeadlessFrameEngine`` (plain input fields, no Qt) is what other applications
run through ``portalcalc.service``. These tests hold it to the desktop:

- every scenario of the results snapshot test is solved both ways and the whole
  outcome is compared: the full dossier, the report text, the saved preset and
  the displacements;
- the field schema the headless engine starts from matches the real input form;
- a headless calculation never imports Qt.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import json
import os
import re
import subprocess
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"

from results_snapshot_regression import SCENARIOS, differences, solved_engine

# Stamped per run, or a record of which source produced the result.
VOLATILE = {"generated_at", "engine_provenance"}


TIMING = re.compile(r"(solve\(s\) in|Total elapsed:) [0-9.]+ s")


def without_timings(value):
    """Elapsed times in the optimiser's report differ on every run."""
    if isinstance(value, str):
        return TIMING.sub(r"\1 <time>", value)
    if isinstance(value, dict):
        return {key: without_timings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [without_timings(item) for item in value]
    return value


def outcome(name, kind):
    return without_timings(raw_outcome(name, kind))


def raw_outcome(name, kind):
    from portalcalc.service.contract import json_safe

    with solved_engine(name, kind) as engine:
        result = {
            "solved": engine.structure is not None and engine.design_envelope_actions is not None,
            "results_text": engine.results_box.toPlainText(),
            "section_checks_text": engine.section_checks_box.toPlainText(),
            "costing_text": engine.costing_box.toPlainText(),
            "foundation_text": engine.foundation_box.toPlainText(),
            "preset": json_safe(engine.collect_preset_data()),
        }
        if result["solved"]:
            dossier = json_safe(engine.calculation_design_dossier())
            result["dossier"] = {key: value for key, value in dossier.items() if key not in VOLATILE}
            result["displacements"] = [float(x) for x in engine.displacements]
            result["optimiser_report"] = engine.auto_size_report_text
        return result


class HeadlessMatchesDesktopTests(unittest.TestCase):
    """One test per scenario: the headless engine and the window give the same outcome."""

    def check(self, name):
        changed, added = differences(outcome(name, "window"), outcome(name, "headless"))
        problems = changed + [f"{path}: only in the headless result" for path in added]
        if problems:
            shown = "\n  ".join(problems[:25])
            self.fail(f"{name}: headless engine differs from the desktop window in {len(problems)} values\n  {shown}")


def _make_test(name):
    def test(self):
        self.check(name)
    test.__doc__ = f"Headless engine equals the desktop window for scenario: {name}"
    return test


for _name in SCENARIOS:
    setattr(HeadlessMatchesDesktopTests, f"test_{_name}", _make_test(_name))


class FormSchemaTests(unittest.TestCase):
    def test_schema_matches_the_input_form(self):
        from portalcalc.desktop.form_schema import SCHEMA_PATH, build_schema

        saved = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(build_schema()))
        changed, added = differences(saved, current)
        problems = changed + [f"{path}: new in the form" for path in added]
        self.assertFalse(problems, "The input form or section catalogue changed. Regenerate the schema with "
                         "`python -m portalcalc.desktop.form_schema`.\n  " + "\n  ".join(problems[:15]))


class InputRulesTests(unittest.TestCase):
    """The engine's change rules are the ones the desktop form wires to its widgets."""

    PROGRAMMATIC = {"currentIndexChanged", "currentTextChanged", "textChanged", "toggled", "stateChanged", "valueChanged"}

    def rules(self, path, function_name=None):
        import ast
        from portalcalc.frame.engine import FrameEngine
        from portalcalc.frame.engine.host import EngineHost

        engine_methods = {name for name in dir(FrameEngine) if not name.startswith("__")}
        hooks = {name for name in vars(EngineHost) if not name.startswith("__")}
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        scope = tree if function_name is None else next(
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function_name)
        found = []
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"):
                continue
            signal = node.func.value
            if not isinstance(signal, ast.Attribute) or signal.attr not in self.PROGRAMMATIC or not node.args:
                continue
            called = sorted({n.attr for n in ast.walk(node.args[0]) if isinstance(n, ast.Attribute)
                             and isinstance(n.value, ast.Name) and n.value.id == "self"
                             and n.attr in engine_methods and n.attr not in hooks})
            if called:
                found.append((node.lineno, ast.get_source_segment(source, signal.value), signal.attr, tuple(called)))
        return [rule[1:] for rule in sorted(found)]

    def test_engine_rules_match_the_desktop_wiring(self):
        root = _bootstrap.ROOT / "portalcalc"
        desktop = self.rules(root / "desktop" / "ui_build.py")
        engine = self.rules(root / "frame" / "engine" / "headless.py", "connect_input_rules")
        self.assertEqual(sorted(desktop), sorted(engine),
                         "ui_build.py and HeadlessFrameEngine.connect_input_rules wire different change rules")
        # Rules on the same field run in the order they were connected.
        def per_field(rules):
            order = {}
            for field, signal, called in rules:
                order.setdefault((field, signal), []).append(called)
            return order
        self.assertEqual(per_field(desktop), per_field(engine))


class NoQtTests(unittest.TestCase):
    def test_headless_check_and_service_call_never_import_qt(self):
        code = (
            "import json, sys\n"
            "sys.modules['PySide6'] = None\n"
            "sys.modules['matplotlib'] = None\n"
            "from portalcalc.resources import FACTORY_DEFAULTS, data_path\n"
            "from portalcalc.service import calculate\n"
            "preset = json.loads(data_path(FACTORY_DEFAULTS).read_text(encoding='utf-8'))\n"
            "result = calculate({'schema': 'portalcalc.calculation.request.v1', 'request_id': 'no-qt',\n"
            "                    'mode': 'checkSpecified', 'inputs': preset})\n"
            "assert result['status'] == 'checked', result['status']\n"
            "assert result['checks']['members'], 'no member checks returned'\n"
        )
        env = dict(os.environ, PYTHONPATH=str(_bootstrap.ROOT))
        completed = subprocess.run([sys.executable, "-c", code], cwd=_bootstrap.ROOT, env=env,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
