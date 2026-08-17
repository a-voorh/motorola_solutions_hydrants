"""Deterministic tests for the planning-reserve flow semantics (no Streamlit)."""

import pytest

import pandas as pd

from core import (
    flow_status_lines,
    planning_target_flow,
    run_initial_analysis,
    summarize_flow,
)


# --- status semantics ------------------------------------------------------

def test_minimum_met_but_target_slightly_short():
    flow = summarize_flow(600.0, 50.0, 899.999)
    assert flow["minimum_met"] is True
    assert flow["target_met"] is False
    assert flow["planning_target_shortfall_l_min"] == pytest.approx(0.001, abs=1e-9)
    lines = flow_status_lines(flow)
    assert any("Minimum request met" in l for l in lines)
    assert any("Planning-target shortfall" in l for l in lines)
    assert not any("Minimum request not met" in l for l in lines)


def test_minimum_not_met():
    flow = summarize_flow(600.0, 50.0, 500.0)
    assert flow["minimum_met"] is False
    assert flow["target_met"] is False
    assert flow["operational_shortfall_l_min"] == pytest.approx(100.0)
    lines = flow_status_lines(flow)
    assert any("Minimum request not met" in l for l in lines)
    assert "100" in [l for l in lines if "operational shortfall" in l][0]


def test_exact_planning_target_fulfilment():
    flow = summarize_flow(600.0, 50.0, 900.0)
    assert flow["minimum_met"] is True
    assert flow["target_met"] is True
    assert flow["planning_target_shortfall_l_min"] == 0.0
    lines = flow_status_lines(flow)
    assert any("Minimum request met" in l for l in lines)
    assert any("Planning reserve fully achieved" in l for l in lines)


def test_reserve_control_changes_target():
    assert planning_target_flow(600.0, 50.0) == pytest.approx(900.0)
    assert planning_target_flow(600.0, 25.0) == pytest.approx(750.0)
    assert planning_target_flow(600.0, 0.0) == pytest.approx(600.0)

    flow = summarize_flow(600.0, 50.0, 900.0)
    assert flow["planning_reserve_l_min"] == pytest.approx(300.0)
    assert flow["planning_target_flow_l_min"] == pytest.approx(900.0)


# --- integration: transcript + reserve -------------------------------------

def _synthetic_hydrants():
    return pd.DataFrame({
        "Hydrant": ["H1", "H2", "H3"],
        "Latitude": [55.6600, 55.6610, 55.6620],
        "Longitude": [12.5500, 12.5510, 12.5520],
        "Capacity_L_min": [1000.0, 1000.0, 1000.0],
        "Available": [True, True, True],
    })


def test_transcript_request_plus_reserve_fields():
    plan, _event = run_initial_analysis(
        55.6600, 12.5500, "We need 600 L/min", _synthetic_hydrants(),
        model="B", planning_reserve_percent=50, distance_method="gis",
    )
    assert plan["stated_minimum_flow_l_min"] == pytest.approx(600.0)
    assert plan["planning_reserve_percent"] == 50
    assert plan["planning_reserve_l_min"] == pytest.approx(300.0)
    assert plan["planning_target_flow_l_min"] == pytest.approx(900.0)
    assert plan["effective_demand"] == pytest.approx(900.0)  # backward compat
    assert plan["delivered_flow_l_min"] == pytest.approx(plan["result"].demand_served)
    assert plan["minimum_met"] is True


def test_update_demand_recomputes_target():
    plan, _event = run_initial_analysis(
        55.6600, 12.5500, "We need 600 L/min", _synthetic_hydrants(),
        model="B", planning_reserve_percent=50, distance_method="gis",
    )
    from core import apply_update
    new_plan, _det, error = apply_update(
        plan, "Increase demand to 1000 L/min", _synthetic_hydrants(),
        distance_method="gis",
    )
    assert error is None
    assert new_plan["stated_minimum_flow_l_min"] == pytest.approx(1000.0)
    assert new_plan["planning_target_flow_l_min"] == pytest.approx(1500.0)
