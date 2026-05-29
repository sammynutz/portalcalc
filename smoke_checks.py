import py_compile
import json
import math
import tempfile
from pathlib import Path


def compile_sources(root: Path) -> None:
    for name in ["main.py", "member_checks.py", "loads.py", "wind.py", "licensing.py", "license_server.py", "license_generator_app.py"]:
        py_compile.compile(str(root / name), doraise=True)


def run_license_smoke_checks() -> None:
    import licensing
    import license_server
    import wind

    assert len(licensing.machine_id()) == 64
    side_wall_result = wind.calculate_wind(
        wind.WindInputs(
            eave_height_m=6.0,
            building_width_m=24.0,
            building_length_m=40.0,
            bay_size_m=12.0,
        )
    )
    expected_side_wall_cpe = (
        -0.65 * side_wall_result.h_m
        + -0.50 * (12.0 - side_wall_result.h_m)
    ) / 12.0
    assert math.isclose(wind.wall_cpe_for_surface(side_wall_result, "side_wall"), expected_side_wall_cpe, abs_tol=1e-9)
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = licensing.LicenseManager(storage_path=Path(temp_dir) / "license.json")
        status = manager.local_status()
        assert not status.valid
        assert "No license" in status.reason

        private_hex, public_hex = license_server.generate_keypair()
        db_path = str(Path(temp_dir) / "licenses.db")
        license_server.init_db(db_path)
        license_key = license_server.create_license(db_path, "Smoke Test", "standard", None)
        server_settings = license_server.ServerSettings(db_path=db_path, private_key_hex=private_hex)
        activation = license_server.activate_license(server_settings, license_key, licensing.machine_id())
        assert activation["ok"]
        license_file = Path(temp_dir) / "license.json"
        metadata_file = Path(temp_dir) / "license_state.json"
        manager = licensing.LicenseManager(
            public_key_hex=public_hex,
            storage_path=license_file,
            metadata_path=metadata_file,
        )
        payload = manager.verify_token(activation["token"])
        assert payload["customer"] == "Smoke Test"
        assert manager.status_from_payload(payload).valid
        manager.write_token(activation["token"])
        manager.write_metadata({"last_successful_server_check_at": licensing.time.time() - 2 * 86400})
        warning_status = manager.local_status()
        assert warning_status.valid
        assert "not checked" in warning_status.warning
        manager.write_metadata({"last_successful_server_check_at": licensing.time.time() - 8 * 86400})
        stale_status = manager.local_status()
        assert not stale_status.valid
        assert stale_status.server_check_required
        check = license_server.check_license(server_settings, activation["token"], licensing.machine_id())
        assert not check["revoked"]

        from fastapi.testclient import TestClient

        api_settings = license_server.ServerSettings(
            db_path=str(Path(temp_dir) / "api_licenses.db"),
            private_key_hex=private_hex,
            admin_token="smoke-admin",
        )
        with TestClient(license_server.create_app(api_settings)) as client:
            assert client.get("/health").json()["ok"]
            created = client.post(
                "/admin/licenses",
                headers={"X-Admin-Token": "smoke-admin"},
                json={"customer": "API Smoke", "plan": "standard", "max_machines": 1},
            )
            assert created.status_code == 200
            api_license_key = created.json()["license_key"]
            activated = client.post(
                "/activate",
                json={"license_key": api_license_key, "machine_id": licensing.machine_id()},
            )
            assert activated.status_code == 200
            assert activated.json()["ok"]
            refreshed = client.post(
                "/check",
                json={"token": activated.json()["token"], "machine_id": licensing.machine_id()},
            )
            assert refreshed.status_code == 200
            assert not refreshed.json()["revoked"]


def run_ui_smoke_checks() -> None:
    import os

    from PySide6.QtWidgets import QApplication

    os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"

    import main

    app = QApplication.instance() or QApplication([])
    window = main.MainWindow()

    names = [combo.name for combo in window.load_combinations]
    assert "1.2G + 1.5Q + Crane Left" in names
    assert "1.2G + 1.5Q + Crane Right" in names
    assert "1.2G + Wu (End +, Cpi +)" in names
    assert window.envelope_plan_selector.wall_states() == {"back": True, "left": True, "right": True, "front": True}

    window.generate_and_solve()
    assert window.derived_frame_type_label.text() == "Enclosed"
    assert "Not enforced" in window.license_status_label.text()
    concept_data = window.concept_3d_data()
    concept_dae = window.concept_3d_dae(concept_data)
    assert "<COLLADA" in concept_dae
    assert "<triangles" in concept_dae
    assert window.bottom_profile_combo is window.top_profile_combo
    assert window.left_canopy_bottom_profile_combo is window.left_canopy_top_profile_combo
    assert window.right_canopy_bottom_profile_combo is window.right_canopy_top_profile_combo
    assert window.frame_system_combo.currentText() == "Truss"
    assert window.g_load_input.text() == "0.10"
    assert window.downward_roof_load_from_input(window.g_load_input) == -0.10
    assert window.downward_roof_load_text("-0.25") == "0.25"
    assert window.downward_roof_load_text("0.15") == "0.15"
    assert window.snow_ahd_height_input.isHidden()
    window.snow_region_combo.setCurrentText("Region AS")
    assert not window.snow_ahd_height_input.isHidden()
    window.snow_region_combo.setCurrentText("None")
    assert window.left_canopy_length_input.isHidden()
    window.left_canopy_type_combo.setCurrentText("Canopy")
    assert not window.left_canopy_length_input.isHidden()
    window.left_canopy_type_combo.setCurrentText("None")
    with tempfile.TemporaryDirectory() as temp_dir:
        preset_path = Path(temp_dir) / "remembered_preset"
        saved_path = Path(window.write_preset_file(preset_path))
        assert saved_path.name == "remembered_preset.json"
        assert window.current_preset_path == str(saved_path)
        window.g_load_input.setText("0.22")
        window.write_preset_file(window.current_preset_path)
        saved_data = json.loads(saved_path.read_text(encoding="utf-8"))
        assert saved_data["loads"]["g_kpa"] == "0.22"
    window.g_load_input.setText("0.10")
    ub_chord_options = [
        window.top_profile_combo.itemText(index)
        for index in range(window.top_profile_combo.count())
        if "UB" in window.top_profile_combo.itemText(index).upper()
    ]
    assert ub_chord_options
    window.span_input.setText("28500")
    window.update_truss_depth_from_span()
    assert window.depth_input.text() == "900"
    assert window.truss_depth_from_span(0.045) == 1300.0
    assert window.selected_profile(window.top_profile_combo).name == window.selected_profile(window.bottom_profile_combo).name
    top_ladder = window.auto_size_candidate_indices(window.top_profile_combo, "TOP")
    assert top_ladder
    assert len(top_ladder) <= window.top_profile_combo.count()
    assert all(0 <= index < window.top_profile_combo.count() for index in top_ladder)
    if len(top_ladder) >= 3:
        original_top_index = window.top_profile_combo.currentIndex()
        window.top_profile_combo.setCurrentIndex(top_ladder[1])
        assert window.auto_size_candidate_walk(window.top_profile_combo, "TOP", current_passes=False)[0] == top_ladder[2]
        assert window.auto_size_candidate_walk(window.top_profile_combo, "TOP", current_passes=True)[0] == top_ladder[0]
        window.top_profile_combo.setCurrentIndex(original_top_index)
    assert window.member_check_restraints["BOTTOM"]["minor_mm"] == 2.0 * window.member_check_restraints["TOP"]["minor_mm"]
    assert "bottom minor length defaults to every second girt/flybrace" in window.section_checks_box.toPlainText()
    assert "automatic every-purlin retry if required" in window.section_checks_box.toPlainText()
    assert "Ignored main chord centre panels" in window.results_box.toPlainText()
    assert "Bottom chord envelope excludes the outer 30% from each eave" in window.results_box.toPlainText()
    assert "Canopy/lean-to chord envelopes ignore end panels" in window.results_box.toPlainText()
    assert "BOTTOM CHORD - CENTRAL 40% ENVELOPE" in window.section_checks_box.toPlainText()
    assert "Midspan sag G" in window.section_checks_box.toPlainText()
    assert "Midspan sag Q" in window.section_checks_box.toPlainText()
    assert "Midspan wind deflection Ws" in window.section_checks_box.toPlainText()
    assert "Column midspan deflection Ws" in window.section_checks_box.toPlainText()
    assert "H/150" in window.section_checks_box.toPlainText()
    assert "Wu Cpe Left +" in window.single_load_arrows
    assert "Wu Cpi + with Left +" in window.single_load_arrows
    assert "Ws Cpi - with End -" in window.single_load_arrows
    assert "Wu Left +" not in window.single_load_arrows
    assert window.normalized_single_load_label("Cpi - with Left +") == "Wu Cpi - with Left +"
    concept_data = window.concept_3d_data()
    concept_html = window.concept_3d_html(concept_data)
    assert concept_data["bay_count"] >= 1
    assert concept_data["frame_lines"]
    assert "Drag to orbit" in concept_html
    assert "const data =" in concept_html
    assert window.wind_reduction_input.parent() is None
    assert window.eave_x_spring_input.isHidden()
    assert window.end_wall_brace_dia_input.isHidden()
    window.eave_x_restraint_combo.setCurrentText("Both eaves")
    assert not window.eave_x_spring_input.isHidden()
    assert window.end_wall_brace_dia_input.isHidden()
    window.eave_x_restraint_combo.setCurrentText("End wall bracing approx")
    assert window.eave_x_spring_input.isHidden()
    assert not window.end_wall_brace_dia_input.isHidden()
    window.eave_x_restraint_combo.setCurrentText("None")
    window.frame_system_combo.setCurrentText("Rafter")
    assert not window.rafter_haunch_length_input.isHidden()
    window.rafter_haunch_length_input.setText("3000")
    window.frame_type_combo.setCurrentText("Roof Only")
    window.envelope_plan_selector.set_wall_states({"left": False, "right": False, "front": False, "back": False})
    window.set_combo_text_if_present(window.top_profile_combo, ub_chord_options[0])
    window.generate_and_solve()
    assert any(element.group == "TOP" for element in window.structure.elements)
    assert any(element.group == "TOP" and element.analysis_i_factor > 1.0 for element in window.structure.elements)
    rafter_depth = window.profile_depth_mm(window.selected_profile(window.top_profile_combo))
    expected_column_length = max(abs(window.structure.nodes[0].y - window.base_nodes[0].y) - 2.0 * rafter_depth, 0.0)
    assert math.isclose(window.member_check_restraints["LEFT_COLUMN"]["major_mm"], expected_column_length, abs_tol=1e-6)
    assert not any(element.group in {"BOTTOM", "WEB", "POST", "APEX"} for element in window.structure.elements)
    assert "RAFTER" in window.section_checks_box.toPlainText()
    assert "Rafter envelope uses the full rafter length" in window.results_box.toPlainText()
    assert "Rafter haunch" in window.results_box.toPlainText()
    window.frame_system_combo.setCurrentText("Truss")
    assert window.rafter_haunch_length_input.isHidden()
    window.generate_and_solve()
    assert window.purlin_check is not None
    assert "PURLIN CHECK" in window.section_checks_box.toPlainText()
    assert window.purlin_check_label.text() != "Not checked"
    assert window.wall_girt_check is not None
    assert "WALL GIRT CHECK" in window.section_checks_box.toPlainText()
    assert window.wall_girt_check_label.text() != "Not checked"
    window.left_canopy_type_combo.setCurrentText("Canopy")
    window.left_canopy_length_input.setText("3000")
    window.left_canopy_eave_height_input.setText("5000")
    window.generate_and_solve()
    assert "eave 5000.0 mm" in window.results_box.toPlainText()
    assert any(
        element.group == "LEFT_CANOPY_TOP"
        and abs(element.start.x - window.base_nodes[0].x) < 1e-6
        and abs(element.start.y - 5000.0) < 1e-6
        for element in window.structure.elements
    )
    window.left_canopy_wind_model_combo.setCurrentText("AS/NZS B.5 free roof")
    window.left_canopy_underside_combo.setCurrentText("Empty under")
    window.load_combination_combo.setCurrentText("1.2G + Wu (Left +, Cpi +)")
    window.generate_and_solve()
    assert "wind model   : AS/NZS B.5 free roof, windward, empty under" in window.results_box.toPlainText()
    assert "Cpn net" in window.results_box.toPlainText()
    assert any(abs(arrow[2]) > 1e-9 for arrow in window.load_arrows)
    main_roof_fx = [
        arrow[2]
        for arrow in window.single_load_arrows["Wu Cpe Left +"]
        if 0.0 <= arrow[0] <= float(window.span_input.text())
    ]
    assert any(abs(fx) > 1e-9 for fx in main_roof_fx)
    window.left_canopy_wind_model_combo.setCurrentText("Main roof Cpe + Cpi")
    window.left_canopy_type_combo.setCurrentText("Lean-to")
    window.left_lean_enclosure_selector.set_wall_states({"left": True, "right": True, "front": True, "back": True})
    window.load_combination_combo.setCurrentText("1.2G + Wu (Left +, Cpi +)")
    window.generate_and_solve()
    lean_text = window.results_box.toPlainText()
    assert "enclosed chamber Cpi" in lean_text
    assert "lean-to walls: outer clad, front clad, back clad" in lean_text
    window.envelope_plan_selector.set_wall_states({"left": True, "right": True, "front": True, "back": True})
    window.left_lean_enclosure_selector.set_wall_states({"left": False, "right": False, "front": False, "back": False})
    window.generate_and_solve()
    open_lean_text = window.results_box.toPlainText()
    assert "Table 5.1(B)" in open_lean_text
    assert "Left  wind model   : AS/NZS B.5 free roof" not in open_lean_text
    window.left_canopy_length_input.setText("0")
    window.left_canopy_eave_height_input.setText("")
    window.left_canopy_wind_model_combo.setCurrentText("Main roof Cpe + Cpi")
    window.left_canopy_type_combo.setCurrentText("None")
    window.generate_and_solve()
    assert window.costing_summary is not None
    assert window.costing_summary["portal_count"] > 1
    assert window.costing_summary["purlin_total_kg"] > 0.0
    assert window.foundation_summary is not None
    assert len(window.foundation_summary["items"]) >= 2
    assert "FOUNDATION DESIGN" in window.foundation_box.toPlainText()
    assert "SELECTED FOOTINGS" in window.foundation_box.toPlainText()
    assert "Skin friction used for bearing: No" in window.foundation_box.toPlainText()
    assert all("SLS:" in item["compression_combo"] or item["compression_combo"] == "-" for item in window.foundation_summary["items"])
    assert all(not item["uplift_combo"].startswith("SLS:") for item in window.foundation_summary["items"] if item["uplift_combo"] != "-")
    small_pier = window.design_pier_footing(20.0, 0.0, 150.0, 15.0)
    assert small_pier["diameter_mm"] == 600
    uplift_pier = window.design_pier_footing(20.0, 200.0, 150.0, 15.0)
    assert uplift_pier["diameter_mm"] >= 750 or uplift_pier["depth_m"] <= 4.0
    skin_bearing_pier = window.design_pier_footing(100.0, 0.0, 150.0, 15.0, True)
    no_skin_bearing_pier = window.design_pier_footing(100.0, 0.0, 150.0, 15.0, False)
    assert skin_bearing_pier["use_skin_friction_for_bearing"]
    assert skin_bearing_pier["bearing_capacity_kn"] >= 100.0
    assert (skin_bearing_pier["diameter_mm"], skin_bearing_pier["depth_m"]) <= (no_skin_bearing_pier["diameter_mm"], no_skin_bearing_pier["depth_m"])
    assert window.stock_length_m_for_cut(6.1) == 6.75
    assert window.stock_length_m_for_cut(20.1) == 40.0
    assert window.costing_summary["shs_wastage_factor"] == 1.07
    assert window.costing_summary["continuous_lap_factor"] == 1.15
    assert "wb" in window.costing_summary["rates"]
    assert window.costing_summary["per_portal_weights"]["shs"] >= window.costing_summary["per_portal_base_weights"]["shs"] * 1.07 - 1e-9
    assert window.costing_summary["ub_stock_length_m_per_portal"] >= window.costing_summary["ub_cut_length_m_per_portal"]
    assert "TOTAL ESTIMATED STEEL COST" in window.costing_box.toPlainText()
    assert "+ 7% waste" in window.costing_box.toPlainText()
    assert "rounded to stock length" in window.costing_box.toPlainText()
    window.purlin_span_type_combo.setCurrentText("Continuous lapped")
    window.generate_and_solve()
    assert any(item["lap_factor"] == 1.15 for item in window.costing_summary["purlin_items"] if item["label"].startswith("Roof"))
    assert "x 1.15 lap" in window.costing_box.toPlainText()
    assert window.total_cost_label.text() == f"${window.costing_summary['total_cost']:,.0f}"
    assert window.shs_cost_input.text() == "2.07"
    assert window.ub_cost_input.text() == "2.07"
    assert window.wb_cost_input.text() == "2.80"
    assert window.purlin_cost_input.text() == "1.90"
    current_summary = window.costing_summary
    window.shs_cost_input.setText("9.99")
    window.shs_cost_input.editingFinished.emit()
    assert window.costing_summary is current_summary
    window.shs_cost_input.setText("2.07")
    saved_defaults = json.loads(json.dumps(window.collect_preset_data()))
    test_defaults = json.loads(json.dumps(saved_defaults))
    test_defaults["geometry"]["span_mm"] = "32000"
    test_defaults["sections"]["purlin_span_type"] = "Continuous lapped"
    with tempfile.TemporaryDirectory() as temp_dir:
        defaults_path = Path(temp_dir) / "portal_frame_defaults.json"
        defaults_path.write_text(json.dumps(test_defaults), encoding="utf-8")
        window.default_settings_path = lambda: str(defaults_path)
        window.span_input.setText("111")
        assert window.load_default_inputs(silent=True)
        assert window.span_input.text() == "32000"
        assert window.purlin_span_type_combo.currentText() == "Continuous lapped"
    window.apply_preset_data(saved_defaults)
    window.shs_cost_input.setText("bad")
    assert window.costing_rate_from_input(window.shs_cost_input, "shs_rate_per_kg") == 2.07
    window.shs_cost_input.setText("2.07")
    assert window.wall_girt_check["spacing_mm"] == 1400.0
    rank_check = window.match_metroll_purlin("Double span", 8000.0, 1.58, 0.70, 0.55)
    assert rank_check["section"] == "C/Z 200 19"
    interpolated = window.match_metroll_purlin("Single span", 7842.0, 1.20, 0.40, 0.15)
    assert interpolated["interpolated"]
    single_capped = window.match_metroll_purlin("Single span", 7842.0, 0.96, 0.67, 0.55)
    assert single_capped["section"] == "C/Z 200 15"
    window.wind_region_combo.setCurrentText("C")
    window.generate_and_solve()
    assert window.wall_girt_check["spacing_mm"] == 1000.0

    window.eave_x_restraint_combo.setCurrentText("End wall bracing approx")
    window.generate_and_solve()
    spring_values = list(window.structure.springs.values())
    assert len(spring_values) == 2
    brace_diameter_mm = float(window.end_wall_brace_dia_input.text())
    brace_area_mm2 = math.pi * brace_diameter_mm**2 / 4.0
    one_square_kn_mm = (200000.0 * brace_area_mm2 / (math.sqrt(2.0) * 8000.0)) * 0.5 / 1000.0
    expected_spring_kn_mm = 2.0 * one_square_kn_mm / 3.0
    assert all(abs(value / 1000.0 - expected_spring_kn_mm) < 1e-9 for value in spring_values)
    assert "End wall bracing approximation" in window.results_box.toPlainText()

    window.crane_rating_input.setText("5")
    window.load_combination_combo.setCurrentText("1.2G + 1.5Q")
    window.generate_and_solve()
    assert "CRANE LOAD" not in window.results_box.toPlainText()

    window.load_combination_combo.setCurrentText("1.2G + 1.5Q + Crane Left")
    window.generate_and_solve()
    left_text = window.results_box.toPlainText()
    assert "Position     : left column only" in left_text
    assert "Horiz load" in left_text

    window.load_combination_combo.setCurrentText("1.2G + 1.5Q + Crane Right")
    window.generate_and_solve()
    right_text = window.results_box.toPlainText()
    assert "Position     : right column only" in right_text
    assert "Horiz load" in right_text

    window.crane_rating_input.setText("20")
    window.load_combination_combo.setCurrentText("1.2G + 1.5Q + Crane Left")
    window.generate_and_solve()
    text = window.results_box.toPlainText()
    assert "Horiz load   :   37.500 kN per loaded column" in text

    left_fx = [arrow[2] for arrow in window.single_load_arrows["Crane Q left"] if abs(arrow[2]) > 0.0]
    right_fx = [arrow[2] for arrow in window.single_load_arrows["Crane Q right"] if abs(arrow[2]) > 0.0]
    both_fx = [arrow[2] for arrow in window.single_load_arrows["Crane Q both"] if abs(arrow[2]) > 0.0]
    assert left_fx == [-25_000.0]
    assert right_fx == [25_000.0]
    assert len(both_fx) == 2 and min(both_fx) < 0.0 and max(both_fx) > 0.0

    weird_path = Path(__file__).resolve().parent / "presets" / "Truss Deflection Weirdness.json"
    if weird_path.exists():
        window.apply_preset_data(json.loads(weird_path.read_text(encoding="utf-8")))
        window.generate_and_solve()
        coord_dofs = {}
        for node in window.structure.nodes:
            key = (round(node.x, 6), round(node.y, 6))
            coord_dofs.setdefault(key, set()).add((node.ux, node.uy))
        assert all(len(dofs) == 1 for dofs in coord_dofs.values())

    internal_web_path = Path(__file__).resolve().parent / "presets" / "Truss Deflection Weirdness 2.json"
    if internal_web_path.exists():
        window.apply_preset_data(json.loads(internal_web_path.read_text(encoding="utf-8")))
        window.generate_and_solve()
        top_nodes = sorted(
            {element.start for element in window.structure.elements if element.group == "TOP"}
            | {element.end for element in window.structure.elements if element.group == "TOP"},
            key=lambda node: node.x,
        )
        for column in [element for element in window.structure.elements if element.group == "INTERNAL_COLUMN"]:
            column_top = column.start if column.start.y > column.end.y else column.end
            panel_index = next(
                i for i, (n1, n2) in enumerate(zip(top_nodes[:-1], top_nodes[1:]))
                if n1.x - 1e-6 <= column_top.x <= n2.x + 1e-6
            )
            expected = top_nodes[panel_index + 1] if panel_index % 2 == 0 else top_nodes[panel_index]
            connected_targets = []
            for web in [element for element in window.structure.elements if element.group == "WEB"]:
                if abs(web.start.x - column_top.x) < 1e-6 and abs(web.start.y - column_top.y) < 1e-6:
                    connected_targets.append(web.end)
                if abs(web.end.x - column_top.x) < 1e-6 and abs(web.end.y - column_top.y) < 1e-6:
                    connected_targets.append(web.start)
            assert any(abs(node.x - expected.x) < 1e-6 and abs(node.y - expected.y) < 1e-6 for node in connected_targets)

    cpi_right_path = Path(__file__).resolve().parent / "presets" / "Cpi Right.json"
    if cpi_right_path.exists():
        window.apply_preset_data(json.loads(cpi_right_path.read_text(encoding="utf-8")))
        window.load_combination_combo.setCurrentText("1.2G + Wu (Right +, Cpi +)")
        window.generate_and_solve()
        cpi_text = window.results_box.toPlainText()
        assert "Left  Table 5.1(B)" in cpi_text
        assert "Left  Table 5.1(B) leeward_wall opening" in cpi_text
        assert "internal wall Cpi" in cpi_text
        assert "2 Sided lean-to Cpi" not in cpi_text

    window.confirm_on_close = False
    window.close()
    app.quit()


def main() -> None:
    root = Path(__file__).resolve().parent
    compile_sources(root)
    run_license_smoke_checks()
    run_ui_smoke_checks()
    print("smoke checks ok")


if __name__ == "__main__":
    main()
