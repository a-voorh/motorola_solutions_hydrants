"""Tests for recompute-on-update behaviour (C parallel lines + radius extension)."""

import pytest
import pandas as pd

from domain import HydrantLine, IncidentRequest, ModelResult, Params, hose_pieces
from workflow import analyse_incident, apply_update, recompute_plan


def _make_plan(selected_entries, committed_pieces, unavailable, demand=3000.0):
    """Build a minimal demand-known plan with a hand-specified prior result."""
    params = Params(gamma=10000.0)
    selected = []
    for hydrant, dist, nominal, n_lines in selected_entries:
        hp = hose_pieces(dist, params)
        selected.append(HydrantLine(
            hydrant=hydrant, latitude=0.0, longitude=0.0,
            distance_m=dist, nominal_capacity=nominal,
            effective_capacity=nominal, hose_pieces=hp,
            lines=n_lines, hose_pieces_total=hp * n_lines,
        ))
    result = ModelResult(
        model="C-hard", demand=demand, demand_served=demand,
        unmet_demand=0.0, demand_met=True,
        total_nominal_capacity=sum(s.nominal_capacity for s in selected),
        total_effective_capacity=sum(s.effective_capacity for s in selected),
        deployment_time=0.0, hose_pieces_used=committed_pieces,
        carried_pieces_used=committed_pieces, extra_hose_pieces=None,
        radius=100.0, distance_method="manhattan", selected=selected,
    )
    return {
        "location": (55.6, 12.5),
        "effective_demand": demand,
        "model": "C-hard",
        "result": result,
        "selected": {s.hydrant: {"capacity": s.nominal_capacity, "distance": s.distance_m}
                     for s in selected},
        "unavailable": list(unavailable),
        "declined": [],
        "committed_pieces": committed_pieces,
        "params": params,
        "stated_minimum_flow_l_min": 2000.0,
        "planning_reserve_percent": 50.0,
    }


def _hydrants_df():
    # H1/H3 at 100 m from the fire (Manhattan); H2 also at 100 m but unavailable.
    dlat = 100.0 / 111320.0
    return pd.DataFrame({
        "Hydrant": ["H1", "H2", "H3"],
        "Latitude": [55.6 + dlat, 55.6 + dlat, 55.6 + dlat],
        "Longitude": [12.5, 12.5, 12.5],
        "Capacity_L_min": [2000.0, 1000.0, 1000.0],
        "Available": [True, True, True],
    })


def test_recompute_counts_surviving_parallel_lines():
    # H1 deployed with 2 lines (14 pieces), H2 with 1 line (7 pieces) = 21 total.
    # H2 fails: only H1's 14 pieces are active, so 7 pieces are lost, not 14.
    plan = _make_plan([("H1", 100.0, 2000.0, 2), ("H2", 100.0, 1000.0, 1)],
                      committed_pieces=21, unavailable=["H2"])
    new = recompute_plan(plan, _hydrants_df(), "C-hard",
                         distance_method="manhattan", max_radius=500,
                         radius_extension=0)

    assert "H3" in new["selected"]          # budget (30 - 7 = 23) fits H1(14) + H3(7)
    assert new["result"].demand_met
    assert new["result"].hose_pieces_used == 21
    assert new["result"].carried_pieces_used == 21


def test_recompute_extends_search_radius():
    # H2 sits 1600 m away: invisible at max_radius=1500, visible with +200.
    dlat = 1600.0 / 111320.0
    hydrants = pd.DataFrame({
        "Hydrant": ["H1", "H2"],
        "Latitude": [55.6, 55.6 + dlat],
        "Longitude": [12.5, 12.5],
        "Capacity_L_min": [1000.0, 1000.0],
        "Available": [True, True],
    })

    request = IncidentRequest(transcript="We need 1000 L/min",
                              location=(55.6, 12.5), planning_reserve_percent=0.0)
    plan, _event, _cmp = analyse_incident(
        request, hydrants, "B", Params(),
        max_radius=1500, distance_method="manhattan",
    )
    assert set(plan["selected"]) == {"H1"}  # H2 beyond the initial cap

    # Without extension the recompute still cannot reach H2.
    no_ext, _det, err0 = apply_update(
        plan, "Increase demand to 2000 L/min", hydrants, "B",
        max_radius=1500, radius_extension=0, distance_method="manhattan",
    )
    assert err0 is None
    assert "H2" not in no_ext["selected"]

    # With extension the recompute finds H2 and meets the raised demand.
    ext, _det, err1 = apply_update(
        plan, "Increase demand to 2000 L/min", hydrants, "B",
        max_radius=1500, radius_extension=200, distance_method="manhattan",
    )
    assert err1 is None
    assert "H2" in ext["selected"]
    assert ext["result"].demand_met


def test_recompute_requires_hydrant():
    # H1 alone covers the demand; forcing H2 (tiny capacity) must still include it.
    dlat = 30.0 / 111320.0
    hydrants = pd.DataFrame({
        "Hydrant": ["H1", "H2"],
        "Latitude": [55.6, 55.6 + dlat],
        "Longitude": [12.5, 12.5],
        "Capacity_L_min": [1000.0, 100.0],
        "Available": [True, True],
    })

    request = IncidentRequest(transcript="We need 1000 L/min",
                              location=(55.6, 12.5), planning_reserve_percent=0.0)
    plan, _event, _cmp = analyse_incident(
        request, hydrants, "B", Params(),
        max_radius=1500, distance_method="manhattan",
    )
    assert set(plan["selected"]) == {"H1"}

    new = recompute_plan(plan, hydrants, "B", require={"H2"},
                         distance_method="manhattan", max_radius=1500,
                         radius_extension=0)
    assert "H2" in new["selected"]      # forced in
    assert "H1" in new["selected"]      # still needed to meet demand
    assert new["result"].demand_met


def test_initial_analysis_keeps_candidate_margin():
    # H2 sits 80 m away: outside the 30 m covering radius, inside the +100 m
    # candidate margin, so it is a candidate (for force-include) but not selected.
    dlat = 80.0 / 111320.0
    hydrants = pd.DataFrame({
        "Hydrant": ["H1", "H2"],
        "Latitude": [55.6, 55.6 + dlat],
        "Longitude": [12.5, 12.5],
        "Capacity_L_min": [1000.0, 100.0],
        "Available": [True, True],
    })

    request = IncidentRequest(transcript="We need 1000 L/min",
                              location=(55.6, 12.5), planning_reserve_percent=0.0)
    plan, _e, _c = analyse_incident(
        request, hydrants, "B", Params(),
        max_radius=1500, distance_method="manhattan",
    )
    assert set(plan["selected"]) == {"H1"}
    assert "H2" in plan["candidates"].index  # kept by the default +100 m margin
    assert plan["radius"] >= 80.0

    # With no margin, H2 is dropped from the candidate pool.
    no_margin, _e, _c = analyse_incident(
        request, hydrants, "B", Params(),
        max_radius=1500, candidate_margin=0, distance_method="manhattan",
    )
    assert "H2" not in no_margin["candidates"].index


def test_recompute_keeps_candidate_margin():
    # H2 at 80 m: outside the 30 m covering radius but inside the +100 m pad.
    dlat = 80.0 / 111320.0
    hydrants = pd.DataFrame({
        "Hydrant": ["H1", "H2"],
        "Latitude": [55.6, 55.6 + dlat],
        "Longitude": [12.5, 12.5],
        "Capacity_L_min": [1000.0, 100.0],
        "Available": [True, True],
    })

    request = IncidentRequest(transcript="We need 1000 L/min",
                              location=(55.6, 12.5), planning_reserve_percent=0.0)
    plan, _e, _c = analyse_incident(
        request, hydrants, "B", Params(),
        max_radius=1500, candidate_margin=0, distance_method="manhattan",
    )
    assert "H2" not in plan["candidates"].index

    # Default recompute pads the pool, so H2 becomes a candidate (not selected).
    padded = recompute_plan(plan, hydrants, "B",
                            distance_method="manhattan", max_radius=1500,
                            radius_extension=0)
    assert "H2" in padded["candidates"].index
    assert set(padded["selected"]) == {"H1"}

    # candidate_margin=0 leaves H2 out of the recomputed pool.
    unpadded = recompute_plan(plan, hydrants, "B",
                              distance_method="manhattan", max_radius=1500,
                              radius_extension=0, candidate_margin=0)
    assert "H2" not in unpadded["candidates"].index


def test_covered_demand_update_is_metadata_only():
    # H1 (2000 L/min) alone covers the initial 1000 L/min request.
    hydrants = pd.DataFrame({
        "Hydrant": ["H1"],
        "Latitude": [55.6],
        "Longitude": [12.5],
        "Capacity_L_min": [2000.0],
        "Available": [True],
    })
    request = IncidentRequest(transcript="We need 1000 L/min",
                              location=(55.6, 12.5), planning_reserve_percent=0.0)
    plan, _e, _c = analyse_incident(
        request, hydrants, "B", Params(),
        max_radius=1500, distance_method="manhattan",
    )
    assert set(plan["selected"]) == {"H1"}

    # Increase demand to 1500 L/min — still covered by H1 (2000 >= 1500).
    new, det, status = apply_update(
        plan, "Increase demand to 1500 L/min", hydrants, "B",
        max_radius=1500, radius_extension=0, distance_method="manhattan",
    )
    assert status == "covered"
    assert set(new["selected"]) == {"H1"}                 # config unchanged
    assert new["stated_minimum_flow_l_min"] == pytest.approx(1500.0)
    assert new["result"].demand_served == pytest.approx(1500.0)
    assert new["result"].demand_met is True

    # An increase beyond capacity must still recompute (not "covered").
    bigger, det, status2 = apply_update(
        plan, "Increase demand to 3000 L/min", hydrants, "B",
        max_radius=1500, radius_extension=0, distance_method="manhattan",
    )
    assert status2 is None
