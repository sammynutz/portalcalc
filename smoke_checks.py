import argparse
from contextlib import contextmanager
import py_compile
import copy
import html
import json
import math
import tempfile
import time
from pathlib import Path

import numpy as np


@contextmanager
def timed_step(label: str):
    start = time.perf_counter()
    print(f"[smoke] {label}...", flush=True)
    try:
        yield
    except Exception:
        elapsed = time.perf_counter() - start
        print(f"[smoke] {label} failed after {elapsed:.1f}s", flush=True)
        raise
    else:
        elapsed = time.perf_counter() - start
        print(f"[smoke] {label} finished in {elapsed:.1f}s", flush=True)


def compile_sources(root: Path) -> None:
    for name in ["app_metadata.py", "main.py", "portalcalc_service.py", "portalcalc_headless.py", "configurator_review.py", "calculation_service_regression.py", "configurator_review_regression.py", "physics_optimiser_regression.py", "concept_3d.py", "costing.py", "elevation_drawings.py", "finishes.py", "member_checks.py", "loads.py", "wind.py", "end_wall.py", "bracing.py", "licensing.py", "structural_assistant.py", "reduced_optimizer.py", "license_server.py", "license_generator_app.py", "crane_beam.py", "storage_loads.py"]:
        py_compile.compile(str(root / name), doraise=True)


def run_reduced_optimizer_smoke_checks() -> None:
    from reduced_optimizer import (
        CataloguePoint,
        ReducedElasticScreen,
        ResponseConstraint,
        StiffnessVariable,
        statically_condense,
    )

    stiffness = np.array([
        [4.0, -1.0, 0.0],
        [-1.0, 3.0, -1.0],
        [0.0, -1.0, 2.0],
    ])
    loads = np.array([1.0, 0.5, -0.25])
    condensed_k, condensed_f = statically_condense(stiffness, loads, [0, 2])
    full_displacement = np.linalg.solve(stiffness, loads)
    retained_displacement = np.linalg.solve(condensed_k, condensed_f)
    assert np.allclose(retained_displacement, full_displacement[[0, 2]])

    zero = np.zeros((2, 2))
    first_matrix = zero.copy()
    first_matrix[0, 0] = 1.0
    second_matrix = zero.copy()
    second_matrix[1, 1] = 1.0
    points = (
        CataloguePoint("light", 1.0, 1.0, 1.0, 0.8),
        CataloguePoint("medium", 2.0, 2.0, 1.8, 0.5),
        CataloguePoint("heavy", 4.0, 4.0, 3.2, 0.3),
    )
    variables = (
        StiffnessVariable("left", ("LEFT",), first_matrix, zero, points, "medium", "area"),
        StiffnessVariable("right", ("RIGHT",), second_matrix, zero, points, "medium", "area"),
    )
    constraints = (
        ResponseConstraint("left displacement", np.array([1.0, 0.0]), np.array([1.0, 0.0]), 0.55),
        ResponseConstraint("right displacement", np.array([0.0, 1.0]), np.array([0.0, 1.0]), 0.55),
    )
    screen = ReducedElasticScreen(
        fixed_matrix=np.eye(2) * 0.1,
        restrained_dofs=(),
        variables=variables,
        constraints=constraints,
    )
    result = screen.run(candidate_count=4)
    assert result.optimum.evaluations > 10
    assert result.candidates
    assert any(candidate.governing_ratio <= 1.0 for candidate in result.candidates)


def run_roof_udl_smoke_checks() -> None:
    import main
    from loads import apply_roof_vertical_loads

    profile = main.SectionProfile(
        "TEST",
        "SHS",
        "SHS",
        5000.0,
        1.0e6,
        2.0e8,
        1.0e8,
        0.0,
    )
    structure = main.Structure2D()
    left = main.Node(1, 0.0, 0.0)
    right = main.Node(2, 10000.0, 1000.0)
    structure.add_node(left)
    structure.add_node(right)
    element = main.FrameElement2D(1, left, right, 200000.0, profile, "TOP")
    structure.add_element(element)
    structure.add_support(left.ux)
    structure.add_support(left.uy)
    structure.add_support(right.uy)

    horizontal_line_load_kn_m = -2.0
    apply_roof_vertical_loads(
        structure,
        [left, right],
        horizontal_line_load_kn_m,
        element_groups={"TOP"},
    )
    length_mm = element.length()
    expected_total_n = horizontal_line_load_kn_m * 10000.0
    expected_global_y_n_mm = expected_total_n / length_mm
    expected_local_transverse_n_mm = (
        (right.x - left.x) / length_mm * expected_global_y_n_mm
    )
    assert math.isclose(
        sum(value for dof, value in structure.loads.items() if dof % 3 == 1),
        expected_total_n,
        rel_tol=1e-9,
    )
    assert math.isclose(
        structure.uniform_load_for_element(element)[1],
        expected_local_transverse_n_mm,
        rel_tol=1e-9,
    )
    displacements, _, _ = structure.solve()
    summary = structure.element_force_summary(element, displacements)
    expected_moment_knm = (
        abs(expected_local_transverse_n_mm) * length_mm**2 / 8.0 / 1_000_000.0
    )
    assert math.isclose(summary["moment_knm"], expected_moment_knm, rel_tol=1e-9)


def run_calculation_service_smoke_checks() -> None:
    from portalcalc_service import (
        PortalCalcCalculationService,
        UnsupportedCalculationInput,
        prepare_request,
    )

    inputs = {
        "geometry": {"roof_pitch_deg": "10"},
        "loads": {
            "q_kpa": "0.25",
            "snow_region": "Region AS",
            "snow_ahd_height_m": "1200",
            "left_wall_kn_m": "0",
            "right_wall_kn_m": 0,
        },
        "wind": {"importance_level": "2"},
        "sections": {
            "top_chord": "65*3 SHS",
            "bottom_chord": "65*3 SHS",
            "left_canopy_top_chord": "20*1.6 SHS",
            "left_canopy_bottom_chord": "20*1.6 SHS",
        },
    }
    prepared = prepare_request({
        "request_id": "smoke-check-specified",
        "mode": "checkSpecified",
        "inputs": inputs,
    })
    assert prepared.inputs["sections"]["bottom_chord"] == "65*3 SHS"
    assert prepared.load_policy["effective_live_or_snow_kpa"] >= 0.25
    assert len(prepared.input_digest) == 64

    web_selection_inputs = copy.deepcopy(inputs)
    web_selection_inputs["calculation_service"] = {
        "auto_web_profile_assignments": {"WEB": {"12": "50*2 SHS"}},
        "auto_web_joint_doubler_sides": {"WEB": [[12, "top chord"]]},
    }
    web_prepared = prepare_request({
        "request_id": "smoke-web-selection",
        "mode": "checkSpecified",
        "inputs": web_selection_inputs,
    })
    assert web_prepared.inputs["calculation_service"]["auto_web_profile_assignments"]["WEB"]["12"] == "50*2 SHS"
    assert web_prepared.inputs["calculation_service"]["auto_web_joint_doubler_sides"]["WEB"] == [[12, "top chord"]]

    bad_wall = copy.deepcopy(inputs)
    bad_wall["loads"]["left_wall_kn_m"] = "0.1"
    try:
        prepare_request({"request_id": "bad-wall", "mode": "autoSize", "inputs": bad_wall})
        raise AssertionError("non-zero wall load was not rejected")
    except UnsupportedCalculationInput:
        pass

    bad_chord = copy.deepcopy(inputs)
    bad_chord["sections"]["bottom_chord"] = "75*3 SHS"
    try:
        prepare_request({"request_id": "bad-chord", "mode": "checkSpecified", "inputs": bad_chord})
        raise AssertionError("unlinked chords were not rejected")
    except UnsupportedCalculationInput:
        pass

    class FakeBackend:
        def calculate(self, request):
            return {
                "inputs": copy.deepcopy(dict(request.inputs)),
                "overall_governing": {
                    "ratio": 0.82,
                    "group": "TOP",
                    "check": "Axial plus bending",
                    "passes_ratio_screen": True,
                },
                "member_checks": [{"group": "TOP", "overall_ratio": 0.82}],
                "serviceability_checks": [{"ratio": 0.61, "check": "Q deflection"}],
                "warnings": ["Review required"],
            }

    result = PortalCalcCalculationService(FakeBackend()).run({
        "request_id": "smoke-auto",
        "mode": "autoSize",
        "inputs": inputs,
    })
    assert result["status"] == "proposal"
    assert result["verified"] is True
    assert result["sizing"]["top_chord"] == result["sizing"]["bottom_chord"]
    assert result["provenance"]["request_digest_sha256"] == result["dossier"]["request_digest"]


def run_structural_assistant_smoke_checks() -> None:
    from unittest import mock

    import structural_assistant as assistant

    dossier_a = {"value": 1.25, "checks": [{"ratio": float("inf"), "name": "test"}]}
    dossier_b = {"checks": [{"name": "test", "ratio": float("inf")}], "value": 1.25}
    assert assistant.dossier_json(dossier_a) == assistant.dossier_json(dossier_b)
    assert len(assistant.dossier_hash(dossier_a)) == 64
    assert "infinity" in assistant.dossier_json(dossier_a)

    usage = {
        "input_tokens": 10_000,
        "input_tokens_details": {"cached_tokens": 2_000},
        "output_tokens": 1_500,
        "output_tokens_details": {"reasoning_tokens": 400},
        "total_tokens": 11_500,
    }
    expected_cost = (8_000 * 0.20 + 2_000 * 0.02 + 1_500 * 1.20) / 1_000_000.0
    assert math.isclose(
        assistant.estimate_usage_cost_usd("gpt-5.6-luna", usage),
        expected_cost,
        rel_tol=1e-12,
    )
    parsed = assistant.parse_usage("gpt-5.6-luna", {"usage": usage})
    assert parsed.reasoning_tokens == 400
    assert math.isclose(parsed.estimated_cost_usd, expected_cost, rel_tol=1e-12)
    assert assistant.extract_response_text({
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Grounded review"}]}]
    }) == "Grounded review"
    combined = assistant.combine_usage(parsed, parsed)
    assert combined.total_tokens == parsed.total_tokens * 2
    assert math.isclose(combined.estimated_cost_usd, parsed.estimated_cost_usd * 2, rel_tol=1e-12)

    fake_payload = {
        "id": "resp_smoke",
        "model": "gpt-5.6-luna",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Mock review"}]}],
        "usage": usage,
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(fake_payload).encode("utf-8")

    with mock.patch.object(assistant.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen:
        response = assistant.request_structural_review(
            api_key="test-key-that-must-not-enter-the-body",
            dossier=dossier_a,
            question="Review this test dossier",
            reasoning_effort="high",
        )
    request = urlopen.call_args.args[0]
    request_payload = json.loads(request.data.decode("utf-8"))
    assert request_payload["model"] == "gpt-5.6-luna"
    assert request_payload["reasoning"] == {"effort": "high", "context": "current_turn"}
    assert request_payload["text"] == {"verbosity": "low"}
    assert request_payload["store"] is False
    assert request_payload["max_output_tokens"] == 8000
    assert "test-key-that-must-not-enter-the-body" not in request.data.decode("utf-8")
    assert response.text == "Mock review"
    assert response.reasoning_effort == "high"

    fake_help_payload = {
        "id": "resp_help_smoke",
        "model": "gpt-5.6-luna",
        "output": [
            {
                "type": "reasoning",
                "id": "reasoning_smoke",
                "encrypted_content": "opaque-smoke-content",
                "summary": [],
            },
            {
                "type": "function_call",
                "name": "run_design_trial",
                "call_id": "call_trial_smoke",
                "arguments": json.dumps({
                    "hypothesis": "Test roof-frame stiffness",
                    "truss_depth_delta_mm": 100,
                    "left_addition_depth_delta_mm": 0,
                    "right_addition_depth_delta_mm": 0,
                    "left_column_steps": 0,
                    "right_column_steps": 0,
                    "top_member_steps": 0,
                    "bottom_chord_steps": 0,
                    "web_steps": 0,
                    "panel_layout_steps": 0,
                }),
            },
        ],
        "usage": usage,
    }

    class FakeHelpResponse(FakeResponse):
        def read(self):
            return json.dumps(fake_help_payload).encode("utf-8")

    help_input = [{"role": "user", "content": "Compact structural snapshot"}]
    with mock.patch.object(assistant.urllib.request, "urlopen", return_value=FakeHelpResponse()) as urlopen:
        help_turn = assistant.request_structural_help_turn(
            api_key="test-help-key-that-must-not-enter-the-body",
            input_items=help_input,
            snapshot_hash="a" * 64,
            reasoning_effort="medium",
        )
    help_request = urlopen.call_args.args[0]
    help_payload = json.loads(help_request.data.decode("utf-8"))
    assert help_payload["reasoning"] == {"effort": "medium", "context": "all_turns"}
    assert help_payload["store"] is False
    assert help_payload["parallel_tool_calls"] is False
    assert help_payload["include"] == ["reasoning.encrypted_content"]
    assert help_payload["text"] == {"verbosity": "low"}
    assert help_payload["tools"][0]["strict"] is True
    assert help_payload["tools"][0]["parameters"]["additionalProperties"] is False
    assert set(help_payload["tools"][0]["parameters"]["required"]) == set(
        help_payload["tools"][0]["parameters"]["properties"]
    )
    right_column_schema = help_payload["tools"][0]["parameters"]["properties"]["right_column_steps"]
    assert right_column_schema["minimum"] == -8
    assert right_column_schema["maximum"] == 8
    assert "right_addition_depth_delta_mm" in help_payload["tools"][0]["parameters"]["properties"]
    assert "lowest verified estimated total cost" in assistant.HELP_SYSTEM_INSTRUCTIONS
    assert "Never strengthen a member merely" in assistant.HELP_SYSTEM_INSTRUCTIONS
    assert "test-help-key-that-must-not-enter-the-body" not in help_request.data.decode("utf-8")
    assert len(help_turn.tool_calls) == 1
    assert help_turn.tool_calls[0].name == "run_design_trial"
    assert help_turn.tool_calls[0].arguments["truss_depth_delta_mm"] == 100
    assert any(item.get("type") == "reasoning" for item in help_turn.output_items)

    fake_optimise_payload = {
        "id": "resp_guided_optimise_smoke",
        "model": "gpt-5.6-luna",
        "output": [{
            "type": "function_call",
            "name": "run_optimisation_strategy",
            "call_id": "call_optimise_smoke",
            "arguments": json.dumps({
                "hypothesis": "Trade greater truss depth for lighter end columns",
                "search_focus": "column_stiffness_trade",
                "truss_depth_delta_mm": 200,
                "left_addition_depth_delta_mm": 0,
                "right_addition_depth_delta_mm": 200,
                "left_column_steps": -6,
                "right_column_steps": 1,
                "left_lean_column_steps": 0,
                "right_lean_column_steps": 0,
                "top_member_steps": -2,
                "bottom_chord_steps": -2,
                "web_steps": 0,
                "panel_layout_steps": 0,
            }),
        }],
        "usage": usage,
    }

    class FakeOptimiseResponse(FakeResponse):
        def read(self):
            return json.dumps(fake_optimise_payload).encode("utf-8")

    with mock.patch.object(assistant.urllib.request, "urlopen", return_value=FakeOptimiseResponse()) as urlopen:
        optimise_turn = assistant.request_guided_optimise_turn(
            api_key="test-optimise-key-that-must-not-enter-the-body",
            input_items=[{"role": "user", "content": "Verified deterministic baseline"}],
            snapshot_hash="b" * 64,
            reasoning_effort="medium",
        )
    optimise_request = urlopen.call_args.args[0]
    optimise_payload = json.loads(optimise_request.data.decode("utf-8"))
    optimise_probe_tool = optimise_payload["tools"][0]
    optimise_tool = optimise_payload["tools"][1]
    optimise_decision_tool = optimise_payload["tools"][2]
    assert optimise_payload["store"] is False
    assert optimise_payload["parallel_tool_calls"] is False
    assert optimise_payload["reasoning"] == {"effort": "medium", "context": "all_turns"}
    assert optimise_probe_tool["name"] == "run_design_probe"
    assert optimise_probe_tool["strict"] is True
    assert set(optimise_probe_tool["parameters"]["required"]) == set(
        optimise_probe_tool["parameters"]["properties"]
    )
    assert optimise_tool["name"] == "run_optimisation_strategy"
    assert optimise_tool["strict"] is True
    assert set(optimise_tool["parameters"]["required"]) == set(
        optimise_tool["parameters"]["properties"]
    )
    assert "cheapest verified" in assistant.GUIDED_OPTIMISE_SYSTEM_INSTRUCTIONS.lower()
    assert "incumbent returned" in assistant.GUIDED_OPTIMISE_SYSTEM_INSTRUCTIONS.lower()
    assert "left_addition_depth_delta_mm" in optimise_tool["parameters"]["properties"]
    assert "right_column_steps" in optimise_tool["parameters"]["properties"]
    assert optimise_decision_tool["name"] == "request_design_decision"
    assert optimise_decision_tool["strict"] is True
    assert set(optimise_decision_tool["parameters"]["required"]) == set(
        optimise_decision_tool["parameters"]["properties"]
    )
    assert "request_design_decision" in assistant.GUIDED_OPTIMISE_SYSTEM_INSTRUCTIONS
    assert "deterministic optimisations" in assistant.GUIDED_OPTIMISE_SYSTEM_INSTRUCTIONS
    assert "test-optimise-key-that-must-not-enter-the-body" not in optimise_request.data.decode("utf-8")
    assert optimise_turn.tool_calls[0].arguments["search_focus"] == "column_stiffness_trade"


def run_license_smoke_checks() -> None:
    import costing
    import licensing
    import license_server
    import wind
    from crane_beam import CraneBeamInputs, calculate_crane_beam
    from storage_loads import StorageLoadInputs, StoragePanelDesignInputs, apply_storage_loads_to_columns, calculate_ka, calculate_storage_loads, design_storage_panel, storage_summary_lines
    from end_wall import mullion_layout, roof_height_mm
    from bracing import BracingInputs, calculate_preliminary_bracing

    rod_restraint = costing.end_wall_portal_restraint_item({
        "modelled": True,
        "brace_area_mm2": math.pi * 12.0**2 / 4.0,
        "diagonal_length_mm": math.sqrt(2.0) * 8000.0,
        "end_count": 1,
        "squares_to_end": 3,
        "brace_label": "12 DIA ROD",
    }, restrained_eave_count=2, physical_x_count_per_end_side=6)
    assert rod_restraint["path_squares_per_end"] == 3
    assert rod_restraint["physical_x_count_per_end_side"] == 6
    assert rod_restraint["braced_square_count"] == 12
    assert rod_restraint["run_count"] == 24
    assert rod_restraint["turnbuckle_count"] == 24
    assert rod_restraint["turnbuckle_unit_cost"] is None
    assert rod_restraint["turnbuckle_cost_included"] is False
    assert math.isclose(
        rod_restraint["kg"],
        rod_restraint["total_m"] * rod_restraint["kg_m"],
        rel_tol=1e-12,
    )
    for rod_diameter, turnbuckle_size, unit_cost in (
        (16, "16", 9.62),
        (20, "20", 16.60),
        (24, "22", 23.75),
        (30, "30", 45.00),
    ):
        priced_rod = costing.end_wall_portal_restraint_item({
            "modelled": True,
            "brace_area_mm2": math.pi * rod_diameter**2 / 4.0,
            "diagonal_length_mm": 10000.0,
            "end_count": 1,
            "squares_to_end": 3,
            "brace_label": f"{rod_diameter} DIA ROD",
        }, restrained_eave_count=1, physical_x_count_per_end_side=6)
        assert priced_rod["run_count"] == 12
        assert priced_rod["turnbuckle_size"] == turnbuckle_size
        assert math.isclose(priced_rod["turnbuckle_unit_cost"], unit_cost, rel_tol=1e-12)
        assert math.isclose(priced_rod["turnbuckle_cost"], 12 * unit_cost, rel_tol=1e-12)
        assert priced_rod["turnbuckle_cost_included"] is True
    angle_restraint = costing.end_wall_portal_restraint_item({
        "modelled": True,
        "brace_area_mm2": 1136.0,
        "diagonal_length_mm": 10000.0,
        "end_count": 2,
        "squares_to_end": 2,
        "brace_label": "EA 75x75x8",
    }, restrained_eave_count=1, physical_x_count_per_end_side=6)
    assert angle_restraint["run_count"] == 24
    assert angle_restraint["turnbuckle_count"] == 0
    assert costing.end_wall_portal_restraint_item(None, 2) is None

    expected_area = 30.0 * 7.0 + 0.5 * 30.0 * (15.0 * math.tan(math.radians(10.0)))
    bracing = calculate_preliminary_bracing(BracingInputs(
        span_mm=30000.0,
        eave_height_mm=7000.0,
        roof_pitch_deg=10.0,
        roof_shape="Gable",
        bay_width_mm=8000.0,
        roof_panel_widths_mm=(7500.0,) * 4,
        roof_panel_projected_areas_m2=(expected_area / 4.0,) * 4,
        roof_node_lines=(
            {"type": "eave_strut", "label": "Left eave strut", "station_mm": 0.0, "height_mm": 7000.0},
            {"type": "mullion_tie", "label": "Mullion tie M1", "station_mm": 7500.0, "height_mm": 8000.0},
            {"type": "mullion_tie", "label": "Mullion tie M2", "station_mm": 15000.0, "height_mm": 9000.0},
            {"type": "mullion_tie", "label": "Mullion tie M3", "station_mm": 22500.0, "height_mm": 8000.0},
            {"type": "eave_strut", "label": "Right eave strut", "station_mm": 30000.0, "height_mm": 7000.0},
        ),
        wall_side_count=2,
        braced_bays_per_side=2,
        roof_brace_area_mm2=math.pi * 20.0**2 / 4.0,
        roof_brace_label="20 DIA ROD",
        wall_brace_area_mm2=math.pi * 16.0**2 / 4.0,
        wall_brace_label="16 DIA ROD",
        ultimate_pressure_kpa=1.0,
        service_pressure_kpa=0.7,
        strut_candidates=({"label": "Test strut", "area_mm2": 1000.0, "i_min_mm4": 2.0e6, "fy_mpa": 300.0},),
    ))
    assert math.isclose(bracing["projected_end_wall_area_m2"], expected_area, rel_tol=1e-9)
    assert math.isclose(bracing["ultimate_resultant_kn"], expected_area, rel_tol=1e-9)
    assert math.isclose(bracing["roof_reaction_uls_kn"], expected_area / 2.0, rel_tol=1e-9)
    assert math.isclose(bracing["roof_uls"]["shear_kn"], expected_area / 8.0, rel_tol=1e-9)
    assert math.isclose(bracing["wall_uls"]["shear_kn"], expected_area / 8.0, rel_tol=1e-9)
    assert bracing["roof_uls"]["brace_force_kn"] > bracing["roof_uls"]["shear_kn"]
    assert bracing["wall_sls"]["horizontal_drift_mm"] > 0.0
    assert bracing["strut"]["label"] == "Test strut"
    assert bracing["roof_panel_count"] == 4
    assert len(bracing["eave_struts"]) == 2
    assert len(bracing["mullion_ties"]) == 3
    assert bracing["mullion_ties"][1]["demand_kn"] > bracing["eave_struts"][0]["demand_kn"]

    end_layout = mullion_layout(21000.0, 6000.0, 7.5, "Gable")
    assert end_layout["spaces"] == 3
    assert end_layout["mullion_count"] == 2
    assert math.isclose(end_layout["actual_spacing_mm"], 7000.0, rel_tol=1e-9)
    assert all(math.isclose(item["height_mm"], roof_height_mm(item["station_mm"], 21000.0, 6000.0, 7.5, "Gable"), rel_tol=1e-9) for item in end_layout["mullions"])
    override_layout = mullion_layout(21000.0, 6000.0, 7.5, "Gable", spacing_override_mm=6000.0)
    assert override_layout["spaces"] == 4
    assert math.isclose(override_layout["actual_spacing_mm"], 5250.0, rel_tol=1e-9)
    skillion_layout = mullion_layout(20000.0, 5000.0, 10.0, "Skillion")
    assert skillion_layout["mullions"][-1]["height_mm"] > skillion_layout["mullions"][0]["height_mm"]

    assert len(licensing.machine_id()) == 64
    storage_result = calculate_storage_loads(
        StorageLoadInputs(
            enabled=True,
            bulk_density_kn_m3=8.0,
            internal_friction_angle_deg=30.0,
            backfill_slope_deg=0.0,
            stored_height_m=3.0,
            bay_spacing_m=8.0,
            rail_height_mm=3000.0,
        )
    )
    assert math.isclose(calculate_ka(30.0, 0.0), 1.0 / 3.0, rel_tol=1e-9)
    assert math.isclose(storage_result.ka, 1.0 / 3.0, rel_tol=1e-9)
    assert math.isclose(storage_result.base_pressure_kpa, 8.0, rel_tol=1e-9)
    assert math.isclose(storage_result.total_per_wall_kn, 96.0, rel_tol=1e-9)
    assert math.isclose(storage_result.rail_reaction_per_wall_kn, 32.0, rel_tol=1e-9)
    assert math.isclose(storage_result.base_reaction_per_wall_kn, 64.0, rel_tol=1e-9)
    assert len(storage_result.rail_loads) == 1
    assert math.isclose(storage_result.rail_loads[0].level_mm, 3000.0, rel_tol=1e-9)
    assert math.isclose(storage_result.rail_loads[0].rail_line_load_kn_m, 4.0, rel_tol=1e-9)
    assert math.isclose(storage_result.rail_loads[0].panel_max_moment_knm_per_m, 4.6188021535, rel_tol=1e-9)
    assert math.isclose(storage_result.rail_loads[0].panel_positive_moment_knm_per_m, 4.6188021535, rel_tol=1e-9)
    assert math.isclose(storage_result.rail_loads[0].panel_negative_moment_knm_per_m, 0.0, abs_tol=1e-9)
    assert math.isclose(storage_result.rail_loads[0].rail_bending_moment_knm, 32.0, rel_tol=1e-9)
    reverse_result = calculate_storage_loads(
        StorageLoadInputs(
            enabled=True,
            reverse_direction=True,
            bulk_density_kn_m3=8.0,
            internal_friction_angle_deg=30.0,
            backfill_slope_deg=0.0,
            stored_height_m=3.0,
            bay_spacing_m=8.0,
            rail_height_mm=3000.0,
        )
    )
    reverse_summary = "\n".join(storage_summary_lines(reverse_result))
    assert "Direction       : inward" in reverse_summary
    assert "left wall +X" in reverse_summary
    assert "right wall -X" in reverse_summary
    panel_design = design_storage_panel(storage_result, StoragePanelDesignInputs())
    assert panel_design is not None
    assert math.isclose(panel_design.thickness_mm, 125.0, rel_tol=1e-9)
    assert panel_design.positive_face.reinforcement_type == "mesh"
    assert panel_design.positive_face.reinforcement_label == "SL102"
    assert panel_design.positive_face.capacity_knm_per_m > panel_design.positive_face.demand_knm_per_m
    assert panel_design.positive_face.service_steel_stress_mpa <= panel_design.positive_face.service_stress_limit_mpa
    assert panel_design.positive_face.spacing_mm <= panel_design.positive_face.crack_spacing_limit_mm
    assert panel_design.positive_face.crack_width_mm <= panel_design.positive_face.crack_width_limit_mm
    assert panel_design.negative_face.reinforcement_label == "SL62"
    assert panel_design.negative_face.as_min_mm2_m == 0.0
    assert panel_design.negative_face.spacing_mm <= panel_design.negative_face.crack_spacing_limit_mm
    assert panel_design.deflection_mm < panel_design.deflection_limit_mm
    assert panel_design.effective_i_mm4_m < panel_design.gross_i_mm4_m
    overhang_storage = calculate_storage_loads(
        StorageLoadInputs(
            enabled=True,
            bulk_density_kn_m3=8.0,
            internal_friction_angle_deg=30.0,
            backfill_slope_deg=0.0,
            stored_height_m=4.0,
            bay_spacing_m=8.0,
            rail_height_mm=3000.0,
        )
    )
    assert overhang_storage.rail_loads[0].panel_rail_moment_knm_per_m < 0.0
    assert math.isclose(overhang_storage.rail_loads[0].panel_negative_moment_knm_per_m, 0.4444444444, rel_tol=1e-9)
    assert math.isclose(overhang_storage.rail_loads[0].panel_negative_moment_level_mm, 3000.0, rel_tol=1e-9)
    assert overhang_storage.rail_loads[0].panel_max_moment_level_mm <= 3000.0
    stub_storage = calculate_storage_loads(
        StorageLoadInputs(
            enabled=True,
            load_path_mode="Side-running panels with centre stub",
            bulk_density_kn_m3=8.0,
            internal_friction_angle_deg=30.0,
            backfill_slope_deg=0.0,
            stored_height_m=3.0,
            bay_spacing_m=8.0,
            rail_height_mm=3000.0,
        )
    )
    assert math.isclose(stub_storage.total_per_wall_kn, 48.0, rel_tol=1e-9)
    assert math.isclose(stub_storage.rail_reaction_per_wall_kn, 16.0, rel_tol=1e-9)
    assert math.isclose(stub_storage.base_reaction_per_wall_kn, 32.0, rel_tol=1e-9)
    assert stub_storage.rail_loads[0].rail_load_type == "centre_point"
    assert math.isclose(stub_storage.rail_loads[0].rail_reaction_kn, 16.0, rel_tol=1e-9)
    assert math.isclose(stub_storage.rail_loads[0].panel_span_m, 4.0, rel_tol=1e-9)
    assert math.isclose(stub_storage.rail_loads[0].panel_positive_moment_knm_per_m, 16.0, rel_tol=1e-9)
    assert math.isclose(stub_storage.rail_loads[0].panel_negative_moment_knm_per_m, 0.0, abs_tol=1e-9)
    assert math.isclose(stub_storage.rail_loads[0].rail_bending_moment_knm, 32.0, rel_tol=1e-9)
    assert math.isclose(stub_storage.rail_loads[0].direct_column_reaction_kn, 48.0, rel_tol=1e-9)

    class StorageTestNode:
        def __init__(self, x, y, ux):
            self.x = x
            self.y = y
            self.ux = ux

    class StorageTestStructure:
        def __init__(self):
            self.loads = {}

        def add_load(self, dof, value):
            self.loads[dof] = self.loads.get(dof, 0.0) + value

    test_structure = StorageTestStructure()
    test_nodes = [StorageTestNode(0.0, 0.0, 0), StorageTestNode(0.0, 3000.0, 1)]
    apply_storage_loads_to_columns(test_structure, stub_storage, test_nodes, [], factor=1.0)
    assert math.isclose(test_structure.loads[0], -32000.0, rel_tol=1e-9)
    assert math.isclose(test_structure.loads[1], -32000.0, rel_tol=1e-9)
    continuous_stub_storage = calculate_storage_loads(
        StorageLoadInputs(
            enabled=True,
            load_path_mode="Side-running panels with centre stub",
            panel_support_mode="Continuous over vertical stub",
            bulk_density_kn_m3=8.0,
            internal_friction_angle_deg=30.0,
            backfill_slope_deg=0.0,
            stored_height_m=3.0,
            bay_spacing_m=8.0,
            rail_height_mm=3000.0,
        )
    )
    assert math.isclose(continuous_stub_storage.rail_loads[0].panel_positive_moment_knm_per_m, 9.0, rel_tol=1e-9)
    assert math.isclose(continuous_stub_storage.rail_loads[0].panel_negative_moment_knm_per_m, 16.0, rel_tol=1e-9)
    assert math.isclose(continuous_stub_storage.rail_loads[0].panel_negative_moment_level_mm, 3000.0, rel_tol=1e-9)
    assert math.isclose(continuous_stub_storage.total_per_wall_kn, 60.0, rel_tol=1e-9)
    assert math.isclose(continuous_stub_storage.rail_loads[0].rail_reaction_kn, 20.0, rel_tol=1e-9)
    assert math.isclose(continuous_stub_storage.rail_loads[0].direct_column_reaction_kn, 36.0, rel_tol=1e-9)
    fixed_direction_wind = wind.calculate_wind(wind.WindInputs(wind_region="A5", orientation="E"))
    any_direction_wind = wind.calculate_wind(wind.WindInputs(wind_region="A5", orientation="Any"))
    enclosed_wind = wind.calculate_wind(
        wind.WindInputs(
            wind_region="A5",
            left_wall_clad=True,
            right_wall_clad=True,
            front_wall_clad=True,
            back_wall_clad=True,
        )
    )
    assert enclosed_wind.cpi_max == 0.20
    assert enclosed_wind.cpi_min == -0.20
    assert any_direction_wind.design_direction_multiplier == 1.0
    assert any_direction_wind.wu_kn_m2 > fixed_direction_wind.wu_kn_m2
    skillion_wind = wind.calculate_wind(
        wind.WindInputs(
            roof_shape="Skillion",
            roof_pitch_deg=15.0,
            building_width_m=20.0,
            building_length_m=40.0,
            eave_height_m=6.0,
        )
    )
    upwind_zone = next(zone for zone in skillion_wind.roof_zones if zone.name == "Upwind rafter")
    crosswind_zone = next(zone for zone in skillion_wind.roof_zones if zone.name == "Downwind/crosswind monoslope")
    assert math.isclose(upwind_zone.distance_from_eave_from_m, 0.0, abs_tol=1e-9)
    assert math.isclose(upwind_zone.distance_from_eave_to_m, skillion_wind.roof_slope_length_m, rel_tol=1e-9)
    left_strips = skillion_wind.case_roof_strips("Left +")
    right_strips = skillion_wind.case_roof_strips("Right +")
    end_strips = skillion_wind.case_roof_strips("End +")
    assert left_strips == [(0.0, skillion_wind.roof_slope_length_m, upwind_zone.line_load_max_kn_m)]
    assert right_strips == [(0.0, skillion_wind.roof_slope_length_m, crosswind_zone.line_load_max_kn_m)]
    assert end_strips == right_strips
    assert math.isclose(wind.free_roof_cpn(18.4, 0.5, "leeward", "empty", False), 0.0, abs_tol=1e-12)
    assert math.isclose(wind.transverse_free_roof_cpn("empty", True), -0.3, abs_tol=1e-12)
    assert math.isclose(wind.transverse_free_roof_cpn("empty", False), 0.4, abs_tol=1e-12)
    crane_result = calculate_crane_beam(CraneBeamInputs())
    assert math.isclose(crane_result.mv_max_knm, 207.7, rel_tol=0.02)
    assert math.isclose(crane_result.mhl_coincident_knm, 13.2, rel_tol=0.03)
    side_wall_result = wind.calculate_wind(
        wind.WindInputs(
            eave_height_m=6.0,
            building_width_m=24.0,
            building_length_m=40.0,
            bay_size_m=12.0,
        )
    )
    cyclonic_enclosed = wind.calculate_wind(
        wind.WindInputs(
            wind_region="B2",
            eave_height_m=6.0,
            building_width_m=24.0,
            building_length_m=40.0,
            bay_size_m=12.0,
            left_wall_clad=True,
            right_wall_clad=True,
            front_wall_clad=True,
            back_wall_clad=True,
        )
    )
    assert cyclonic_enclosed.cpi_max == 0.60
    assert cyclonic_enclosed.cpi_min == -0.60
    cyclonic_louvre = wind.calculate_wind(
        wind.WindInputs(
            wind_region="C",
            eave_height_m=6.0,
            building_width_m=24.0,
            building_length_m=40.0,
            bay_size_m=12.0,
            left_wall_clad=True,
            right_wall_clad=True,
            front_wall_clad=True,
            back_wall_clad=True,
            internal_pressure_mode="Louvre vented walls",
        )
    )
    assert cyclonic_louvre.cpi_max == 0.30
    assert cyclonic_louvre.cpi_min == -0.30
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
        guarded_settings = license_server.ServerSettings(
            db_path=db_path,
            require_persistent_db=True,
            persistent_root=str(Path(temp_dir) / "missing-mount"),
        )
        try:
            license_server.validate_database_location(guarded_settings)
            raise AssertionError("Missing persistent mount should fail validation")
        except RuntimeError as exc:
            assert "not mounted" in str(exc)
        license_server.init_db(db_path)
        license_key = license_server.create_license(db_path, "Smoke Test", "standard", None)
        assert license_server.backup_path(db_path).exists()
        Path(db_path).unlink()
        assert license_server.restore_database_backup(db_path)
        assert len(license_server.list_licenses(db_path)) == 1
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

        migration_key = license_server.create_license(db_path, "Migration Test", "standard", None, max_machines=1)
        legacy_id = "legacy-machine-id"
        stable_id = "stable-machine-guid-id"
        legacy_activation = license_server.activate_license(server_settings, migration_key, legacy_id, "TESTPC (User)")
        assert legacy_activation["ok"]
        migrated = license_server.check_license(
            server_settings,
            legacy_activation["token"],
            stable_id,
            "TESTPC (User)",
            legacy_machine_id=legacy_id,
        )
        assert not migrated["revoked"]
        migrated_payload = license_server.verify_token(migrated["token"], public_hex)
        assert migrated_payload["machine_id"] == stable_id
        migration_hash = license_server.hash_license_key(migration_key)
        migration_detail = license_server.license_detail(db_path, migration_hash)
        assert migration_detail is not None
        assert len(migration_detail["activation_rows"]) == 1
        assert migration_detail["activation_rows"][0]["machine_id"] == stable_id
        blocked = license_server.activate_license(server_settings, migration_key, "different-machine")
        assert not blocked["ok"]
        license_server.delete_activation(db_path, migration_hash, stable_id)
        removed_check = license_server.check_license(server_settings, migrated["token"], stable_id)
        assert not removed_check["revoked"]

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
            listed = client.get("/admin/licenses", headers={"X-Admin-Token": "smoke-admin"})
            assert listed.status_code == 200
            licenses = listed.json()["licenses"]
            assert len(licenses) == 1
            api_license_hash = licenses[0]["license_key_hash"]
            assert licenses[0]["customer"] == "API Smoke"
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
            detail = client.get(f"/admin/licenses/{api_license_hash}", headers={"X-Admin-Token": "smoke-admin"})
            assert detail.status_code == 200
            assert len(detail.json()["activation_rows"]) == 1
            canceled = client.post(
                f"/admin/licenses/{api_license_hash}/cancel",
                headers={"X-Admin-Token": "smoke-admin"},
            )
            assert canceled.status_code == 200
            revoked = client.post(
                "/check",
                json={"token": activated.json()["token"], "machine_id": licensing.machine_id()},
            )
            assert revoked.status_code == 200
            assert revoked.json()["revoked"]
            reactivated = client.post(
                f"/admin/licenses/{api_license_hash}/reactivate",
                headers={"X-Admin-Token": "smoke-admin"},
            )
            assert reactivated.status_code == 200
            removed = client.delete(
                f"/admin/licenses/{api_license_hash}/activations/{licensing.machine_id()}",
                headers={"X-Admin-Token": "smoke-admin"},
            )
            assert removed.status_code == 200
            admin_page = client.get("/admin")
            assert admin_page.status_code == 200
            assert "PortalCalc License Admin" in admin_page.text


def run_ui_smoke_checks(quick: bool = False) -> None:
    import os

    from PySide6.QtCore import QEventLoop, QThread, QTimer, Signal
    from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

    os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="portalcalc_smoke_appdata_")

    import main
    import structural_assistant
    from loads import apply_roof_vertical_loads

    shorter_eave = {"value": 46.8, "height_mm": 6000.0}
    taller_eave = {"value": 46.9, "height_mm": 7980.0}
    assert main.governing_height_deflection_item([shorter_eave, taller_eave]) is shorter_eave

    top_stations = main.pitch_compensated_gable_top_stations(27000.0, 1300.0, 22.0, 24)
    assert math.isclose(top_stations[-1], 27000.0, abs_tol=1e-6)
    assert all(math.isclose(a + b, 27000.0, abs_tol=1e-6) for a, b in zip(top_stations, reversed(top_stations)))
    roof_tan = math.tan(math.radians(22.0))
    bottom_stations = [27000.0 * index / 24 for index in range(25)]
    compensated_angles = []
    for index in range(12):
        if index % 2 == 0:
            run = top_stations[index + 1] - bottom_stations[index]
            rise = 1300.0 + roof_tan * run
            web_vector = (run, rise)
        else:
            run = bottom_stations[index + 1] - top_stations[index]
            rise = 1300.0 - roof_tan * run
            web_vector = (run, -rise)
        chord_vector = (1.0, roof_tan)
        cross = abs(web_vector[0] * chord_vector[1] - web_vector[1] * chord_vector[0])
        dot = abs(web_vector[0] * chord_vector[0] + web_vector[1] * chord_vector[1])
        compensated_angles.append(math.degrees(math.atan2(cross, dot)))
    assert max(compensated_angles) - min(compensated_angles) < 1e-6

    app = QApplication.instance() or QApplication([])

    event_order = []

    class GuidedEventOrderProbe(QThread):
        result_ready = Signal(object)

        def run(self):
            self.result_ready.emit(object())

    event_order_probe = GuidedEventOrderProbe()
    event_order_loop = QEventLoop()

    def handle_event_order_result(_result):
        event_order.append("result-start")
        QApplication.processEvents()
        event_order.append("result-end")

    main.connect_guided_worker_success(event_order_probe.result_ready, handle_event_order_result)
    event_order_probe.finished.connect(
        lambda: (event_order.append("worker-finished"), event_order_loop.quit())
    )
    QTimer.singleShot(5000, event_order_loop.quit)
    event_order_probe.start()
    event_order_loop.exec()
    event_order_probe.wait()
    assert event_order == ["result-start", "result-end", "worker-finished"]

    window = main.MainWindow()
    catalogue_chs = window.catalog.chs_profiles()
    assert len(catalogue_chs) >= len(main.STRUCTURAL_CHS_C350L0_SIZES)
    chs_114 = window.catalog.get("114.3*4.8 CHS")
    assert chs_114 is not None
    assert chs_114.shape_type == "Circular Hollow Section"
    assert chs_114.fabrication == "Cold-formed"
    assert chs_114.fy == 350.0 and chs_114.fu == 430.0
    assert math.isclose(chs_114.Iyp, chs_114.Izp, rel_tol=1e-12)
    assert math.isclose(chs_114.J, 2.0 * chs_114.Iyp, rel_tol=1e-12)
    assert chs_114.display_name.startswith("114.3*4.8 CHS (13.0 kg/m)")
    chs_406 = window.catalog.get("406.4*6.4 CHS")
    assert chs_406 is not None
    assert chs_406.display_name.startswith("406.4*6.4 CHS (63.1 kg/m)")
    assert max(profile.depth for profile in catalogue_chs) == 406.4
    column_combos = (
        window.left_column_profile_combo,
        window.right_column_profile_combo,
        window.internal_column_profile_combo,
        window.left_lean_column_profile_combo,
        window.right_lean_column_profile_combo,
    )
    for combo in column_combos:
        original_text = combo.currentText()
        chs_index = combo.findText(chs_114.display_name)
        assert chs_index >= 0
        assert combo.findText(chs_406.display_name) > chs_index
        combo.setCurrentIndex(chs_index)
        assert window.selected_profile(combo) is chs_114
        assert window.section_cost_bucket(window.selected_profile(combo)) == "shs"
        window.set_combo_text_if_present(combo, original_text)
    window.canvas.plot_load_arrows([(0.0, 0.0, 1000.0, 0.0)], show_labels=False)
    window.canvas.plot_load_arrows([(0.0, 0.0, 1000.0, 0.0)], show_labels=True)
    assert window.optimise_structure_button.text() == "Optimise"
    assert "low-cost verified design" in window.optimise_structure_button.toolTip()
    assert window.sections_guided_optimise_button.text() == "AI Optimise"
    assert window.sections_ai_help_button.text() == "AI Help"
    window.show_sections_ai_help_dialog()
    assert window.sections_ai_help_dialog.windowTitle() == "AI Help"
    assert window.sections_ai_review_button.text() == "Review Current Design"
    assert window.sections_ai_explain_button.text() == "Explain Governing Check"
    assert window.sections_ai_ask_button.text() == "Ask Structural Assistant"
    assert window.sections_ai_new_chat_button.text() == "New chat"
    captured_help_questions = []
    original_guided_help = window.guided_help_with_assistant
    window.guided_help_with_assistant = lambda question=None, continue_chat=True: (
        captured_help_questions.append((question, continue_chat)) or True
    )
    window.sections_ai_help_question_box.setPlainText("Would another 50 mm of lean-to depth save cost?")
    try:
        assert window.start_sections_ai_help_request("ask") is True
    finally:
        window.guided_help_with_assistant = original_guided_help
    assert captured_help_questions == [("Would another 50 mm of lean-to depth save cost?", True)]
    assert window.sections_ai_help_question_box.toPlainText() == ""
    window.append_sections_ai_help_chat("You", "Test follow-up")
    window.append_sections_ai_help_chat("Assistant", "Verified answer")
    assert "You:\nTest follow-up" in window.sections_ai_help_output_box.toPlainText()
    assert "Assistant:\nVerified answer" in window.sections_ai_help_output_box.toPlainText()
    window.assistant_help_chat_session = {"conversation": []}
    window.clear_sections_ai_help_chat()
    assert window.assistant_help_chat_session is None
    assert window.sections_ai_help_output_box.toPlainText() == ""
    window.sections_ai_help_dialog.close()
    assert window.whats_new_action.text().replace("&", "") == "What's New"
    help_dialog = window.show_help_dialog("whats_new")
    assert window.help_tabs.count() == 3
    assert window.help_tabs.tabText(0) == "What's New"
    assert window.help_tabs.tabText(1) == "EULA"
    assert "SLS Structural Software" in window.help_tabs.widget(1).toPlainText()
    assert "Portal Calc 1.5 - 14 August 2026" in window.whats_new_text.toPlainText()
    assert "Longitudinal bracing" in window.whats_new_text.toPlainText()
    assert "406.4 mm diameter" in window.whats_new_text.toPlainText()
    help_dialog.close()
    importance_tooltip = window.importance_combo.toolTip()
    terrain_tooltip = window.terrain_combo.toolTip()
    assert "IL 1: Rarely occupied farm sheds" in importance_tooltip
    assert "IL 4: Emergency buildings (CFA)" in importance_tooltip
    assert "TC 1: Open water" in terrain_tooltip
    assert "TC 2.5: Light industrial, scattered buildings" in terrain_tooltip
    assert "TC 4: City centre" in terrain_tooltip

    assert "building span" in window.span_input.toolTip()
    assert "confirm" in window.importance_combo.toolTip().lower()
    assert "both eave nodes" in window.end_wall_braced_sides_combo.toolTip().lower()
    assert "300-350 mm" in window.left_canopy_shallow_depth_input.toolTip()
    assert "Automatically selected" in window.web_split_label.toolTip()

    assert window.left_canopy_g_load_input.text() == "0.15"
    assert window.left_canopy_q_load_input.text() == "0.25"
    assert window.right_canopy_g_load_input.text() == "0.15"
    assert window.right_canopy_q_load_input.text() == "0.25"
    assert window.left_canopy_shallow_depth_input.text() == "300"
    migrated_depth = main.canopy_depth_mm_from_preset(
        {"left_shallow_depth_mm": "300", "left_length_mm": "6000", "left_pitch_deg": "5"},
        "left",
        "Canopy",
        "0",
    )
    assert math.isclose(float(migrated_depth), 300.0 + math.tan(math.radians(5.0)) * 6000.0, rel_tol=1e-6)
    assert main.canopy_depth_mm_from_preset(
        {"left_shallow_depth_mm": "300"}, "left", "Lean-to", "0"
    ) == "300"
    window.left_canopy_type_combo.setCurrentText("Canopy")
    window.left_canopy_length_input.setText("6")
    window.left_canopy_pitch_input.setText("5")
    window.update_canopy_depth_from_geometry("left")
    assert window.left_canopy_shallow_depth_input.text() == "850"
    calculated_shallow_depth = 850.0 - math.tan(math.radians(5.0)) * 6000.0
    assert 300.0 <= calculated_shallow_depth < 350.0
    window.left_canopy_type_combo.setCurrentText("Lean-to")
    window.left_canopy_shallow_depth_input.setText("777")
    window.update_canopy_depth_from_geometry("left")
    assert window.left_canopy_shallow_depth_input.text() == "777"
    window.left_canopy_type_combo.setCurrentText("None")
    window.left_canopy_length_input.setText("0")
    window.left_canopy_pitch_input.setText("5")
    window.left_canopy_shallow_depth_input.setText("300")
    canopy_preset_data = window.collect_preset_data()["canopies"]
    assert canopy_preset_data["left_depth_mm"] == "300"
    assert "left_shallow_depth_mm" not in canopy_preset_data

    names = [combo.name for combo in window.load_combinations]
    assert "1.2G + 1.5Q + Crane Left" in names
    assert "1.2G + 1.5Q + Crane Right" in names
    assert "1.2G + Wu (End +, Cpi +)" in names
    assert window.envelope_plan_selector.wall_states() == {"back": True, "left": True, "right": True, "front": True}

    if quick:
        window._suppress_auto_web_split_on_solve = True
    window.generate_and_solve()
    assistant_dossier = window.assistant_design_dossier()
    assert assistant_dossier["schema"] == "portalcalc.structural_assistant.design_dossier.v1"
    assert assistant_dossier["member_checks"]
    assert "overall_governing" in assistant_dossier
    assert window.assistant_model_combo.currentText() == "gpt-5.6-luna"
    assert window.assistant_reasoning_combo.currentText() in {"none", "low", "medium", "high", "xhigh", "max"}
    help_snapshot = window.assistant_help_snapshot()
    assert help_snapshot["schema"] == "portalcalc.structural_assistant.guided_help.v1"
    assert help_snapshot["objective"] == "minimise verified estimated total cost while every reported check passes"
    assert 1 <= len(help_snapshot["highest_checks"]) <= 8
    assert help_snapshot["overall_governing"] == help_snapshot["highest_checks"][0]
    assert len(json.dumps(help_snapshot, default=str)) < 25_000
    assert help_snapshot["member_economics"]
    assert all("approximate_selected_section_cost" in item for item in help_snapshot["member_economics"])
    economics_costs = [item["approximate_selected_section_cost"] for item in help_snapshot["member_economics"]]
    assert economics_costs == sorted(economics_costs, reverse=True)
    bracing_concepts = window.assistant_unverified_concept_options(
        {"end_wall_bracing": "None"},
        [{
            "type": "serviceability",
            "check": "Column top deflection SLS",
            "ratio": 0.977,
            "limit_basis": "H/150",
        }],
        {"front": True, "back": True},
    )
    assert bracing_concepts[0]["id"] == "end_wall_portal_restraint"
    assert bracing_concepts[0]["available_trial_settings"] == ["One end", "Both ends"]
    assert "16, 20, 24, and 30 DIA rods" in bracing_concepts[0]["costing_caveat"]
    assert "12 DIA" not in bracing_concepts[0]["costing_caveat"]
    assert "excluded" in bracing_concepts[0]["costing_caveat"]
    assert window.assistant_unverified_concept_options(
        {"end_wall_bracing": "One end"},
        [{"type": "serviceability", "check": "Column top deflection SLS", "ratio": 0.99}],
        {"front": True, "back": True},
    ) == []
    assert window.assistant_unverified_concept_options(
        {"end_wall_bracing": "None"},
        [{"type": "serviceability", "check": "Column top deflection SLS", "ratio": 0.50}],
        {"front": True, "back": True},
    ) == []
    one_clad_concept = window.assistant_unverified_concept_options(
        {"end_wall_bracing": "None"},
        [{"type": "serviceability", "check": "Column top deflection SLS", "ratio": 0.99}],
        {"front": True, "back": False},
    )
    assert one_clad_concept[0]["available_trial_settings"] == ["One end"]
    assert window.assistant_unverified_concept_options(
        {"end_wall_bracing": "None"},
        [{"type": "serviceability", "check": "Column top deflection SLS", "ratio": 0.99}],
        {"front": False, "back": False},
    ) == []
    assert window.sections_guided_optimise_button.text() == "AI Optimise"
    assert "cheapest fully verified" in window.sections_guided_optimise_button.toolTip()
    assert "results will appear here" in window.sections_guided_optimise_status_label.text().lower()
    assert window.assistant_optimise_button.text() == "Guided Optimise"
    assert "cheapest fully verified" in window.assistant_optimise_button.toolTip()
    assert window.assistant_help_button.text() == "Help? Run Guided Trials"
    assert [
        window.assistant_model_combo.itemText(index)
        for index in range(window.assistant_model_combo.count())
    ] == ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
    assert {"left_lean_column_steps", "right_lean_column_steps"}.issubset(
        set(structural_assistant.HELP_TRIAL_TOOL["parameters"]["required"])
    )
    assert window.metroll_section_display("C/Z 200 19", "Single span") == "C 200 19"
    assert window.metroll_section_display("C/Z 200 19", "Double span") == "C 200 19"
    assert window.metroll_section_display("C/Z 200 19", "Continuous lapped") == "Z 200 19"
    column_focus = window.guided_optimisation_search_settings({"search_focus": "column_stiffness_trade"})
    guided_balanced_focus = window.guided_optimisation_search_settings({"search_focus": "balanced"})
    baseline_focus = window.guided_optimisation_search_settings(None)
    assert column_focus["column_depth_steps"] > guided_balanced_focus["column_depth_steps"]
    assert column_focus["column_candidates"] > guided_balanced_focus["column_candidates"]
    assert column_focus["chord_candidates"] < baseline_focus["chord_candidates"]
    assert column_focus["max_passes"] < baseline_focus["max_passes"]
    guided_snapshot = window.assistant_guided_optimisation_snapshot({
        "verified": True,
        "cost": help_snapshot["estimated_total_cost"],
        "ratio": help_snapshot["overall_governing"]["ratio"],
        "governing_group": help_snapshot["overall_governing"]["group"],
        "governing_check": help_snapshot["overall_governing"]["check"],
        "exact_solves": 12,
        "search_focus": "baseline",
    })
    assert guided_snapshot["schema"] == "portalcalc.structural_assistant.ai_optimise.v3"
    assert guided_snapshot["strategy_budget"] == 2
    assert guided_snapshot["probe_budget"] == 4
    assert guided_snapshot["baseline_optimiser_result"]["verified"] is True
    assert "structural_efficiency_audit" in guided_snapshot
    assert "cost_weighted_strength_utilisation" in guided_snapshot["structural_efficiency_audit"]
    synthetic_imbalance = window.assistant_structural_efficiency_audit([
        {
            "group": "LEFT_COLUMN",
            "section": "Synthetic heavy left",
            "utilisation": 0.10,
            "approximate_selected_section_cost": 4000.0,
            "stiffness_metric": 2.5e9,
        },
        {
            "group": "RIGHT_COLUMN",
            "section": "Synthetic efficient right",
            "utilisation": 0.90,
            "approximate_selected_section_cost": 1000.0,
            "stiffness_metric": 1.0e8,
        },
    ])
    assert synthetic_imbalance["requires_followup"] is True
    assert synthetic_imbalance["material_low_utilisation_members"][0]["group"] == "LEFT_COLUMN"
    assert "material_expensive_member_below_35_percent_strength_utilisation" in synthetic_imbalance["flags"]
    base = window.assistant_capture_help_base_state(help_snapshot)
    base_signature = base["model_signature"]
    window.assistant_help_session = {
        "snapshot": help_snapshot,
        "base": base,
        "trials": [],
    }
    current_depth = float(window.depth_input.text())
    minimum_depth = window.truss_depth_from_span(main.MAIN_TRUSS_DEPTH_MIN_RATIO)
    maximum_depth = window.max_main_truss_depth_mm()
    if current_depth + 50.0 <= maximum_depth + 1e-9:
        depth_delta = 50
    elif current_depth - 50.0 >= minimum_depth - 1e-9:
        depth_delta = -50
    else:
        depth_delta = 0
    trial = window.assistant_execute_help_trial({
        "hypothesis": "Smoke-test isolated depth sensitivity",
        "truss_depth_delta_mm": depth_delta,
        "left_addition_depth_delta_mm": 0,
        "right_addition_depth_delta_mm": 0,
        "left_column_steps": 1 if depth_delta == 0 else 0,
        "right_column_steps": 0,
        "top_member_steps": 0,
        "bottom_chord_steps": 0,
        "web_steps": 0,
        "panel_layout_steps": 0,
    })
    assert trial["applied_changes"]
    assert trial["solved"]
    assert "estimated_cost_saving" in trial
    assert "passes_all_reported_checks" in trial
    assert "eligible_cost_improvement" in trial
    assert trial["live_design_restored"] is True
    assert window.assistant_help_model_signature() == base_signature
    window.assistant_help_session = None
    baseline_cost = float(help_snapshot["estimated_total_cost"])
    guided_baseline = {
        "kind": "baseline",
        "state": base,
        "snapshot": guided_snapshot,
        "result": guided_snapshot["baseline_optimiser_result"],
        "cost": baseline_cost,
        "verified": True,
        "strategy_number": 0,
        "hypothesis": "Smoke deterministic baseline",
    }
    window.assistant_optimise_session = {
        "baseline": guided_baseline,
        "best": guided_baseline,
        "probes": [],
        "strategies": [],
    }
    optimise_probe = window.assistant_execute_optimisation_probe({
        "hypothesis": "Smoke-test an isolated AI Optimise probe",
        "truss_depth_delta_mm": depth_delta,
        "left_addition_depth_delta_mm": 0,
        "right_addition_depth_delta_mm": 0,
        "left_column_steps": 1 if depth_delta == 0 else 0,
        "right_column_steps": 0,
        "left_lean_column_steps": 0,
        "right_lean_column_steps": 0,
        "top_member_steps": 0,
        "bottom_chord_steps": 0,
        "web_steps": 0,
        "panel_layout_steps": 0,
    })
    assert optimise_probe["solved"] is True
    assert optimise_probe["optimiser_invoked"] is False
    assert "structural_efficiency_audit" in optimise_probe["after"]
    assert optimise_probe["baseline_live_design_restored"] is True
    original_run_full_optimiser = window.assistant_run_full_optimiser
    window.assistant_run_full_optimiser = lambda strategy=None: {
        "verified": True,
        "cost": baseline_cost - 100.0,
        "ratio": help_snapshot["overall_governing"]["ratio"],
        "governing_group": help_snapshot["overall_governing"]["group"],
        "governing_check": help_snapshot["overall_governing"]["check"],
        "exact_solves": 17,
        "report": "Coordinated search: 17 exact section solve(s)\nFINAL VERIFICATION: OK",
        "guided_seed_changes": [],
        "search_focus": "column_stiffness_trade",
    }
    try:
        strategy_result = window.assistant_execute_optimisation_strategy({
            "hypothesis": "Smoke-test a full optimiser branch",
            "search_focus": "column_stiffness_trade",
            "truss_depth_delta_mm": 200,
            "left_addition_depth_delta_mm": 0,
            "right_addition_depth_delta_mm": 200,
            "left_column_steps": -4,
            "right_column_steps": 1,
            "left_lean_column_steps": 0,
            "right_lean_column_steps": 0,
            "top_member_steps": 0,
            "bottom_chord_steps": 0,
            "web_steps": 0,
            "panel_layout_steps": 0,
        })
    finally:
        window.assistant_run_full_optimiser = original_run_full_optimiser
    assert strategy_result["verified"] is True
    assert strategy_result["improved_best_incumbent"] is True
    assert math.isclose(strategy_result["saving_vs_baseline"], 100.0, abs_tol=1e-9)
    assert strategy_result["baseline_live_design_restored"] is True
    assert window.assistant_optimise_session["best"]["strategy_number"] == 1
    decision_snapshot = copy.deepcopy(guided_snapshot)
    decision_snapshot["unverified_concept_options"] = copy.deepcopy(bracing_concepts)
    window.assistant_optimise_session.update({
        "snapshot": decision_snapshot,
        "concept_trials": [],
        "design_decisions": {},
    })
    original_prompt_restraint_scope = window.assistant_prompt_end_wall_restraint_scope
    original_run_full_optimiser = window.assistant_run_full_optimiser
    original_end_column_balance = window.assistant_end_column_balance
    window.assistant_prompt_end_wall_restraint_scope = lambda concept, rationale="": {
        "accepted": True,
        "configurations": [
            {"ends": "One end", "sides": "One side"},
            {"ends": "Both ends", "sides": "Both sides"},
        ],
    }

    def fake_restraint_optimiser(strategy=None):
        saving = 300.0 if (
            window.eave_x_restraint_combo.currentText() == "Both ends"
            and window.end_wall_braced_sides_combo.currentText() == "Both sides"
        ) else 50.0
        return {
            "verified": True,
            "cost": baseline_cost - saving,
            "ratio": 0.82,
            "governing_group": "SLS",
            "governing_check": "Column top deflection SLS",
            "exact_solves": 19,
            "report": "FINAL VERIFICATION: OK",
            "guided_seed_changes": [],
            "search_focus": "column_stiffness_trade",
        }

    window.assistant_run_full_optimiser = fake_restraint_optimiser
    window.assistant_end_column_balance = lambda: {
        "well_utilised": True,
        "columns": [
            {
                "group": "LEFT_COLUMN",
                "section": window.left_column_profile_combo.currentText(),
                "strength_ratio": 0.82,
                "strength_check": "Combined",
                "deflection_ratio": 0.70,
                "deflection_check": "Top deflection",
            },
            {
                "group": "RIGHT_COLUMN",
                "section": window.right_column_profile_combo.currentText(),
                "strength_ratio": 0.80,
                "strength_check": "Combined",
                "deflection_ratio": 0.72,
                "deflection_check": "Top deflection",
            },
        ],
        "minimum_strength_ratio": 0.80,
        "maximum_deflection_ratio": 0.72,
    }
    try:
        decision_result = window.assistant_execute_end_wall_restraint_decision({
            "decision_id": "end_wall_portal_restraint",
            "rationale": "Column deflection remains near its serviceability limit.",
        })
    finally:
        window.assistant_prompt_end_wall_restraint_scope = original_prompt_restraint_scope
        window.assistant_run_full_optimiser = original_run_full_optimiser
        window.assistant_end_column_balance = original_end_column_balance
    assert decision_result["user_accepted"] is True
    # Each layout starts at the lightest brace and tests just one further size
    # once the columns are strength-led. The unchanged sections then stop the
    # adaptive search instead of scanning the full brace catalogue.
    assert len(decision_result["comparisons"]) == 4
    assert all(item["verified"] for item in decision_result["comparisons"])
    assert [
        item["configuration"]["brace_size"]
        for item in decision_result["comparisons"][:2]
    ] == ["16 DIA ROD", "20 DIA ROD"]
    assert window.assistant_optimise_session["best"]["kind"] == "concept"
    assert window.assistant_optimise_session["best"]["configuration"] == {
        "ends": "Both ends", "sides": "Both sides", "brace_size": "16 DIA ROD",
    }
    assert decision_result["baseline_live_design_restored"] is True
    assert window.eave_x_restraint_combo.currentText() == base["end_wall_bracing"]
    assert window.end_wall_braced_sides_combo.currentText() == base["end_wall_braced_sides"]
    assert window.end_wall_brace_size_combo.currentText() == base["end_wall_brace_size"]
    window.assistant_optimise_session = None
    deflection_overlay = window.governing_column_deflection_overlay
    assert deflection_overlay is not None
    assert deflection_overlay["combo"] in window.column_deflection_check_label.toolTip()
    assert "Load case:" in window.column_deflection_check_label.toolTip()
    assert window.governing_deflection_overlay_checkbox.isChecked()
    assert window.collect_preset_data()["display"]["show_governing_deflection"] is True
    window.result_combo.setCurrentText("Geometry")
    window.refresh_plot()
    assert not any("Governing frame deflection" in item.get_text() for item in window.canvas.ax.texts)
    window.set_deflection_hover_overlay_active(True)
    resolved_deflection_overlay = window.resolved_governing_column_deflection_overlay()
    assert resolved_deflection_overlay is not None
    assert resolved_deflection_overlay["displacements"] is not None
    assert any("Governing frame deflection" in item.get_text() for item in window.canvas.ax.texts)
    window.set_deflection_hover_overlay_active(False)
    assert not any("Governing frame deflection" in item.get_text() for item in window.canvas.ax.texts)
    assert window.truss_deflection_check_label in window.deflection_hover_overlays
    assert "Load case:" in window.truss_deflection_check_label.toolTip()
    window.set_deflection_hover_overlay_active(True, window.truss_deflection_check_label)
    resolved_truss_overlay = window.resolved_deflection_hover_overlay()
    assert resolved_truss_overlay is not None
    assert resolved_truss_overlay["direction"] == "vertical"
    assert resolved_truss_overlay["displacements"] is not None
    assert any("dy =" in item.get_text() for item in window.canvas.ax.texts)
    window.set_deflection_hover_overlay_active(False, window.truss_deflection_check_label)
    assert not any("Governing frame deflection" in item.get_text() for item in window.canvas.ax.texts)
    assert window.derived_frame_type_label.text() == "Enclosed"
    assert "Not enforced" in window.license_status_label.text()
    assert window.top_profile_combo.currentText().startswith("65*3 SHS")
    assert window.bottom_profile_combo.currentText().startswith("65*3 SHS")
    initial_purlin_match = (window.purlin_check or {}).get("match", {})
    if initial_purlin_match.get("status") == "OK":
        displayed_purlin = window.metroll_section_display(
            initial_purlin_match["section"],
            window.purlin_check["span_type"],
        )
        assert displayed_purlin in window.purlin_check_label.text()
        assert "C/Z" not in window.purlin_check_label.text()
    if quick:
        assert "webs" in window.web_split_label.text()
        window.confirm_on_close = False
        window.close()
        app.quit()
        return
    if not window.auto_web_profile_assignments.get("WEB"):
        active_web_specs = [
            spec for spec in window.active_auto_size_group_specs()
            if spec[2] in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}
        ]
        assert window.auto_size_two_web_assignments(active_web_specs)
        window._preserve_web_assignments_on_solve = True
        try:
            window.generate_and_solve()
        finally:
            window._preserve_web_assignments_on_solve = False
    assert window.auto_web_profile_assignments.get("WEB")
    assert "auto-sized split active" in window.web_check_label.text()
    assert "6 mm continuous fillet weld" in window.section_checks_box.toPlainText()
    assert "branch angle 90.0 deg" not in window.section_checks_box.toPlainText()
    assert "Hollow local" in window.section_checks_box.toPlainText()
    assert window.top_profile_combo.currentText().startswith("65*3 SHS")
    assert window.bottom_profile_combo.currentText().startswith("65*3 SHS")
    assert "50*2.5 SHS" not in set(window.auto_web_profile_assignments.get("WEB", {}).values())
    assert "webs" in window.web_split_label.text()
    assert "posts" not in window.web_split_label.text()
    assert "K face plast" in window.section_checks_box.toPlainText()
    assert "auto-sized split active" in window.web_check_label.text()
    web_65 = window.catalog.get("65*2.5 SHS (4.8 kg/m)")
    chord_125 = window.catalog.get("125*4 SHS (14.8 kg/m)")
    assert web_65 is not None and chord_125 is not None
    k_with_50_peer = window.welded_chord_web_connection_checks(
        web_65, chord_125, 100.0,
        {"branch_angle_deg": 45.0, "k_joint": {"peer_width_mm": 50.0}},
    )
    k_with_65_peer = window.welded_chord_web_connection_checks(
        web_65, chord_125, 100.0,
        {"branch_angle_deg": 45.0, "k_joint": {"peer_width_mm": 65.0}},
    )
    capacity_50 = next(check["capacity"] for check in k_with_50_peer if check["label"] == "K face plast")
    capacity_65 = next(check["capacity"] for check in k_with_65_peer if check["label"] == "K face plast")
    assert capacity_65 > capacity_50
    assert "branches 65/50 mm" in next(check["notes"] for check in k_with_50_peer if check["label"] == "K face plast")
    chord_100 = window.catalog.get("100*5 SHS (14.2 kg/m)")
    web_89 = window.catalog.get("89*3.5 SHS (9.0 kg/m)")
    web_100 = window.catalog.get("100*3 SHS (8.9 kg/m)")
    assert chord_100 is not None and web_89 is not None and web_100 is not None
    assert window.web_profile_fits_chords(web_89, "WEB", chord_profiles=(chord_100, chord_100))
    assert not window.web_profile_fits_chords(web_100, "WEB", chord_profiles=(chord_100, chord_100))
    equal_width_checks = window.welded_chord_web_connection_checks(web_100, chord_100, 100.0)
    assert equal_width_checks[0]["label"] == "Branch fit"
    assert "one nominal size below" in equal_width_checks[0]["notes"]

    # A chord doubler may be triggered on an analysis subdivision, but its
    # physical run must extend to actual web/chord joints at both ends.
    boundary_expansion_checked = False
    for chord_group, web_groups in {
        "TOP": {"WEB", "POST", "APEX"},
        "BOTTOM": {"WEB", "POST", "APEX"},
    }.items():
        web_nodes = {
            (round(node.x, 6), round(node.y, 6))
            for element in window.structure.elements if element.group in web_groups
            for node in (element.start, element.end)
        }
        for element in [item for item in window.structure.elements if item.group == chord_group]:
            endpoint_keys = {
                (round(element.start.x, 6), round(element.start.y, 6)),
                (round(element.end.x, 6), round(element.end.y, 6)),
            }
            if endpoint_keys <= web_nodes:
                continue
            source = {
                (chord_group, element.id): {
                    "group": chord_group,
                    "member": element.id,
                    "x1": element.start.x,
                    "y1": element.start.y,
                    "x2": element.end.x,
                    "y2": element.end.y,
                    "length_mm": element.length(),
                    "single_ratio": 1.1,
                }
            }
            expanded = window.expand_doubled_chord_zones_to_web_joints(window.structure, source)
            expanded_elements = {
                int(zone["member"]): next(
                    item for item in window.structure.elements
                    if item.group == chord_group and item.id == int(zone["member"])
                )
                for zone in expanded.values()
            }
            run_nodes = [node for item in expanded_elements.values() for node in (item.start, item.end)]
            run_low = min(run_nodes, key=lambda node: (node.x, node.y))
            run_high = max(run_nodes, key=lambda node: (node.x, node.y))
            assert (round(run_low.x, 6), round(run_low.y, 6)) in web_nodes
            assert (round(run_high.x, 6), round(run_high.y, 6)) in web_nodes
            boundary_expansion_checked = True
            break
        if boundary_expansion_checked:
            break

    # Ordinary Generate & Solve must keep the FEA webs fixed while publishing
    # a repeatable calculated design assignment beside that analysis model.
    def web_assignment_signature():
        return tuple(sorted(
            (group, int(member_id), profile_name)
            for group, group_assignments in window.auto_web_profile_assignments.items()
            for member_id, profile_name in group_assignments.items()
        ))

    first_web_signature = web_assignment_signature()
    first_web_base = window.web_profile_combo.currentText()
    first_web_profiles = {
        element.id: element.profile.name
        for element in window.structure.elements
        if element.group == "WEB"
    }
    analysis_web_profile = window.analysis_web_profile_for_group("WEB")
    assert analysis_web_profile is not None
    assert set(first_web_profiles.values()) == {analysis_web_profile.name}
    first_doubled_zones = [
        (zone.get("group"), zone.get("member"), zone.get("web_member"), zone.get("combo"))
        for zone in window.design_envelope_actions.get("doubled_chord_zones", [])
    ]
    first_displacements = np.array(window.displacements, copy=True)
    original_single_topology_solve = window._generate_and_solve_once
    repeated_solve_calls = {"count": 0}
    def counted_single_topology_solve():
        repeated_solve_calls["count"] += 1
        return original_single_topology_solve()
    window._generate_and_solve_once = counted_single_topology_solve
    try:
        window.generate_and_solve()
    finally:
        window._generate_and_solve_once = original_single_topology_solve
    assert repeated_solve_calls["count"] == 1
    assert window._panel_topology_cache_count == int(window.panel_count_label.text())
    assert web_assignment_signature() == first_web_signature
    assert window.web_profile_combo.currentText() == first_web_base
    assert {
        element.id: element.profile.name
        for element in window.structure.elements
        if element.group == "WEB"
    } == first_web_profiles
    assert [
        (zone.get("group"), zone.get("member"), zone.get("web_member"), zone.get("combo"))
        for zone in window.design_envelope_actions.get("doubled_chord_zones", [])
    ] == first_doubled_zones
    assert np.allclose(window.displacements, first_displacements, rtol=1e-10, atol=1e-10)
    assert set(first_web_profiles.values()) == {analysis_web_profile.name}

    crane_report = window.crane_beam_results_box.toPlainText()
    assert "Mv*" in crane_report
    assert "Preliminary section capacity" in crane_report
    assert window.crane_beam_span_input.text() == window.load_bay_size_input.text()
    assert window.crane_beam_rail_profile_combo.currentText() == "12.5 kg/m rail"
    assert "utilization" in window.crane_beam_capacity_status_label.text()
    assert "utilization" in window.crane_beam_deflection_status_label.text()
    window.auto_select_crane_beam()
    crane_report = window.crane_beam_results_box.toPlainText()
    assert "Auto selection" in crane_report
    assert "Lightest passing" in crane_report
    assert math.isclose(float(window.crane_beam_load_height_input.text()), 0.12, rel_tol=1e-9)
    assert window.collect_preset_data()["crane_beam"]["load_height_mm"] == "120"
    assert all(combo.currentText() == "Monument" for combo in [
        window.roof_finish_combo, window.wall_finish_combo, window.barge_finish_combo,
        window.ridge_finish_combo, window.corner_trim_finish_combo, window.gutter_finish_combo,
    ])
    assert window.skylight_run_combo.isHidden()
    window.skylight_count_combo.setCurrentText("Two per bay")
    window.skylight_run_combo.setCurrentText("Half run")
    assert not window.skylight_run_combo.isHidden()
    concept_data = window.concept_3d_data()
    assert concept_data["finishes"]["cladding_back"] == "Surfmist"
    assert concept_data["finishes"]["show_slab"] is True
    assert window.collect_preset_data()["finishes"]["show_slab"] is True
    concept_mesh = window.concept_3d_dae(concept_data, return_mesh=True)
    assert len(concept_mesh["vertices"]) < 400000
    mesh_materials = {material for material, _ in concept_mesh["triangles"]}
    assert {"roof_cladding", "wall_cladding", "cladding_back", "skylight", "gutter", "barge", "ridge_capping", "corner_trim"} <= mesh_materials
    window.concept_3d_viewer.set_data(concept_data, concept_mesh)
    assert len(window.concept_3d_viewer.transparent_triangle_data) > 0
    slab_on_triangle_count = len(window.concept_3d_viewer.triangle_data)
    window.show_slab_checkbox.setChecked(False)
    slabless_data = window.concept_3d_data()
    assert slabless_data["finishes"]["show_slab"] is False
    window.concept_3d_viewer.set_data(slabless_data, concept_mesh)
    assert len(window.concept_3d_viewer.triangle_data) < slab_on_triangle_count
    window.show_slab_checkbox.setChecked(True)
    window.concept_3d_viewer.set_data(concept_data, concept_mesh)
    concept_dae = window.concept_3d_dae(concept_data)
    assert "<COLLADA" in concept_dae
    assert "<triangles" in concept_dae
    assert 'name="Transparent Skylight"' in concept_dae
    window.skylight_count_combo.setCurrentText("None")
    assert window.skylight_run_combo.isHidden()
    assert any((line.get("profile") or {}).get("name") for line in concept_data["frame_lines"])
    from elevation_drawings import _elevation_titles, _roof_cladding_edges, export_elevation_pdf
    assert _elevation_titles({"north_direction_in_section": "E"}) == {
        "left": "SOUTH ELEVATION",
        "right": "NORTH ELEVATION",
        "front": "EAST ELEVATION",
        "back": "WEST ELEVATION",
    }
    cladding_edges = _roof_cladding_edges(
        {"roof_cladding_offset_m": 0.35, "roof_cladding_depth_m": 0.03, "roof_edge_overhang_m": 0.0},
        [(0.0, 0.0), (1.0, 0.0)],
    )
    assert math.isclose(cladding_edges[0][2][1] - cladding_edges[0][0][1], 0.03, abs_tol=1e-9)
    elevation_path = os.path.join(tempfile.gettempdir(), "portalcalc-smoke-elevations.pdf")
    export_elevation_pdf(concept_data, elevation_path, {"project": "SMOKE TEST", "job_number": "TEST"})
    assert os.path.getsize(elevation_path) > 1000
    from PySide6.QtPdf import QPdfDocument
    drawing_document = QPdfDocument()
    assert drawing_document.load(elevation_path) == QPdfDocument.Error.None_
    assert drawing_document.pageCount() == 4
    ub_member_data = {
        "title": "UB member export smoke",
        "length_m": 1.0,
        "z_positions": [0.0],
        "nodes": [{"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 3.0}],
        "frame_lines": [{
            "a": 0,
            "b": 1,
            "group": "LEFT_COLUMN",
            "profile": {
                "name": "250 UB smoke",
                "group_name": "UB",
                "shape_type": "Universal Beam",
                "depth_mm": 250.0,
                "flange_width_mm": 146.0,
                "flange_thickness_mm": 10.9,
                "web_thickness_mm": 6.1,
            },
        }],
        "longitudinal_nodes": [],
        "roof_indices": [],
        "main_top_indices": [],
        "wall_states": {},
    }
    ub_dae = window.concept_3d_dae(ub_member_data)
    assert '<triangles material="steel-material" count="36">' in ub_dae
    chs_member_data = copy.deepcopy(ub_member_data)
    chs_member_data["title"] = "CHS member export smoke"
    chs_member_data["frame_lines"][0]["profile"] = {
        "name": chs_114.name,
        "group_name": chs_114.group_name,
        "shape_type": chs_114.shape_type,
        "depth_mm": chs_114.depth,
        "flange_width_mm": chs_114.flange_width,
        "flange_thickness_mm": chs_114.flange_thickness,
        "web_thickness_mm": chs_114.web_thickness,
    }
    chs_dae = window.concept_3d_dae(chs_member_data)
    assert '<triangles material="steel-material" count="128">' in chs_dae
    assert window.bottom_profile_combo is window.top_profile_combo
    assert window.left_canopy_bottom_profile_combo is window.left_canopy_top_profile_combo
    assert window.right_canopy_bottom_profile_combo is window.right_canopy_top_profile_combo
    factory_defaults = json.loads((Path(__file__).resolve().parent / "presets" / "factory_defaults.json").read_text(encoding="utf-8"))
    factory_g_load = factory_defaults["loads"]["g_kpa"]
    assert window.frame_system_combo.currentText() == "Truss"
    assert window.g_load_input.text() == factory_g_load
    assert window.downward_roof_load_from_input(window.g_load_input) == -float(factory_g_load)
    assert isinstance(window.sections_tab, QScrollArea)
    assert window.left_canopy_top_profile_combo.currentText().startswith("65*2.5 SHS")
    assert window.right_canopy_top_profile_combo.currentText().startswith("65*2.5 SHS")
    assert window.left_lean_column_profile_combo.currentText().startswith("250 UB 25.7")
    assert window.right_lean_column_profile_combo.currentText().startswith("250 UB 25.7")
    assert " kg/m)" in window.left_column_profile_combo.currentText()
    assert window.selected_profile(window.left_column_profile_combo) is not None
    selected_cap_label = window.crane_beam_pfc_cap_combo.currentText()
    selected_cap_name = main.clean_profile_label(selected_cap_label)
    assert selected_cap_name in main.CRANE_PFC_CAP_PROFILES
    if main.CRANE_PFC_CAP_PROFILES[selected_cap_name] is None:
        assert selected_cap_label == "No PFC cap"
    else:
        assert " kg/m)" in selected_cap_label
    assert window.roof_shape_combo.currentText() == "Gable"
    window.roof_shape_combo.setCurrentText("Skillion")
    window.generate_and_solve()
    skillion_top_nodes = window.concept_main_top_nodes
    assert skillion_top_nodes[-1].y > skillion_top_nodes[0].y
    assert "Roof shape: Skillion" in window.results_box.toPlainText()
    assert window.wind_inputs_from_ui(float(window.span_input.text()) * 1000.0, float(window.pitch_input.text()), float(window.eave_input.text()) * 1000.0).roof_shape == "Skillion"
    assert window.purlin_layout is not None
    assert len(window.purlin_layout.points) >= 2
    window.roof_shape_combo.setCurrentText("Gable")
    window.generate_and_solve()
    assert "RL1218" in window.storage_panel_mesh_input.text()
    assert all(
        any(token in window.storage_rail_profile_combo.itemText(index).upper() for token in [" UB ", "UB", "PFC", "CHANNEL"])
        and "SHS" not in window.storage_rail_profile_combo.itemText(index).upper()
        and "RHS" not in window.storage_rail_profile_combo.itemText(index).upper()
        for index in range(window.storage_rail_profile_combo.count())
    )
    window.storage_enabled_checkbox.setChecked(True)
    window.calculate_storage_load_report()
    storage_text = window.storage_results_box.toPlainText()
    assert "STORAGE HORIZONTAL RAIL" in storage_text
    assert "Deflection" in storage_text
    assert "STORAGE PANEL PRELIMINARY REO" in storage_text
    assert "Selected thickness" in storage_text
    assert "Service deflection" in storage_text
    assert "+M face" in storage_text
    assert "-M face" in storage_text
    assert "Crack control gates" in storage_text
    assert "w=" in storage_text
    assert "SL102" in storage_text
    window.storage_reverse_direction_checkbox.setChecked(True)
    window.calculate_storage_load_report()
    assert "Direction       : inward" in window.storage_results_box.toPlainText()
    window.generate_and_solve()
    assert math.isclose(window.eave_deflection_divisor(), 100.0, rel_tol=1e-9)
    assert "Column top deflection SLS" in window.section_checks_box.toPlainText()
    assert "H/100" in window.section_checks_box.toPlainText()
    storage_rail_deflection = window.design_envelope_actions["sls_column_midspan_ws_deflection"]
    assert storage_rail_deflection["value"] < 0.0
    assert "Storage rail connection deflection Ws" not in window.section_checks_box.toPlainText()
    assert "rail Ws" not in window.column_deflection_check_label.text()
    window.eave_deflection_limit_override = 125.0
    window.refresh_settings_menu_checks()
    window.generate_and_solve()
    assert "H/125" in window.section_checks_box.toPlainText()
    assert window.collect_preset_data()["display"]["eave_deflection_limit_override"] == 125.0
    window.eave_deflection_limit_override = None
    window.refresh_settings_menu_checks()
    assert window.member_check_restraints["LEFT_COLUMN"]["storage_restraint"]
    assert window.member_check_restraints["RIGHT_COLUMN"]["storage_restraint"]
    assert "storage rail restraint" in window.section_checks_box.toPlainText()
    window.storage_reverse_direction_checkbox.setChecked(False)
    window.storage_load_path_combo.setCurrentText("Side-running panels with centre stub")
    window.calculate_storage_load_report()
    stub_storage_text = window.storage_results_box.toPlainText()
    assert "Side-running panels with centre stub" in stub_storage_text
    assert "Stub mode" in stub_storage_text
    assert "Design point load P*" in stub_storage_text
    assert "STORAGE VERTICAL STUB" in stub_storage_text
    assert "Stub is checked as simply supported" in stub_storage_text
    assert window.storage_stub_profile_combo.count() > 0
    assert all("UB" in window.storage_stub_profile_combo.itemText(index).upper() for index in range(window.storage_stub_profile_combo.count()))
    assert window.storage_stub_capacity_status_label.text() != "-"
    assert window.storage_stub_deflection_status_label.text() != "-"
    window.storage_panel_support_combo.setCurrentText("Continuous over vertical stub")
    window.calculate_storage_load_report()
    continuous_stub_text = window.storage_results_box.toPlainText()
    assert "Panel support" in continuous_stub_text
    assert "Continuous over vertical stub" in continuous_stub_text
    assert "Panel -M face" in continuous_stub_text
    window.storage_panel_crack_min_thickness_input.setText("175Nah nah ")
    window.generate_and_solve()
    assert "Panel crack minimum thickness" in window.results_box.toPlainText()
    assert "175Nah nah" in window.results_box.toPlainText()
    window.storage_panel_crack_min_thickness_input.setText("100")
    window.storage_load_path_combo.setCurrentText("Wall panels to horizontal rail")
    window.storage_panel_support_combo.setCurrentText("Break at vertical stub")
    window.auto_select_storage_rail()
    assert "OK" in window.storage_rail_capacity_status_label.text()
    assert "OK" in window.storage_rail_deflection_status_label.text()
    assert "Auto rail selection" in window.storage_results_box.toPlainText()
    assert "SHS" not in window.storage_rail_profile_combo.currentText().upper()
    window.storage_load_path_combo.setCurrentText("Side-running panels with centre stub")
    window.auto_select_storage_rail()
    assert "Auto stub selection" in window.storage_results_box.toPlainText()
    assert "OK" in window.storage_stub_capacity_status_label.text()
    assert "OK" in window.storage_stub_deflection_status_label.text()
    window.storage_front_wall_checkbox.setChecked(True)
    window.storage_back_wall_checkbox.setChecked(True)
    window.generate_and_solve()
    storage_concept = window.concept_3d_data()
    panel_height = storage_concept["storage"]["panel_height_m"]
    assert storage_concept["storage"]["enabled"]
    assert storage_concept["storage"]["centre_stub"]
    assert storage_concept["storage"]["front_wall"]
    assert storage_concept["storage"]["back_wall"]
    assert math.isclose(storage_concept["cladding_back_offset_m"], 0.006, abs_tol=1e-9)
    assert all(
        segment["y1"] >= panel_height - 1e-9
        for segment in storage_concept["wall_segments"]
        if segment["label"] in {"Left wall", "Right wall"}
    )
    assert all(
        girt["y"] >= panel_height - 1e-9
        for girt in storage_concept["wall_girts"]
        if girt["label"] in {"Left wall", "Right wall"}
    )
    storage_end_surfaces = [
        surface for surface in storage_concept["end_wall_surfaces"]
        if surface["label"].endswith(("front", "back"))
    ]
    assert storage_end_surfaces
    assert all(math.isclose(surface["points"][0]["y"], panel_height, abs_tol=1e-9) for surface in storage_end_surfaces)
    eave_purlins = [purlin for purlin in storage_concept["roof_purlins"] if purlin.get("eave_drop_m")]
    assert len(eave_purlins) >= 2
    assert all(math.isclose(purlin["eave_drop_m"], 0.035, abs_tol=1e-9) for purlin in eave_purlins)
    assert all("normal_x" in purlin and "normal_y" in purlin for purlin in eave_purlins)
    selected_end_mullion = (window.end_wall_mullion_design.get("selected") or {}).get("section")
    assert storage_concept["end_mullion_profile"]["name"] == (selected_end_mullion or "200 UB 22.3")
    assert math.isclose(
        storage_concept["end_mullion_target_spacing_m"],
        window.end_wall_mullion_design["target_spacing_mm"] / 1000.0,
        rel_tol=1e-9,
    )
    assert math.isclose(
        storage_concept["end_girt_spacing_m"],
        (window.end_wall_girt_check or {}).get("spacing_mm", 1500.0) / 1000.0,
        rel_tol=1e-9,
    )
    storage_mesh = window.concept_3d_dae(storage_concept, return_mesh=True)
    storage_materials = {material for material, _ in storage_mesh["triangles"]}
    assert {"storage_panel", "galvanised"} <= storage_materials
    window.add_sliding_door()
    window.sliding_door_wall_combo.setCurrentText("Left wall")
    window.sliding_door_height_input.setText("5.2")
    window.sliding_door_width_input.setText("6.4")
    window.sliding_door_position_input.setText("3.0")
    window.sliding_door_opening_combo.setCurrentText("Slide right")
    window.sliding_door_tracks_combo.setCurrentText("Double")
    window.sliding_door_count_input.setValue(4)
    window.update_sliding_door_from_controls()
    door_concept = window.concept_3d_data()
    assert len(door_concept["sliding_doors"]) == 1
    assert door_concept["sliding_doors"][0]["tracks"] == 2
    assert door_concept["sliding_doors"][0]["door_count"] == 4
    assert door_concept["sliding_door_vertical_profile"]["depth_mm"] == 150.0
    assert door_concept["sliding_door_header_profile"]["name"] == "200 UB 22.3"
    window.add_pa_door()
    window.pa_door_wall_combo.setCurrentText("Right wall")
    window.pa_door_height_input.setText("2.4")
    window.pa_door_width_input.setText("1.0")
    window.pa_door_position_input.setText("4.0")
    window.pa_door_opening_combo.setCurrentText("Open right")
    window.update_pa_door_from_controls()
    door_concept = window.concept_3d_data()
    assert len(door_concept["pa_doors"]) == 1
    assert door_concept["pa_doors"][0]["wall"] == "Right wall"
    assert door_concept["pa_doors"][0]["opening"] == "Open right"
    assert window.collect_preset_data()["pa_doors"][0]["width_m"] == 1.0
    door_mesh = window.concept_3d_dae(door_concept, return_mesh=True)
    assert len(door_mesh["triangles"]) > len(storage_mesh["triangles"])
    saved_doors = copy.deepcopy(window.collect_preset_data()["sliding_doors"])
    window.set_sliding_doors([{
        "wall": "Front wall",
        "height_m": 4.8,
        "width_m": 5.5,
        "position_m": 2.5,
        "opening": "Bi-parting",
        "tracks": 2,
        "door_count": 4,
    }])
    front_door_mesh = window.concept_3d_dae(window.concept_3d_data(), return_mesh=True)
    assert len(front_door_mesh["triangles"]) > len(storage_mesh["triangles"])
    window.set_sliding_doors([{
        "wall": "Back wall",
        "height_m": 4.8,
        "width_m": 5.5,
        "position_m": 2.5,
        "opening": "Slide left",
        "tracks": 1,
        "door_count": 2,
    }])
    back_door_mesh = window.concept_3d_dae(window.concept_3d_data(), return_mesh=True)
    assert len(back_door_mesh["triangles"]) > len(storage_mesh["triangles"])
    window.set_sliding_doors([])
    assert not window.sliding_doors
    window.set_sliding_doors(saved_doors)
    assert window.sliding_doors[0]["opening"] == "Slide right"
    assert window.sliding_doors[0]["door_count"] == 4
    window.remove_sliding_door()
    window.remove_pa_door()
    window.storage_enabled_checkbox.setChecked(False)
    window.calculate_storage_load_report()
    window.left_column_length_adjust_input.setText("0.5")
    window.right_column_length_adjust_input.setText("-0.25")
    window.storage_enabled_checkbox.setChecked(True)
    window.generate_and_solve()
    assert math.isclose(window.base_nodes[0].y, -500.0, abs_tol=1e-9)
    assert math.isclose(window.base_nodes[1].y, 250.0, abs_tol=1e-9)
    left_storage_node = next(
        node for node in window.structure.nodes
        if math.isclose(node.x, window.base_nodes[0].x, abs_tol=1e-9)
        and math.isclose(node.y, window.base_nodes[0].y + float(window.storage_rail_height_input.text()) * 1000.0, abs_tol=1e-9)
    )
    right_storage_node = next(
        node for node in window.structure.nodes
        if math.isclose(node.x, window.base_nodes[1].x, abs_tol=1e-9)
        and math.isclose(node.y, window.base_nodes[1].y + float(window.storage_rail_height_input.text()) * 1000.0, abs_tol=1e-9)
    )
    assert left_storage_node is not None
    assert right_storage_node is not None
    assert window.member_check_restraints["LEFT_COLUMN"]["storage_restraint"]
    assert window.member_check_restraints["RIGHT_COLUMN"]["storage_restraint"]
    window.storage_enabled_checkbox.setChecked(False)
    window.left_column_length_adjust_input.setText("0")
    window.right_column_length_adjust_input.setText("0")
    window.generate_and_solve()
    assert window.downward_roof_load_text("-0.25") == "0.25"
    assert window.downward_roof_load_text("0.15") == "0.15"
    assert window.snow_ahd_height_input.isHidden()
    assert window.internal_pressure_combo.isHidden()
    window.wind_region_combo.setCurrentText("C")
    assert not window.internal_pressure_combo.isHidden()
    window.wind_region_combo.setCurrentText("A5")
    assert window.internal_pressure_combo.isHidden()
    window.wind_orientation_selector.setCurrentText("E")
    assert window.wind_source_direction_for_case("E", "Right +") == "N"
    assert window.wind_source_direction_for_case("E", "Left +") == "S"
    assert window.wind_source_direction_for_case("N", "Right +") == "E"
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
    window.span_input.setText("28.5")
    window.update_truss_depth_from_span()
    assert window.depth_input.text() == "950"
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
    active_auto_specs = window.active_auto_size_group_specs()
    assert not window.auto_size_design_acceptable([], 0.0)
    assert not window.save_optimisation_learning([], 0.0)
    original_learning_path = window.optimisation_learning_path
    with tempfile.TemporaryDirectory() as learning_dir:
        learning_path = Path(learning_dir) / "optimisation_learning.json"
        window.optimisation_learning_path = lambda: str(learning_path)
        assert window.save_optimisation_learning(active_auto_specs, 0.95)
        learning_prediction = window.optimisation_surrogate_prediction()
        assert learning_prediction["sample_count"] == 1
        assert math.isclose(learning_prediction["nearest_distance"], 0.0, abs_tol=1e-12)
        assert learning_prediction["section_metrics"]
        assert not learning_prediction["nearest_trusted_for_shortcut"]
        assert window.save_optimisation_learning(active_auto_specs, 0.95)
        confirmed_prediction = window.optimisation_surrogate_prediction()
        assert not confirmed_prediction["nearest_trusted_for_shortcut"]
        stored_learning = json.loads(learning_path.read_text(encoding="utf-8"))["records"][0]
        assert stored_learning["quality"] == "verified_feasible_incumbent"
        assert stored_learning["confirmation_count"] == 2
        assert not stored_learning["trusted_for_shortcut"]
    window.optimisation_learning_path = original_learning_path

    with tempfile.TemporaryDirectory() as learning_dir:
        learning_path = Path(learning_dir) / "optimisation_learning.json"
        matrix_path = Path(learning_dir) / "matrix.xls"
        window.optimisation_learning_path = lambda: str(learning_path)
        matrix_headers = [
            "Inactive", "Job Number", "Project", "Job name", "Product Type",
            "Length", "Width", "Eave Height", "Roof Type & Pitch", "Bays",
            "Wind Region", "Wall Configuration", "Building Importance Level",
            "Canopy", "Canopy Rafter Type", "Lean-to", "Lean-to Rafter Type",
            "Lean-to Column Member", "Truss Depth", "Chord Members", "Double Chords",
            "Web Members", "Haunch Web Members", "Haunch Webs Qty",
            "Main Column Member UB/RHS/SHS",
        ]
        matrix_values = [
            "No", "SMOKE-SEED-1", "Smoke Matrix Seed", "", "AG - Test",
            "40", window.span_input.text(), window.eave_input.text(),
            f"Gable {window.pitch_input.text()}", f"5*{float(window.load_bay_size_input.text()) * 1000:.0f}",
            f"Wind Region {window.wind_region_combo.currentText()}", "Roof Only",
            f"Importance Level {window.importance_combo.currentText()}",
            "", "", "", "", "", window.depth_input.text(),
            window.selected_profile(window.top_profile_combo).name, "",
            window.selected_profile(window.web_profile_combo).name, "", "0",
            window.selected_profile(window.left_column_profile_combo).name,
        ]

        def xml_row(values):
            cells = "".join(
                f"<Cell><Data ss:Type=\"String\">{html.escape(str(value))}</Data></Cell>"
                for value in values
            )
            return f"<Row>{cells}</Row>"

        matrix_path.write_text(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<Workbook xmlns=\"urn:schemas-microsoft-com:office:spreadsheet\" "
            "xmlns:ss=\"urn:schemas-microsoft-com:office:spreadsheet\">"
            "<Worksheet ss:Name=\"EngineeringMatrixList\"><Table>"
            f"{xml_row(matrix_headers)}{xml_row(matrix_values)}"
            "</Table></Worksheet></Workbook>",
            encoding="utf-8",
        )
        import_summary = window.import_optimiser_warm_starts(str(matrix_path))
        assert import_summary["imported"] == 1
        matrix_prediction = window.optimisation_surrogate_prediction()
        assert matrix_prediction["sample_count"] == 1
        assert matrix_prediction["historical_count"] == 1
        assert matrix_prediction["verified_count"] == 0
        assert not matrix_prediction["nearest_trusted_for_shortcut"]
        matrix_specs = window.active_auto_size_group_specs()
        window.apply_optimisation_warm_start(matrix_specs)
        matrix_sections = {group: combo.currentText() for _, combo, group in matrix_specs}
        assert "UC" not in matrix_sections["LEFT_COLUMN"].upper()
        assert "UB" in matrix_sections["LEFT_COLUMN"].upper()
        matrix_records = json.loads(learning_path.read_text(encoding="utf-8"))["records"]
        assert matrix_records[0]["quality"] == "historical_matrix_seed"
        assert not matrix_records[0]["verified"]
        assert matrix_records[0]["warm_start_eligible"]
    window.optimisation_learning_path = original_learning_path
    window.left_canopy_type_combo.setCurrentText("Canopy")
    window.left_canopy_length_input.setText("0")
    assert window.optimisation_job_features()["left_type"] == "None"
    window.left_canopy_type_combo.setCurrentText("None")
    original_wall_states = window.envelope_plan_selector.wall_states()
    window.envelope_plan_selector.set_wall_states({"left": True, "right": False, "front": False, "back": False})
    assert not window.symmetric_end_columns_should_match()
    features = window.optimisation_job_features()
    assert features["left_wall_clad"] is True
    assert features["right_wall_clad"] is False
    window.envelope_plan_selector.set_wall_states(original_wall_states)

    for chord_group, chord_combo in [("TOP", window.top_profile_combo), ("BOTTOM", window.bottom_profile_combo)]:
        chord_envelope = window.design_envelope_actions.get(chord_group, {})
        doubled_zone = chord_envelope.get("doubled_chord_zone")
        if doubled_zone and doubled_zone.get("doubled_envelope"):
            chord_profile = window.selected_profile(chord_combo)
            expected_doubled = window.member_check_result(
                chord_profile,
                doubled_zone["doubled_envelope"],
                chord_group,
            ).governing_ratio
            doubled_ratio, doubled_label = window.doubled_chord_zone_ratio(
                chord_profile,
                chord_envelope,
                chord_group,
            )
            overall_ratio, _, _ = window.member_and_connection_ratio(
                chord_profile,
                chord_envelope,
                chord_group,
            )
            assert math.isclose(doubled_ratio, expected_doubled, rel_tol=1e-9)
            assert doubled_label == "Doubled chord zone"
            assert overall_ratio + 1e-9 >= doubled_ratio
    frozen_ratio_cache = {}
    coordinated_choices, _ = window.auto_size_coordinated_choices(active_auto_specs, frozen_ratio_cache)
    assert coordinated_choices
    assert len({id(combo) for combo, _ in coordinated_choices}) == len(coordinated_choices)
    assert frozen_ratio_cache
    exact_neighbour = window.auto_size_exact_neighbour_candidate(active_auto_specs, frozen_ratio_cache)
    if exact_neighbour is not None:
        frozen_ratio, _, _, neighbour_combo, neighbour_index, _, estimated_saving, _ = exact_neighbour
        neighbour_profile = window.catalog.get(neighbour_combo.itemText(neighbour_index))
        assert frozen_ratio <= 1.35
        assert estimated_saving > 0.0
        assert window.auto_size_profile_cost_key(neighbour_profile) < window.auto_size_profile_cost_key(window.selected_profile(neighbour_combo))
    ub_250_37 = next(profile for profile in window.catalog.all_profiles() if profile.name == "250 UB 37.3")
    ub_310_32 = next(profile for profile in window.catalog.all_profiles() if profile.name == "310 UB 32.0")
    assert ub_310_32.A < ub_250_37.A
    assert ub_310_32.Izp > ub_250_37.Izp
    assert ub_310_32.Iyp < ub_250_37.Iyp
    saved_top_index = window.top_profile_combo.currentIndex()
    saved_auto_state = {
        "depth": window.depth_input.text(),
        "combos": [(window.top_profile_combo, saved_top_index)],
        "web_assignments": copy.deepcopy(window.auto_web_profile_assignments),
    }
    window.top_profile_combo.setCurrentIndex(top_ladder[0])
    window.auto_size_restore_state(saved_auto_state)
    assert window.top_profile_combo.currentIndex() == saved_top_index
    top_chord_depth = window.profile_depth_mm(window.selected_profile(window.top_profile_combo))
    bottom_chord_depth = window.profile_depth_mm(window.selected_profile(window.bottom_profile_combo))
    expected_truss_centreline_depth = float(window.depth_input.text()) - 0.5 * top_chord_depth - 0.5 * bottom_chord_depth
    main_top_nodes = window.concept_main_top_nodes
    bottom_chord_nodes = {
        node
        for element in window.structure.elements
        if element.group == "BOTTOM"
        for node in (element.start, element.end)
    }
    for top_node in (main_top_nodes[0], main_top_nodes[-1]):
        bottom_node = min(bottom_chord_nodes, key=lambda node: abs(node.x - top_node.x) + abs((top_node.y - expected_truss_centreline_depth) - node.y))
        assert math.isclose(top_node.y - bottom_node.y, expected_truss_centreline_depth, abs_tol=1e-6)
    assert "outside-to-outside" in window.results_box.toPlainText()
    assert "chord centreline depth" in window.results_box.toPlainText()
    assert window.member_check_restraints["BOTTOM"]["minor_mm"] == 2.0 * window.member_check_restraints["TOP"]["minor_mm"]
    assert "bottom minor length defaults to every second girt/flybrace" in window.section_checks_box.toPlainText()
    assert "automatic every-purlin retry if required" in window.section_checks_box.toPlainText()
    assert "Doubled chord zones:" in window.results_box.toPlainText()
    assert "chord-member or chord-local web-joint utilisation" in window.results_box.toPlainText()
    assert "Canopy/lean-to truss chord envelopes ignore end panels" in window.results_box.toPlainText()
    assert "BOTTOM CHORD - DESIGN ENVELOPE" in window.section_checks_box.toPlainText()
    assert "Midspan sag G" in window.section_checks_box.toPlainText()
    assert "Midspan sag Q" in window.section_checks_box.toPlainText()
    assert "Combined midspan sag G+Q" in window.section_checks_box.toPlainText()
    assert "Midspan wind deflection Ws" in window.section_checks_box.toPlainText()
    truss_deflection_ratios = window.truss_midspan_deflection_ratios()
    truss_deflection_labels = {label for _, label, _ in truss_deflection_ratios}
    assert truss_deflection_labels == {"Midspan Q deflection", "Midspan Ws deflection"}
    assert "G excluded from governing sizing" in window.section_checks_box.toPlainText()
    forced_g_advisory = window.midspan_component_deflection_check("G", 42.0, 6000.0, 200.0)
    assert forced_g_advisory["status"] == "PRECAMBER REQUIRED"
    assert forced_g_advisory["governing"] is False
    assert math.isclose(forced_g_advisory["precamber_required_mm"], 12.0, rel_tol=1e-9)
    forced_q_failure = window.midspan_component_deflection_check("Q", 42.0, 6000.0, 200.0)
    assert forced_q_failure["status"] == "NOT OK"
    assert forced_q_failure["governing"] is True
    assert "Column midspan deflection Ws" in window.section_checks_box.toPlainText()
    assert set(window.design_envelope_actions["sls_column_top_deflections"]) == {"LEFT_COLUMN", "RIGHT_COLUMN"}
    assert all(
        item["value"] >= 0.0
        for item in window.design_envelope_actions["sls_column_top_deflections"].values()
    )
    assert "H/80" in window.section_checks_box.toPlainText()
    assert "Wu Cpe Left +" in window.single_load_arrows
    assert "Wu Cpi + with Left +" in window.single_load_arrows
    assert "Ws Cpi - with End -" in window.single_load_arrows
    assert "Wu Left +" not in window.single_load_arrows
    assert window.normalized_single_load_label("Cpi - with Left +") == "Wu Cpi - with Left +"
    assert not hasattr(window, "frame_type_combo")

    saved_preset_data = json.loads(json.dumps(window.collect_preset_data()))
    window.apply_preset_data({
        "wind": {
            "frame_type": "Roof Only",
            "wall_enclosure": {"left": True, "right": True, "front": False, "back": True},
        }
    })
    assert window.envelope_plan_selector.wall_states() == {"left": True, "right": True, "front": False, "back": True}
    assert window.derived_frame_type_label.text() == "3 Sided"
    window.apply_preset_data(saved_preset_data)

    concept_data = window.concept_3d_data()
    concept_html = window.concept_3d_html(concept_data)
    assert concept_data["bay_count"] >= 1
    assert concept_data["frame_lines"]
    assert "Drag to orbit" in concept_html
    assert "const data =" in concept_html
    assert window.wind_reduction_input.parent() is None
    assert window.eave_x_spring_input.isHidden()
    assert window.base_x_spring_input.isHidden()
    assert window.end_wall_brace_dia_input.isHidden()
    assert window.end_wall_brace_frame_offset_input.isHidden()
    assert window.end_wall_braced_sides_combo.isHidden()
    assert window.end_wall_brace_size_combo.isHidden()
    assert not window.end_wall_braced_bay_count_input.isHidden()
    assert window.bracing_tab.isAncestorOf(window.longitudinal_bracing_status_label)
    assert "Disabled" in window.longitudinal_bracing_status_label.text()
    assert window.end_wall_brace_size_combo.itemText(0).startswith("16 DIA ROD")
    assert not any(
        window.end_wall_brace_size_combo.itemText(index).startswith("12 DIA ROD")
        for index in range(window.end_wall_brace_size_combo.count())
    )
    assert " kg/m)" in window.end_wall_brace_size_combo.itemText(0)
    brace_items = [window.end_wall_brace_size_combo.itemText(index) for index in range(window.end_wall_brace_size_combo.count())]
    assert any(item.startswith("EA 75x75x8") for item in brace_items)
    assert any(item.startswith("EA 100x100x10") for item in brace_items)
    assert not any(item.startswith("EA 50x50x5") for item in brace_items)
    window.eave_x_restraint_combo.setCurrentText("One end")
    assert window.eave_x_spring_input.isHidden()
    assert not window.end_wall_braced_sides_combo.isHidden()
    assert not window.end_wall_brace_size_combo.isHidden()
    window.eave_x_restraint_combo.setCurrentText("Both ends")
    assert not window.end_wall_brace_size_combo.isHidden()
    window.eave_x_restraint_combo.setCurrentText("None")
    assert window.end_wall_braced_sides_combo.isHidden()
    assert window.end_wall_brace_size_combo.isHidden()
    # Preset application blocks combo-box signals while restoring values, so
    # it must explicitly refresh the dependent bracing rows afterwards.
    unbraced_preset = window.collect_preset_data()
    braced_preset = copy.deepcopy(unbraced_preset)
    braced_preset["geometry"]["end_wall_bracing"] = "Both ends"
    braced_preset["geometry"]["end_wall_braced_sides"] = "Both sides"
    braced_preset["bracing"]["enabled"] = True
    braced_preset["bracing"]["braced_bays_per_side"] = 2
    window.apply_preset_data(braced_preset)
    assert window.eave_x_restraint_combo.currentText() == "Both ends"
    assert not window.end_wall_braced_sides_combo.isHidden()
    assert not window.end_wall_brace_size_combo.isHidden()
    assert window.end_wall_braced_bay_count_input.value() == 2
    window.apply_preset_data(unbraced_preset)
    assert window.eave_x_restraint_combo.currentText() == "None"
    assert window.end_wall_braced_sides_combo.isHidden()
    assert window.end_wall_brace_size_combo.isHidden()
    assert not window.end_wall_braced_bay_count_input.isHidden()
    window.left_support_combo.setCurrentText("Spring")
    window.right_support_combo.setCurrentText("Spring")
    assert not window.base_x_spring_input.isHidden()
    window.base_x_spring_input.setText("2.5")
    assert window.collect_preset_data()["geometry"]["base_moment_spring_knm_rad"] == "2.5"
    window.generate_and_solve()
    assert window.support_types[:2] == ["Spring", "Spring"]
    assert len(window.structure.springs) == 2
    assert all(abs(value - 2_500_000.0) < 1e-9 for value in window.structure.springs.values())
    assert abs(window.reactions[window.base_nodes[0].rz]) > 1e-9
    assert abs(window.reactions[window.base_nodes[1].rz]) > 1e-9
    assert "Base moment spring kR: 2.500 kNm/rad" in window.results_box.toPlainText()
    window.left_support_combo.setCurrentText("Pinned")
    window.right_support_combo.setCurrentText("Pinned")
    assert window.base_x_spring_input.isHidden()
    window.frame_system_combo.setCurrentText("Rafter")
    assert not window.rafter_haunch_length_input.isHidden()
    assert any("RHS" in window.top_profile_combo.itemText(index).upper() for index in range(window.top_profile_combo.count()))
    window.rafter_haunch_length_input.setText("3000")
    window.envelope_plan_selector.set_wall_states({"left": False, "right": False, "front": False, "back": False})
    window.set_combo_text_if_present(window.top_profile_combo, ub_chord_options[0])
    window.load_combination_combo.setCurrentText("1.2G + Wu (Left +, Cpi +)")
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
    assert "MAIN ROOF WIND LOADS" in window.results_box.toPlainText()
    assert "Main roof Cpi : not applied for AS/NZS B.5 free-roof net pressure" in window.results_box.toPlainText()
    assert window.single_load_arrows.get("Wu Cpi + with Left +", []) == []
    assert any(abs(arrow[2]) > 1e-9 for arrow in window.single_load_arrows["Wu Cpe Left +"])
    window.internal_column_count_combo.setCurrentText("2 columns")
    window.internal_column_offset_input.setText("3.3")
    window.generate_and_solve()
    rafter_endpoint_coords = {
        (round(node.x, 6), round(node.y, 6))
        for element in window.structure.elements
        if element.group == "TOP"
        for node in (element.start, element.end)
    }
    internal_columns = [element for element in window.structure.elements if element.group == "INTERNAL_COLUMN"]
    assert len(internal_columns) == 2
    for column in internal_columns:
        column_top = column.start if column.start.y > column.end.y else column.end
        assert (round(column_top.x, 6), round(column_top.y, 6)) in rafter_endpoint_coords
    assert not any(element.group in {"BOTTOM", "WEB", "POST", "APEX"} for element in window.structure.elements)
    window.internal_column_connection_combo.setCurrentText("Moment")
    assert window.collect_preset_data()["geometry"]["internal_column_connection"] == "Moment"
    window.generate_and_solve()
    rafter_endpoint_nodes = {
        node
        for element in window.structure.elements
        if element.group == "TOP"
        for node in (element.start, element.end)
    }
    for column in [element for element in window.structure.elements if element.group == "INTERNAL_COLUMN"]:
        column_top = column.start if column.start.y > column.end.y else column.end
        assert column_top in rafter_endpoint_nodes
    window.internal_column_count_combo.setCurrentText("None")
    window.internal_column_connection_combo.setCurrentText("Pinned")
    window.frame_system_combo.setCurrentText("Truss")
    assert window.rafter_haunch_length_input.isHidden()
    window.generate_and_solve()
    saved_envelope_method = window.member_check_envelope_lines
    saved_section_method = window.update_section_checks_text
    try:
        window.member_check_envelope_lines = lambda *args, **kwargs: ([], {"foundation_reactions": [], "doubled_chord_zones": []})
        window.update_section_checks_text = lambda *args, **kwargs: None
        window.internal_column_count_combo.setCurrentText("2 columns")
        window.internal_column_offset_input.setText("3.3")
        window.internal_column_connection_combo.setCurrentText("Moment")
        window.generate_and_solve()
        assert "error" not in window.results_box.toPlainText().lower()
        top_endpoint_nodes = {
            node
            for element in window.structure.elements
            if element.group == "TOP"
            for node in (element.start, element.end)
        }
        bottom_endpoint_nodes = {
            node
            for element in window.structure.elements
            if element.group == "BOTTOM"
            for node in (element.start, element.end)
        }
        internal_columns = [element for element in window.structure.elements if element.group == "INTERNAL_COLUMN"]
        assert len(internal_columns) == 4
        upper_columns = [element for element in internal_columns if element.start in bottom_endpoint_nodes and element.end in top_endpoint_nodes]
        assert len(upper_columns) == 2
        for column in upper_columns:
            assert abs(column.start.x - column.end.x) < 1e-6
            assert column.profile is window.selected_profile(window.internal_column_profile_combo)
            column_bottom_chord_joint = column.start
            column_top_chord_joint = column.end
            bottom_incident_webs = [
                element for element in window.structure.elements
                if element.group == "WEB"
                and (
                    (abs(element.start.x - column_bottom_chord_joint.x) < 1e-6 and abs(element.start.y - column_bottom_chord_joint.y) < 1e-6)
                    or (abs(element.end.x - column_bottom_chord_joint.x) < 1e-6 and abs(element.end.y - column_bottom_chord_joint.y) < 1e-6)
                )
            ]
            top_incident_webs = [
                element for element in window.structure.elements
                if element.group == "WEB"
                and (
                    (abs(element.start.x - column_top_chord_joint.x) < 1e-6 and abs(element.start.y - column_top_chord_joint.y) < 1e-6)
                    or (abs(element.end.x - column_top_chord_joint.x) < 1e-6 and abs(element.end.y - column_top_chord_joint.y) < 1e-6)
                )
            ]
            assert len(bottom_incident_webs) == 1
            assert len(top_incident_webs) == 1
            crossing_webs = [
                element for element in window.structure.elements
                if element.group == "WEB"
                and min(element.start.x, element.end.x) < column_bottom_chord_joint.x < max(element.start.x, element.end.x)
                and element not in bottom_incident_webs
                and element not in top_incident_webs
            ]
            assert not crossing_webs

        window.internal_column_count_combo.setCurrentText("1 column")
        window.internal_column_offset_input.setText("-2.0")
        window.internal_column_connection_combo.setCurrentText("Moment")
        window.generate_and_solve()
        assert "error" not in window.results_box.toPlainText().lower()
        centre_x = (window.base_nodes[0].x + window.base_nodes[1].x) / 2.0
        expected_x = centre_x - 2000.0
        top_endpoint_nodes = {
            node
            for element in window.structure.elements
            if element.group == "TOP"
            for node in (element.start, element.end)
        }
        bottom_endpoint_nodes = {
            node
            for element in window.structure.elements
            if element.group == "BOTTOM"
            for node in (element.start, element.end)
        }
        internal_columns = [element for element in window.structure.elements if element.group == "INTERNAL_COLUMN"]
        assert len(internal_columns) == 2
        assert all(abs(element.start.x - expected_x) < 1e-6 and abs(element.end.x - expected_x) < 1e-6 for element in internal_columns)
        upper_columns = [element for element in internal_columns if element.start in bottom_endpoint_nodes and element.end in top_endpoint_nodes]
        assert len(upper_columns) == 1

        window.set_combo_text_if_present(window.internal_column_count_combo, "1 central")
        assert window.internal_column_count_combo.currentText() == "1 column"
    finally:
        window.member_check_envelope_lines = saved_envelope_method
        window.update_section_checks_text = saved_section_method
        window.internal_column_count_combo.setCurrentText("None")
        window.internal_column_connection_combo.setCurrentText("Pinned")
        window.generate_and_solve()
    assert window.purlin_check is not None
    assert "PURLIN CHECK" in window.section_checks_box.toPlainText()
    assert window.purlin_check_label.text() != "Not checked"
    assert window.wall_girt_check is not None
    assert "WALL GIRT CHECK" in window.section_checks_box.toPlainText()
    assert window.wall_girt_check_label.text() != "Not checked"
    saved_end_wall_states = window.envelope_plan_selector.wall_states()
    if not (saved_end_wall_states.get("front") or saved_end_wall_states.get("back")):
        clad_end_states = dict(saved_end_wall_states)
        clad_end_states["front"] = True
        window.envelope_plan_selector.set_wall_states(clad_end_states)
        window.generate_and_solve()
    assert window.end_wall_mullion_design is not None
    assert window.end_wall_mullion_design["actual_spacing_mm"] <= 8000.0 + 1e-9
    assert window.end_wall_mullion_design["mullion_count"] == window.end_wall_mullion_design["spaces"] - 1
    assert window.end_wall_mullion_design["connection"] == "Pinned at roof and ground"
    assert window.end_wall_mullion_design["deflection_divisor"] == 150.0
    assert window.end_wall_girt_check is not None
    assert math.isclose(window.end_wall_girt_check["spacing_mm"], window.wall_girt_check["spacing_mm"], rel_tol=1e-9)
    assert "END WALL MULLION CHECK" in window.section_checks_box.toPlainText()
    assert "H/150" in window.section_checks_box.toPlainText()
    assert window.end_wall_mullion_check_label.text() != "Not checked"
    assert "automatic target" not in window.end_wall_layout_label.text().lower()
    assert window.sections_tab.isAncestorOf(window.end_wall_layout_label)
    assert window.sections_tab.isAncestorOf(window.end_wall_mullion_check_label)
    assert window.sections_tab.isAncestorOf(window.end_wall_girt_check_label)
    assert not hasattr(window, "end_wall_mullion_profile_combo")
    truss_limit_row, _ = window.main_geometry_layout.getWidgetPosition(window.max_truss_depth_input)
    mullion_override_row, _ = window.main_geometry_layout.getWidgetPosition(window.end_wall_mullion_spacing_override_input)
    assert mullion_override_row == truss_limit_row + 1
    original_end_spacing_override = window.end_wall_mullion_spacing_override_input.text()
    window.end_wall_mullion_spacing_override_input.setText("6.0")
    window.generate_and_solve()
    assert window.end_wall_mullion_design["spacing_override_mm"] == 6000.0
    assert window.end_wall_mullion_design["actual_spacing_mm"] <= 6000.0 + 1e-9
    window.end_wall_mullion_spacing_override_input.setText(original_end_spacing_override)
    window.envelope_plan_selector.set_wall_states(saved_end_wall_states)
    window.generate_and_solve()
    window.left_canopy_type_combo.setCurrentText("Canopy")
    window.left_canopy_length_input.setText("3")
    window.left_canopy_eave_height_input.setText("5")
    window.generate_and_solve()
    assert window.left_canopy_deflection_check_label in window.deflection_hover_overlays
    assert "Load case:" in window.left_canopy_deflection_check_label.toolTip()
    assert window.deflection_hover_overlays[window.left_canopy_deflection_check_label]["direction"] == "vertical"
    canopy_help_snapshot = window.assistant_help_snapshot(include_optimiser=False)
    canopy_help_base = window.assistant_capture_help_base_state(canopy_help_snapshot)
    window.assistant_help_session = {
        "snapshot": canopy_help_snapshot,
        "base": canopy_help_base,
        "trials": [],
    }
    canopy_depth_trial = window.assistant_execute_help_trial({
        "hypothesis": "Test a deeper left canopy truss",
        "truss_depth_delta_mm": 0,
        "left_addition_depth_delta_mm": 50,
        "right_addition_depth_delta_mm": 0,
        "left_column_steps": 0,
        "right_column_steps": 0,
        "top_member_steps": 0,
        "bottom_chord_steps": 0,
        "web_steps": 0,
        "panel_layout_steps": 0,
    })
    assert canopy_depth_trial["solved"] is True
    assert any(
        item.get("parameter") == "left_canopy_lean_to_depth_mm"
        for item in canopy_depth_trial["applied_changes"]
    )
    assert canopy_depth_trial["live_design_restored"] is True
    window.assistant_help_session = None
    assert "eave 5000.0 mm" in window.results_box.toPlainText()
    assert any(
        element.group == "LEFT_CANOPY_TOP"
        and abs(element.start.x - window.base_nodes[0].x) < 1e-6
        and abs(element.start.y - 5000.0) < 1e-6
        for element in window.structure.elements
    )
    window.left_canopy_frame_system_combo.setCurrentText("Rafter")
    window.set_default_combo(window.left_canopy_top_profile_combo, ["250 UB", "200 UB", "150 UB"])
    window.generate_and_solve()
    assert "Canopies: left 3000.0 mm (Canopy, Rafter" in window.results_box.toPlainText()
    assert "Canopy/lean-to rafter envelopes use the full rafter length" in window.results_box.toPlainText()
    assert "UB" in window.left_canopy_top_profile_combo.currentText().upper()
    assert not any(element.group in {"LEFT_CANOPY_BOTTOM", "LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"} for element in window.structure.elements)
    canopy_restraint = window.member_check_restraints["LEFT_CANOPY_TOP"]
    assert math.isclose(canopy_restraint["minor_mm"], window.member_check_restraints["TOP"]["minor_mm"], rel_tol=1e-9)
    assert math.isclose(canopy_restraint["major_mm"], 2.0 * window.member_check_restraints["TOP"]["minor_mm"], rel_tol=1e-9)
    assert "Left canopy tip:" in window.section_checks_box.toPlainText()
    assert "(L/100)" in window.section_checks_box.toPlainText()
    window.importance_combo.setCurrentText("2")
    window.generate_and_solve()
    assert "(L/180)" in window.section_checks_box.toPlainText()
    window.importance_combo.setCurrentText("1")
    window.ui_theme_combo.setCurrentText("Dark")
    assert app.styleSheet()
    window.ui_theme_combo.setCurrentText("Light")
    assert app.styleSheet() == ""
    window.left_canopy_frame_system_combo.setCurrentText("Truss")
    window.left_canopy_wind_model_combo.setCurrentText("AS/NZS B.5 free roof")
    window.left_canopy_underside_combo.setCurrentText("Empty under")
    window.load_combination_combo.setCurrentText("1.2G + Wu (Left +, Cpi +)")
    window.generate_and_solve()
    left_canopy_web_elements = [
        element for element in window.structure.elements
        if element.group == "LEFT_CANOPY_WEB"
    ]
    assert len(left_canopy_web_elements) % 2 == 0
    outer_web = min(
        left_canopy_web_elements,
        key=lambda element: min(element.start.x, element.end.x),
    )
    outer_node, inward_node = sorted([outer_web.start, outer_web.end], key=lambda node: node.x)
    assert inward_node.y > outer_node.y
    deep_web = max(
        left_canopy_web_elements,
        key=lambda element: max(element.start.x, element.end.x),
    )
    outward_node, deep_node = sorted([deep_web.start, deep_web.end], key=lambda node: node.x)
    assert outward_node.y > deep_node.y
    canopy_concept = window.concept_3d_data()
    canopy_junction = window.concept_main_top_nodes[0]
    assert not any(
        abs(float(purlin["x"]) * 1000.0 - canopy_junction.x) < 1e-3
        and abs(float(purlin["y"]) * 1000.0 - canopy_junction.y) < 1e-3
        for purlin in canopy_concept["roof_purlins"]
    )
    assert "wind model   : AS/NZS B.5 free roof, windward, empty under" in window.results_box.toPlainText()
    assert "Cpn net" in window.results_box.toPlainText()
    assert any(abs(arrow[2]) > 1e-9 for arrow in window.load_arrows)
    free_roof_canopy_top_elements = [
        element for element in window.structure.elements
        if element.group == "LEFT_CANOPY_TOP"
    ]
    free_roof_member_loads = [
        window.structure.uniform_load_for_element(element)
        for element in free_roof_canopy_top_elements
    ]
    assert any(abs(local_qy) > 1e-9 for _, local_qy in free_roof_member_loads)
    assert all(abs(local_qx) < 1e-9 for local_qx, _ in free_roof_member_loads)
    assert max(
        window.structure.element_force_summary(element, window.displacements)["moment_knm"]
        for element in free_roof_canopy_top_elements
    ) > 1e-6
    main_roof_fx = [
        arrow[2]
        for arrow in window.single_load_arrows["Wu Cpe Left +"]
        if 0.0 <= arrow[0] <= float(window.span_input.text()) * 1000.0
    ]
    assert any(abs(fx) > 1e-9 for fx in main_roof_fx)
    window.left_canopy_wind_model_combo.setCurrentText("Main roof Cpe + Cpi")
    window.left_canopy_type_combo.setCurrentText("Lean-to")
    window.left_lean_enclosure_selector.set_wall_states({"left": True, "right": True, "front": True, "back": True})
    window.load_combination_combo.setCurrentText("1.2G + Wu (Left +, Cpi +)")
    window.generate_and_solve()
    assert window.left_lean_deflection_check_label in window.deflection_hover_overlays
    assert "Load case:" in window.left_lean_deflection_check_label.toolTip()
    continuous_canopy_top_elements = [
        element for element in window.structure.elements
        if element.group == "LEFT_CANOPY_TOP"
    ]
    continuous_member_loads = [
        window.structure.uniform_load_for_element(element)
        for element in continuous_canopy_top_elements
    ]
    assert any(abs(local_qy) > 1e-9 for _, local_qy in continuous_member_loads)
    assert all(abs(local_qx) < 1e-9 for local_qx, _ in continuous_member_loads)
    assert max(
        window.structure.element_force_summary(element, window.displacements)["moment_knm"]
        for element in continuous_canopy_top_elements
    ) > 1e-6
    lean_text = window.results_box.toPlainText()
    assert "enclosed chamber Cpi" in lean_text
    assert "lean-to walls: outer clad, front clad, back clad" in lean_text
    window.envelope_plan_selector.set_wall_states({"left": True, "right": True, "front": True, "back": True})
    window.left_lean_enclosure_selector.set_wall_states({"left": False, "right": False, "front": False, "back": False})
    window.generate_and_solve()
    open_lean_text = window.results_box.toPlainText()
    assert "Table 5.1(B)" in open_lean_text
    assert "Left  wind model   : AS/NZS B.5 free roof" not in open_lean_text
    window.envelope_plan_selector.set_wall_states({"left": False, "right": True, "front": True, "back": True})
    window.left_lean_enclosure_selector.set_wall_states({"left": True, "right": False, "front": True, "back": True})
    window.generate_and_solve()
    connected_lean_text = window.results_box.toPlainText()
    assert "Main chamber Cpi    : +0.200 (connected main/lean chamber, enclosed chamber Cpi)" in connected_lean_text
    assert "Left  connected main/lean chamber, enclosed chamber Cpi:   +0.200 coeff" in connected_lean_text
    window.envelope_plan_selector.set_wall_states({"left": True, "right": True, "front": True, "back": True})
    window.left_canopy_length_input.setText("0")
    window.left_canopy_eave_height_input.setText("")
    window.left_canopy_wind_model_combo.setCurrentText("Main roof Cpe + Cpi")
    window.left_canopy_type_combo.setCurrentText("None")
    window.generate_and_solve()
    assert window.costing_summary is not None
    assert window.costing_summary["portal_count"] > 1
    assert window.costing_summary["purlin_total_kg"] > 0.0
    end_wall_mullion_item = window.costing_summary["end_wall_mullion_item"]
    assert end_wall_mullion_item is not None
    assert end_wall_mullion_item["count"] == (
        window.end_wall_mullion_design["end_wall_count"] * window.end_wall_mullion_design["mullion_count"]
    )
    assert end_wall_mullion_item["kg_base"] > 0.0
    assert window.costing_summary["end_wall_mullion_total_kg"] >= end_wall_mullion_item["kg_base"]
    assert math.isclose(
        window.costing_summary["costs"]["end_wall_mullion"],
        window.costing_summary["end_wall_mullion_total_kg"]
        * window.costing_summary["rates"][end_wall_mullion_item["rate_bucket"]],
        rel_tol=1e-9,
    )
    end_wall_girt_item = next(item for item in window.costing_summary["purlin_items"] if item["label"] == "End-wall girts")
    assert end_wall_girt_item["kg"] > 0.0
    assert end_wall_girt_item["rows_per_end"] > 0
    assert "END-WALL MULLIONS" in window.costing_box.toPlainText()
    assert "End-wall girts:" in window.costing_box.toPlainText()
    assert window.costing_summary["crane_beam_total_kg"] == 0.0
    assert "CRANE RUNWAY BEAM" in window.section_checks_box.toPlainText()
    assert "CRANE RUNWAY BEAMS" in window.costing_box.toPlainText()
    assert "Not included - no crane rating entered." in window.costing_box.toPlainText()
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
    test_structure = main.Structure2D()
    test_base = main.Node(1, 0.0, 0.0)
    test_mid = main.Node(2, 0.0, 4000.0)
    test_eave = main.Node(3, 0.0, 7000.0)
    for node in [test_base, test_mid, test_eave]:
        test_structure.add_node(node)
    test_profile = window.selected_profile(window.left_column_profile_combo)
    test_structure.add_element(main.FrameElement2D(1, test_base, test_mid, 200000.0, test_profile, "LEFT_COLUMN"))
    test_structure.add_element(main.FrameElement2D(2, test_mid, test_eave, 200000.0, test_profile, "LEFT_COLUMN"))
    test_structure.add_support(test_base.ux)
    test_structure.add_support(test_base.uy)
    test_structure.add_support(test_base.rz)
    test_k = test_structure.elastic_stiffness_matrix()
    test_loads = np.zeros((len(test_structure.nodes) * 3, 2))
    test_loads[test_eave.ux, :] = [1000.0, -2500.0]
    batched_displacements = test_structure.solve_load_matrix_with_stiffness(test_k, test_loads)
    for load_index in range(test_loads.shape[1]):
        individual_displacements = test_structure.solve_with_stiffness(test_k, test_loads[:, load_index])
        assert np.allclose(batched_displacements[:, load_index], individual_displacements)

    # Uniform wall-load benchmark: a pin-ended column must recover the full
    # member bending and curvature, not merely balance half the load at each end.
    benchmark_structure = main.Structure2D()
    benchmark_base = main.Node(1, 0.0, 0.0)
    benchmark_top = main.Node(2, 0.0, 10000.0)
    benchmark_structure.add_node(benchmark_base)
    benchmark_structure.add_node(benchmark_top)
    benchmark_element = main.FrameElement2D(
        1,
        benchmark_base,
        benchmark_top,
        200000.0,
        test_profile,
        "RIGHT_LEAN_COLUMN",
    )
    benchmark_structure.add_element(benchmark_element)
    benchmark_structure.add_support(benchmark_base.ux)
    benchmark_structure.add_support(benchmark_base.uy)
    benchmark_structure.add_support(benchmark_top.ux)
    benchmark_q_n_mm = 2.75  # numerically identical to kN/m
    matched = benchmark_structure.add_uniform_global_line_load_between(
        benchmark_base,
        benchmark_top,
        fx_n_per_mm=benchmark_q_n_mm,
        element_groups={"RIGHT_LEAN_COLUMN"},
    )
    assert matched == [benchmark_element]
    benchmark_d, benchmark_r, benchmark_f = benchmark_structure.solve()
    benchmark_summary = benchmark_structure.element_force_summary(benchmark_element, benchmark_d)
    benchmark_length_mm = benchmark_element.length()
    expected_total_n = benchmark_q_n_mm * benchmark_length_mm
    expected_shear_kn = expected_total_n / 2.0 / 1000.0
    expected_moment_knm = benchmark_q_n_mm * benchmark_length_mm**2 / 8.0 / 1_000_000.0
    expected_midspan_mm = (
        5.0 * benchmark_q_n_mm * benchmark_length_mm**4
        / (384.0 * benchmark_element.E * benchmark_element.I)
    )
    benchmark_midspan_ux, _ = benchmark_structure.element_global_displacement_at(
        benchmark_element,
        benchmark_d,
        0.5,
    )
    assert math.isclose(benchmark_f[benchmark_base.ux] + benchmark_f[benchmark_top.ux], expected_total_n, rel_tol=1e-9)
    assert math.isclose(benchmark_r[benchmark_base.ux] + benchmark_r[benchmark_top.ux], -expected_total_n, rel_tol=1e-9)
    assert math.isclose(benchmark_summary["shear_kn"], expected_shear_kn, rel_tol=1e-9)
    assert math.isclose(benchmark_summary["moment_knm"], expected_moment_knm, rel_tol=1e-9)
    assert math.isclose(abs(benchmark_midspan_ux), expected_midspan_mm, rel_tol=1e-9)
    benchmark_structure.clear_loads()
    assert not benchmark_structure.loads
    assert not benchmark_structure.element_uniform_loads

    # Roof area loads retain their horizontal-projection resultant but are
    # carried by the sloping chord as a true member UDL, including local
    # chord bending between panel points.
    roof_structure = main.Structure2D()
    roof_left = main.Node(1, 0.0, 0.0)
    roof_right = main.Node(2, 10000.0, 1000.0)
    roof_structure.add_node(roof_left)
    roof_structure.add_node(roof_right)
    roof_element = main.FrameElement2D(
        1,
        roof_left,
        roof_right,
        200000.0,
        test_profile,
        "TOP",
    )
    roof_structure.add_element(roof_element)
    roof_structure.add_support(roof_left.ux)
    roof_structure.add_support(roof_left.uy)
    roof_structure.add_support(roof_right.uy)
    roof_line_kn_m = -2.0
    apply_roof_vertical_loads(
        roof_structure,
        [roof_left, roof_right],
        roof_line_kn_m,
        element_groups={"TOP"},
    )
    roof_length_mm = roof_element.length()
    roof_local_load = roof_structure.uniform_load_for_element(roof_element)
    expected_roof_total_n = roof_line_kn_m * 10000.0
    expected_member_global_y_n_mm = expected_roof_total_n / roof_length_mm
    expected_local_transverse_n_mm = (
        (roof_right.x - roof_left.x) / roof_length_mm
        * expected_member_global_y_n_mm
    )
    assert math.isclose(
        sum(value for dof, value in roof_structure.loads.items() if dof % 3 == 1),
        expected_roof_total_n,
        rel_tol=1e-9,
    )
    assert math.isclose(roof_local_load[1], expected_local_transverse_n_mm, rel_tol=1e-9)
    roof_d, _, _ = roof_structure.solve()
    roof_summary = roof_structure.element_force_summary(roof_element, roof_d)
    expected_roof_moment_knm = (
        abs(expected_local_transverse_n_mm) * roof_length_mm**2 / 8.0 / 1_000_000.0
    )
    assert math.isclose(roof_summary["moment_knm"], expected_roof_moment_knm, rel_tol=1e-9)

    # Fixed-ended companion benchmark verifies that the same member-load
    # formulation recovers rigid-joint end moments and restrained curvature.
    fixed_structure = main.Structure2D()
    fixed_base = main.Node(1, 0.0, 0.0)
    fixed_top = main.Node(2, 0.0, 10000.0)
    fixed_structure.add_node(fixed_base)
    fixed_structure.add_node(fixed_top)
    fixed_element = main.FrameElement2D(
        1,
        fixed_base,
        fixed_top,
        200000.0,
        test_profile,
        "RIGHT_LEAN_COLUMN",
    )
    fixed_structure.add_element(fixed_element)
    fixed_structure.add_support(fixed_base.ux)
    fixed_structure.add_support(fixed_base.uy)
    fixed_structure.add_support(fixed_base.rz)
    fixed_structure.add_support(fixed_top.ux)
    fixed_structure.add_support(fixed_top.rz)
    fixed_structure.add_uniform_global_line_load_between(
        fixed_base,
        fixed_top,
        fx_n_per_mm=benchmark_q_n_mm,
        element_groups={"RIGHT_LEAN_COLUMN"},
    )
    fixed_d, fixed_r, fixed_f = fixed_structure.solve()
    fixed_summary = fixed_structure.element_force_summary(fixed_element, fixed_d)
    fixed_midspan_ux, _ = fixed_structure.element_global_displacement_at(
        fixed_element,
        fixed_d,
        0.5,
    )
    expected_fixed_moment_knm = (
        benchmark_q_n_mm * fixed_element.length() ** 2 / 12.0 / 1_000_000.0
    )
    expected_fixed_midspan_mm = (
        benchmark_q_n_mm * fixed_element.length() ** 4
        / (384.0 * fixed_element.E * fixed_element.I)
    )
    assert math.isclose(fixed_f[fixed_base.ux] + fixed_f[fixed_top.ux], expected_total_n, rel_tol=1e-9)
    assert math.isclose(fixed_r[fixed_base.ux] + fixed_r[fixed_top.ux], -expected_total_n, rel_tol=1e-9)
    assert math.isclose(fixed_summary["shear_kn"], expected_shear_kn, rel_tol=1e-9)
    assert math.isclose(fixed_summary["moment_knm"], expected_fixed_moment_knm, rel_tol=1e-9)
    assert math.isclose(abs(fixed_midspan_ux), expected_fixed_midspan_mm, rel_tol=1e-9)

    test_cuts = window.costing_physical_cut_lengths_m(test_structure)
    assert len(test_cuts) == 1
    assert test_cuts[0][0] is test_profile
    assert math.isclose(test_cuts[0][1], 7.0, abs_tol=1e-9)
    assert window.costing_summary["shs_wastage_factor"] == 1.07
    assert window.costing_summary["continuous_lap_factor"] == 1.15
    assert "wb" in window.costing_summary["rates"]
    assert window.costing_summary["crane_beam_item"] is None
    assert window.costing_summary["per_portal_weights"]["shs"] >= window.costing_summary["per_portal_base_weights"]["shs"] * 1.07 - 1e-9
    assert window.costing_summary["ub_stock_length_m_per_portal"] >= window.costing_summary["ub_cut_length_m_per_portal"]
    assert "TOTAL ESTIMATED COST" in window.costing_box.toPlainText()
    assert "+ 7% waste" in window.costing_box.toPlainText()
    assert "rounded to stock length" in window.costing_box.toPlainText()
    window.purlin_span_type_combo.setCurrentText("Continuous lapped")
    window.generate_and_solve()
    assert any(item["lap_factor"] == 1.15 for item in window.costing_summary["purlin_items"] if item["label"].startswith("Roof"))
    roof_purlin_items = [
        item for item in window.costing_summary["purlin_items"]
        if item["label"].startswith("Roof")
    ]
    assert roof_purlin_items
    assert all(item["display_section"].startswith("Z ") for item in roof_purlin_items)
    assert "Z " in window.purlin_check_label.text()
    assert "C/Z" not in window.purlin_check_label.text()
    assert "x 1.15 lap" in window.costing_box.toPlainText()
    assert window.total_cost_label.text() == f"${window.costing_summary['total_cost']:,.0f}"
    factory_costing = window.factory_default_data["costing"]
    assert window.shs_cost_input.text() == factory_costing["shs_rate_per_kg"]
    assert window.doubled_chord_cost_input.text() == factory_costing["doubled_chord_rate_per_kg"]
    assert window.ub_cost_input.text() == factory_costing["ub_rate_per_kg"]
    assert window.wb_cost_input.text() == factory_costing["wb_rate_per_kg"]
    assert window.purlin_cost_input.text() == factory_costing["purlin_rate_per_kg"]
    current_summary = window.costing_summary
    window.shs_cost_input.setText("9.99")
    window.shs_cost_input.editingFinished.emit()
    assert window.costing_summary is current_summary
    window.shs_cost_input.setText(factory_costing["shs_rate_per_kg"])
    saved_defaults = json.loads(json.dumps(window.collect_preset_data()))
    test_defaults = json.loads(json.dumps(saved_defaults))
    test_defaults["geometry"]["span_mm"] = "32000"
    test_defaults["sections"]["purlin_span_type"] = "Continuous lapped"
    with tempfile.TemporaryDirectory() as temp_dir:
        defaults_path = Path(temp_dir) / "portal_frame_defaults.json"
        defaults_path.write_text(json.dumps(test_defaults), encoding="utf-8")
        window.default_settings_path = lambda: str(defaults_path)
        window.span_input.setText("0.111")
        assert window.load_default_inputs(silent=True)
        assert window.span_input.text() == "32"
        assert window.purlin_span_type_combo.currentText() == "Continuous lapped"
        window.span_input.setText("0.111")
        window.current_preset_path = "old-job.json"
        original_question = QMessageBox.question
        try:
            QMessageBox.question = lambda *args, **kwargs: QMessageBox.Discard
            window.new_preset()
        finally:
            QMessageBox.question = original_question
        assert window.span_input.text() == "32"
        assert window.current_preset_path is None
        assert window.windowTitle().endswith("Untitled")
        assert window.structure is not None
    window.apply_preset_data(saved_defaults)
    window.shs_cost_input.setText("bad")
    assert window.costing_rate_from_input(window.shs_cost_input, "shs_rate_per_kg") == float(main.COSTING_DEFAULTS["shs_rate_per_kg"])
    window.shs_cost_input.setText(factory_costing["shs_rate_per_kg"])
    assert window.wall_girt_check["spacing_mm"] == window.actual_wall_girt_spacing_mm(
        float(window.eave_input.text()) * 1000.0,
        window.wind_region_combo.currentText(),
    )
    expected_wall_girt_spacing = (
        max(float(window.eave_input.text()) * 1000.0 - 300.0, 0.0)
        / max(1, math.ceil(max(float(window.eave_input.text()) * 1000.0 - 300.0, 0.0) / window.wall_girt_spacing_mm(window.wind_region_combo.currentText())))
    )
    assert math.isclose(window.wall_girt_check["spacing_mm"], expected_wall_girt_spacing, rel_tol=1e-9)
    rank_check = window.match_metroll_purlin("Double span", 8000.0, 1.58, 0.70, 0.55)
    assert rank_check["section"] == "C/Z 200 19"
    interpolated = window.match_metroll_purlin("Single span", 7842.0, 1.20, 0.40, 0.15)
    assert interpolated["interpolated"]
    single_capped = window.match_metroll_purlin("Single span", 7842.0, 0.96, 0.67, 0.55)
    assert single_capped["section"] == "C/Z 200 15"
    window.wind_region_combo.setCurrentText("C")
    window.generate_and_solve()
    assert window.wall_girt_check["spacing_mm"] == window.actual_wall_girt_spacing_mm(
        float(window.eave_input.text()) * 1000.0,
        window.wind_region_combo.currentText(),
    )
    expected_region_c_spacing = (
        max(float(window.eave_input.text()) * 1000.0 - 300.0, 0.0)
        / max(1, math.ceil(max(float(window.eave_input.text()) * 1000.0 - 300.0, 0.0) / window.wall_girt_spacing_mm(window.wind_region_combo.currentText())))
    )
    assert math.isclose(window.wall_girt_check["spacing_mm"], expected_region_c_spacing, rel_tol=1e-9)
    left_column_restraint = window.member_check_restraints["LEFT_COLUMN"]
    expected_left_column_flybrace_length = abs(window.concept_main_top_nodes[0].y - window.base_nodes[0].y) / 2.0
    assert math.isclose(left_column_restraint["major_mm"], expected_left_column_flybrace_length, rel_tol=1e-9)
    assert math.isclose(left_column_restraint["minor_mm"], expected_left_column_flybrace_length, rel_tol=1e-9)

    window.right_canopy_length_input.setText("6")
    window.right_canopy_type_combo.setCurrentText("Lean-to")
    window.right_lean_outer_wall_clad_combo.setCurrentText("Yes")
    window.right_lean_enclosure_selector.set_wall_states({"right": True})
    window.generate_and_solve()
    right_lean_column_elements = [
        element for element in window.structure.elements
        if element.group == "RIGHT_LEAN_COLUMN"
    ]
    assert len(right_lean_column_elements) == 2
    lower_lean_column = min(right_lean_column_elements, key=lambda element: min(element.start.y, element.end.y))
    upper_lean_column = max(right_lean_column_elements, key=lambda element: min(element.start.y, element.end.y))
    shared_bottom_chord_node = lower_lean_column.start if lower_lean_column.start.y > lower_lean_column.end.y else lower_lean_column.end
    upper_column_bottom_node = upper_lean_column.start if upper_lean_column.start.y < upper_lean_column.end.y else upper_lean_column.end
    assert shared_bottom_chord_node is upper_column_bottom_node
    assert upper_lean_column.profile is lower_lean_column.profile
    outer_top_chord_node = window.concept_right_lean_top_nodes[-1]
    upper_column_top_node = upper_lean_column.start if upper_lean_column.start.y > upper_lean_column.end.y else upper_lean_column.end
    assert upper_column_top_node is outer_top_chord_node
    window.load_combination_combo.setCurrentText("1.2G + Wu (Right +, Cpi -)")
    window.generate_and_solve()
    right_lean_column_elements = [
        element for element in window.structure.elements
        if element.group == "RIGHT_LEAN_COLUMN"
    ]
    assert len(right_lean_column_elements) == 2
    assert all(
        abs(window.structure.uniform_load_for_element(element)[1]) > 1e-9
        for element in right_lean_column_elements
    )
    assert any(
        abs(window.structure.uniform_load_for_element(element)[1]) > 1e-9
        for element in window.structure.elements
        if element.group in {"LEFT_COLUMN", "RIGHT_COLUMN"}
    )
    right_lean_restraint = window.member_check_restraints["RIGHT_LEAN_COLUMN"]
    right_lean_column_nodes = {
        node
        for element in right_lean_column_elements
        for node in (element.start, element.end)
    }
    expected_right_lean_flybrace_length = (
        max(node.y for node in right_lean_column_nodes)
        - min(node.y for node in right_lean_column_nodes)
    ) / 2.0
    assert math.isclose(right_lean_restraint["major_mm"], expected_right_lean_flybrace_length, rel_tol=1e-9)
    assert math.isclose(right_lean_restraint["minor_mm"], expected_right_lean_flybrace_length, rel_tol=1e-9)
    right_lean_midheight = window.design_envelope_actions["sls_column_midspan_ws_deflections"]["RIGHT_LEAN_COLUMN"]
    assert right_lean_midheight["value"] >= 0.0
    assert "mid Ws" in window.right_lean_column_deflection_check_label.text()
    assert "H/" in window.right_lean_column_deflection_check_label.text()
    assert "Load case:" in window.right_lean_column_deflection_check_label.toolTip()
    stability_rows = {
        item["group"]: item for item in window.assistant_column_stability_context_rows()
    }
    right_lean_stability = stability_rows["RIGHT_LEAN_COLUMN"]
    assert right_lean_stability["wall_clad"] is True
    assert "mid-height flybrace" in right_lean_stability["restraint_assumption"]
    assert math.isclose(
        right_lean_stability["major_restraint_length_mm"],
        expected_right_lean_flybrace_length,
        rel_tol=1e-9,
    )
    assert right_lean_stability["mid_height_ws_deflection"]["checked"] is True
    assert right_lean_stability["mid_height_ws_deflection"]["ratio"] is not None
    lean_help_snapshot = window.assistant_help_snapshot()
    lean_help_base = window.assistant_capture_help_base_state(lean_help_snapshot)
    original_right_lean_section = window.right_lean_column_profile_combo.currentText()
    window.assistant_help_session = {
        "snapshot": lean_help_snapshot,
        "base": lean_help_base,
        "trials": [],
    }
    lean_column_trial = window.assistant_execute_help_trial({
        "hypothesis": "Test one stiffer right lean-to outer column",
        "truss_depth_delta_mm": 0,
        "left_addition_depth_delta_mm": 0,
        "right_addition_depth_delta_mm": 0,
        "left_column_steps": 0,
        "right_column_steps": 0,
        "left_lean_column_steps": 0,
        "right_lean_column_steps": 1,
        "top_member_steps": 0,
        "bottom_chord_steps": 0,
        "web_steps": 0,
        "panel_layout_steps": 0,
    })
    assert lean_column_trial["solved"] is True
    assert any(
        change.get("parameter") == "RIGHT_LEAN_COLUMN"
        for change in lean_column_trial["applied_changes"]
    )
    assert lean_column_trial["live_design_restored"] is True
    assert window.right_lean_column_profile_combo.currentText() == original_right_lean_section
    window.assistant_help_session = None
    main_right_x = window.concept_main_top_nodes[-1].x
    left_cpe_vertical = sorted(
        (
            arrow for arrow in window.single_load_arrows["Wu Cpe Left -"]
            if abs(arrow[3]) > 1e-9
        ),
        key=lambda arrow: arrow[0],
    )
    last_main_cpe = max((arrow for arrow in left_cpe_vertical if arrow[0] < main_right_x), key=lambda arrow: arrow[0])
    first_lean_cpe = min((arrow for arrow in left_cpe_vertical if arrow[0] > main_right_x), key=lambda arrow: arrow[0])
    assert abs(first_lean_cpe[2]) > 1e-9
    assert math.isclose(
        math.hypot(first_lean_cpe[2], first_lean_cpe[3]),
        abs(last_main_cpe[3]),
        rel_tol=1e-9,
    )

    unrestrained_total_cost = window.costing_summary["total_cost"]
    window.eave_x_restraint_combo.setCurrentText("One end")
    window.end_wall_braced_sides_combo.setCurrentText("One side")
    window.end_wall_brace_size_combo.setCurrentText(next(
        window.end_wall_brace_size_combo.itemText(index)
        for index in range(window.end_wall_brace_size_combo.count())
        if window.end_wall_brace_size_combo.itemText(index).startswith("16 DIA ROD")
    ))
    window.generate_and_solve()
    spring_values = list(window.structure.springs.values())
    assert len(spring_values) == 1
    brace_area_mm2 = window.selected_end_wall_brace_size()["area_mm2"]
    one_square_kn_mm = (200000.0 * brace_area_mm2 / (math.sqrt(2.0) * 8000.0)) * 0.5 / 1000.0
    bracing_path_calibration_factor = 4.0 / 53.0
    expected_spring_kn_mm = one_square_kn_mm * bracing_path_calibration_factor / 3.0
    assert all(abs(value / 1000.0 - expected_spring_kn_mm) < 1e-9 for value in spring_values)
    assert "End-wall portal restraint model" in window.results_box.toPlainText()
    assert "1 end(s)" in window.results_box.toPlainText()
    assert "path 20.00 m" in window.results_box.toPlainText()
    assert "SpaceGASS path calibration" in window.results_box.toPlainText()
    assert window.longitudinal_bracing_design is None
    restraint_cost_item = window.costing_summary["end_wall_restraint_item"]
    assert restraint_cost_item["physical_x_count_per_end_side"] == 6
    assert restraint_cost_item["braced_square_count"] == 6
    assert restraint_cost_item["run_count"] == 12
    assert restraint_cost_item["turnbuckle_count"] == 12
    assert restraint_cost_item["turnbuckle_size"] == "16"
    assert math.isclose(restraint_cost_item["turnbuckle_unit_cost"], 9.62, rel_tol=1e-12)
    assert math.isclose(
        window.costing_summary["costs"]["end_wall_restraint"],
        restraint_cost_item["kg"] * window.costing_summary["rates"]["ub"]
        + restraint_cost_item["turnbuckle_count"] * 9.62,
        rel_tol=1e-9,
    )
    assert window.costing_summary["total_cost"] > unrestrained_total_cost
    assert "END-WALL PORTAL RESTRAINT" in window.costing_box.toPlainText()
    assert "12 x size 16 at $9.62" in window.costing_box.toPlainText()

    window.longitudinal_bracing_enabled_checkbox.setChecked(True)
    window.longitudinal_wall_sides_combo.setCurrentText("Both sides")
    window.end_wall_braced_bay_count_input.setValue(1)
    window.generate_and_solve()
    assert window.longitudinal_bracing_design["ultimate_resultant_kn"] > 0.0
    assert window.longitudinal_bracing_design["roof_uls"]["brace_force_kn"] > 0.0
    assert window.longitudinal_bracing_design["wall_uls"]["brace_force_kn"] > 0.0
    assert window.longitudinal_bracing_design["wall_uls"]["brace_force_kn"] > window.longitudinal_bracing_design["roof_uls"]["brace_force_kn"]
    assert window.longitudinal_bracing_design["strut_demand_kn"] > 0.0
    assert window.longitudinal_bracing_design["strut"]["label"]
    assert len(window.longitudinal_bracing_design["eave_struts"]) == 2
    assert len(window.longitudinal_bracing_design["mullion_ties"]) == window.end_wall_mullion_design["mullion_count"]
    assert all(item["label"].startswith("Mullion tie M") for item in window.longitudinal_bracing_design["mullion_ties"])
    assert "Mullion tie M1" in window.bracing_strut_result_label.text()
    if len(window.longitudinal_bracing_design["mullion_ties"]) > 1:
        assert "Mullion tie M2" in window.bracing_strut_result_label.text()
    assert "LONGITUDINAL BRACING - PRELIMINARY CHECK" in window.section_checks_box.toPlainText()
    assert "EAVE STRUTS" in window.section_checks_box.toPlainText()
    assert "MULLION TIES" in window.section_checks_box.toPlainText()
    assert window.longitudinal_bracing_status_label.text().startswith(("OK", "NOT OK"))
    assert "N*" in window.roof_bracing_load_label.text()
    assert "N*" in window.wall_bracing_load_label.text()

    window.end_wall_braced_sides_combo.setCurrentText("Both sides")
    window.generate_and_solve()
    both_side_spring_values = list(window.structure.springs.values())
    assert len(both_side_spring_values) == 2
    assert all(abs(value / 1000.0 - expected_spring_kn_mm) < 1e-9 for value in both_side_spring_values)
    assert "both sides" in window.results_box.toPlainText().lower()

    window.eave_x_restraint_combo.setCurrentText("Both ends")
    window.generate_and_solve()
    model_spring_values = list(window.structure.springs.values())
    model_expected_spring_kn_mm = 2.0 * expected_spring_kn_mm
    assert len(model_spring_values) == 2
    assert all(abs(value / 1000.0 - model_expected_spring_kn_mm) < 1e-9 for value in model_spring_values)
    assert "End-wall portal restraint model" in window.results_box.toPlainText()
    assert "2 end(s)" in window.results_box.toPlainText()
    assert window.costing_summary["end_wall_restraint_item"]["run_count"] == 48

    window.eave_x_restraint_combo.setCurrentText("None")
    window.generate_and_solve()
    assert window.active_eave_x_restraint == "None"
    assert window.costing_summary["end_wall_restraint_item"] is None
    assert math.isclose(window.costing_summary["costs"]["end_wall_restraint"], 0.0, abs_tol=1e-12)
    assert window.active_eave_x_spring_kn_mm == 0.0
    assert window.eave_bracing_info is None
    assert window.structure.springs == {}
    assert "X spring" not in window.support_types
    assert "X restraint" not in window.support_types
    assert "Eave X restraint: None" in window.results_box.toPlainText()

    saved_span_text = window.span_input.text()
    window.eave_x_restraint_combo.setCurrentText("One end")
    window.generate_and_solve()
    assert len(window.structure.springs) == 2
    window.eave_x_restraint_combo.setCurrentText("None")
    window.span_input.setText("0")
    window.generate_and_solve()
    assert window.structure is None
    assert window.support_types is None
    assert window.active_eave_x_restraint == "None"
    assert "span, eave height and truss depth must be positive" in window.results_box.toPlainText()
    window.span_input.setText(saved_span_text)
    window.generate_and_solve()

    window.crane_rating_input.setText("5")
    window.apply_crane_rating_to_crane_beam_inputs()
    assert window.crane_beam_pmax21_input.text() == "43.9"
    assert window.crane_beam_pmin11_input.text() == "13.0"
    assert window.crane_beam_pstatic21_input.text() == "36.7"
    assert window.crane_beam_pstatic_min11_input.text() == "11.8"
    assert window.crane_beam_phb21_input.text() == "6.21"
    assert window.crane_beam_phb22_input.text() == "-6.21"
    assert window.crane_beam_phi2_input.text() == "6.25"
    window.crane_rating_input.setText("15")
    window.apply_crane_rating_to_crane_beam_inputs()
    assert window.crane_beam_pmax21_input.text() == "116.6"
    assert window.crane_beam_phi2_input.text() == "14.35"
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
    assert window.costing_summary["crane_beam_total_kg"] > 0.0
    crane_item = window.costing_summary["crane_beam_item"]
    assert crane_item["rate_bucket"] in {"ub", "wb"}
    expected_crane_rate = window.costing_summary["rates"][crane_item["rate_bucket"]]
    assert math.isclose(
        window.costing_summary["costs"]["crane_beam"],
        window.costing_summary["crane_beam_total_kg"] * expected_crane_rate,
        rel_tol=1e-9,
    )
    left_column_restraint = window.member_check_restraints["LEFT_COLUMN"]
    assert left_column_restraint["crane_minor"]
    assert math.isclose(
        left_column_restraint["minor_mm"] * left_column_restraint["ke"],
        0.85 * left_column_restraint["minor_mm"],
        rel_tol=1e-9,
    )
    assert "crane minor length: base to crane height" in window.section_checks_box.toPlainText()
    assert "Crane rail deflection SLS" in window.section_checks_box.toPlainText()
    assert "H/250" in window.section_checks_box.toPlainText()
    assert "crane rail" in window.column_deflection_check_label.text()
    assert "H/250" in window.column_deflection_check_label.text()

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
        canopy_doubler_groups = {
            zone["group"] for zone in window.design_envelope_actions["doubled_chord_zones"]
            if zone["group"].startswith(("LEFT_CANOPY_", "RIGHT_CANOPY_"))
        }
        # Wider legal webs are now exhausted before a canopy chord doubler is
        # introduced, so this preset may legitimately need no joint doubler.
        for web_group in ("LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"):
            assignments = window.auto_web_profile_assignments.get(web_group, {})
            if not assignments:
                continue
            combo = window.web_group_combo(web_group)
            ratio = window.web_assignment_overall_ratio(window.selected_profile(combo), web_group)
            assert ratio is not None and ratio[0] <= 1.0
        doubled_zone_ids = {
            (zone["group"], int(zone["member"]))
            for zone in window.design_envelope_actions["doubled_chord_zones"]
        }
        for zone in window.design_envelope_actions["doubled_chord_zones"]:
            if not zone.get("web_joint_trigger"):
                continue
            assert all(
                (zone["group"], int(member_id)) in doubled_zone_ids
                for member_id in zone.get("joint_incident_members", [])
            )
        doubled_chord_web_groups = {
            "TOP": {"WEB", "POST", "APEX"},
            "BOTTOM": {"WEB", "POST", "APEX"},
            "LEFT_CANOPY_TOP": {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"},
            "LEFT_CANOPY_BOTTOM": {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"},
            "RIGHT_CANOPY_TOP": {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"},
            "RIGHT_CANOPY_BOTTOM": {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"},
        }
        for group, web_groups in doubled_chord_web_groups.items():
            chord_elements = [element for element in window.structure.elements if element.group == group]
            if not chord_elements:
                continue
            element_by_id = {element.id: element for element in chord_elements}
            def low_node(element):
                return min((element.start, element.end), key=lambda node: (node.x, node.y))
            def high_node(element):
                return max((element.start, element.end), key=lambda node: (node.x, node.y))
            chord_node_keys = [(round(node.x, 6), round(node.y, 6)) for element in chord_elements for node in (element.start, element.end)]
            end_keys = {
                min(chord_node_keys, key=lambda item: item[0]),
                max(chord_node_keys, key=lambda item: item[0]),
            }
            web_node_keys = {
                (round(node.x, 6), round(node.y, 6))
                for element in window.structure.elements
                if element.group in web_groups
                for node in (element.start, element.end)
            }
            doubled_elements = [
                element_by_id[int(zone["member"])]
                for zone in window.design_envelope_actions["doubled_chord_zones"]
                if zone["group"] == group and int(zone["member"]) in element_by_id
            ]
            doubled_elements.sort(key=lambda element: (low_node(element).x, low_node(element).y, element.id))
            runs = []
            for element in doubled_elements:
                if not runs or (round(high_node(runs[-1][-1]).x, 6), round(high_node(runs[-1][-1]).y, 6)) != (round(low_node(element).x, 6), round(low_node(element).y, 6)):
                    runs.append([element])
                else:
                    runs[-1].append(element)
            for run in runs:
                for node in (low_node(run[0]), high_node(run[-1])):
                    key = (round(node.x, 6), round(node.y, 6))
                    if key in end_keys:
                        continue
                    assert key in web_node_keys
        doubled_kg = window.costing_summary["doubled_chord_total_kg"]
        if window.design_envelope_actions["doubled_chord_zones"]:
            assert window.costing_summary["doubled_chord_items"]
            assert doubled_kg > 0.0
            assert math.isclose(
                window.costing_summary["costs"]["doubled_chord"],
                doubled_kg * window.costing_summary["rates"]["doubled_chord"],
                rel_tol=1e-9,
            )
            assert "Doubled chord extra:" in window.costing_box.toPlainText()
        else:
            assert not window.costing_summary["doubled_chord_items"]
            assert math.isclose(doubled_kg, 0.0, abs_tol=1e-9)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PortalCalc smoke and regression checks.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--quick",
        action="store_true",
        help="Run compile, non-UI checks, app startup, and the first UI solve only.",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Run the full UI regression smoke suite. This is the default.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode_name = "quick" if args.quick else "full"
    root = Path(__file__).resolve().parent
    with timed_step("compile sources"):
        compile_sources(root)
    with timed_step("calculation service contract checks"):
        run_calculation_service_smoke_checks()
    with timed_step("reduced optimiser numerical checks"):
        run_reduced_optimizer_smoke_checks()
    with timed_step("roof member UDL checks"):
        run_roof_udl_smoke_checks()
    with timed_step("license and calculation checks"):
        run_license_smoke_checks()
    with timed_step("structural assistant checks"):
        run_structural_assistant_smoke_checks()
    with timed_step(f"{mode_name} UI checks"):
        run_ui_smoke_checks(quick=args.quick)
    print(f"{mode_name} smoke checks ok")


if __name__ == "__main__":
    main()
