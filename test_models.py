"""Deterministic tests for the A/B/C-soft/C-hard water-supply models (no Streamlit).

Each selected hydrant uses a single connection and contributes its full modelled
capacity (decayed where applicable).
"""

import math

import pytest

import pandas as pd

from models import Params, solve_model


def make_candidates(specs):
    """specs: list of (hydrant, distance_m, capacity). Returns (candidates, hydrants_df)."""
    cand_rows = {}
    hyd_rows = []
    for i, (hid, dist, cap) in enumerate(specs):
        cand_rows[hid] = {"Distance_m": float(dist), "Capacity_L_min": float(cap)}
        hyd_rows.append({
            "Hydrant": hid,
            "Latitude": 55.6 + i * 0.001,
            "Longitude": 12.5,
            "Capacity_L_min": float(cap),
        })
    candidates = pd.DataFrame(cand_rows).T
    candidates.index.name = "Hydrant"
    hydrants = pd.DataFrame(hyd_rows)
    return candidates, hydrants


# --- Model A ignores q and r -----------------------------------------------

def test_model_a_ignores_q_and_r():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    res = solve_model("A", cand, 1000.0, params=Params(q=10.0, r=0.058), hydrants_df=hyd)

    # Single hydrant, full nominal capacity (no decay) -> objective = 100 / 5 = 20.
    assert res.demand_met
    assert [s.hydrant for s in res.selected] == ["H1"]
    assert res.selected[0].effective_capacity == pytest.approx(1000.0)
    assert res.demand_served == pytest.approx(1000.0)
    assert res.deployment_time == pytest.approx(20.0)
    # H1 at 100 m -> 4 hose pieces (ceil(100 / 30)); within carried stock.
    assert res.selected[0].hose_pieces == 4
    assert res.hose_pieces_used == 4
    assert res.carried_pieces_used == 4
    assert res.extra_hose_pieces == 0


# --- Model B applies q and r -----------------------------------------------

def test_model_b_applies_q_and_r():
    cand, hyd = make_candidates([("H1", 100.0, 2000.0)])
    res = solve_model("B", cand, 1000.0, params=Params(q=10.0, r=0.058), hydrants_df=hyd)

    # Contribution = 2000 * exp(-r), which covers 1000 on its own.
    eff = 2000.0 * math.exp(-0.058)
    assert res.demand_met
    assert [s.hydrant for s in res.selected] == ["H1"]
    assert res.selected[0].effective_capacity == pytest.approx(eff)
    assert res.deployment_time == pytest.approx(100.0 / 5.0 + 10.0)


def test_model_b_selects_two_when_one_decayed_is_insufficient():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    res = solve_model("B", cand, 1000.0, params=Params(q=10.0, r=0.058), hydrants_df=hyd)

    # Each decayed contribution is below 1000, so both are needed.
    assert res.demand_met
    assert {s.hydrant for s in res.selected} == {"H1", "H2"}
    assert res.deployment_time == pytest.approx((100.0 / 5.0 + 10.0) + (200.0 / 5.0 + 10.0))


# --- Models A/B report hose pieces ------------------------------------------

def test_model_a_reports_hose_pieces_without_changing_optimisation():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    res = solve_model("A", cand, 1000.0, params=Params(q=10.0, r=0.058), hydrants_df=hyd)

    # Model A has no decay, so H1 (nearer) alone covers demand -> 1 hydrant, 4 pieces.
    assert [s.hydrant for s in res.selected] == ["H1"]
    assert res.selected[0].hose_pieces == 4       # ceil(100 / 30)
    assert res.hose_pieces_used == 4
    assert res.carried_pieces_used == 4
    assert res.extra_hose_pieces == 0


def test_model_b_reports_hose_pieces():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    res = solve_model("B", cand, 1000.0, params=Params(q=10.0, r=0.058), hydrants_df=hyd)

    # Both hydrants decayed below demand -> both selected: 4 + 7 = 11 pieces.
    assert {s.hydrant for s in res.selected} == {"H1", "H2"}
    assert res.hose_pieces_used == 11             # ceil(100/30) + ceil(200/30)
    assert res.carried_pieces_used == 11
    assert res.extra_hose_pieces == 0


def test_model_a_reports_extra_hose_pieces():
    # Four 100 m hydrants need 16 pieces, exceeding the 12 carried -> 4 extra.
    specs = [(f"H{i}", 100.0, 1000.0) for i in range(1, 5)]
    cand, hyd = make_candidates(specs)
    res = solve_model("A", cand, 4000.0, params=Params(r=0.0), hydrants_df=hyd)

    assert res.demand_met
    assert res.hose_pieces_used == 16
    assert res.carried_pieces_used == 12
    assert res.extra_hose_pieces == 4


# --- distance decay --------------------------------------------------------

def test_decayed_effective_capacity_for_b_and_c():
    params = Params(r=0.058)
    expected = 1000.0 * math.exp(-0.058 * 100.0 / 100.0)
    cand, hyd = make_candidates([("H1", 100.0, 1000.0)])
    for model in ("B", "C-soft", "C-hard"):
        res = solve_model(model, cand, 900.0, params=params, hydrants_df=hyd)
        assert res.selected[0].effective_capacity == pytest.approx(expected)
        assert res.total_effective_capacity == pytest.approx(expected)
    # Model A has no decay.
    res_a = solve_model("A", cand, 900.0, params=params, hydrants_df=hyd)
    assert res_a.selected[0].effective_capacity == pytest.approx(1000.0)


# --- result semantics ------------------------------------------------------

def test_demand_served_and_unmet_are_capped():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    res = solve_model("A", cand, 1500.0, params=Params(r=0.0), hydrants_df=hyd)

    assert res.total_effective_capacity == pytest.approx(2000.0)
    assert res.demand_served == pytest.approx(1500.0)  # min(demand, total effective)
    assert res.unmet_demand == pytest.approx(0.0)
    assert res.demand_met


def test_no_per_hydrant_flow_or_lines_fields():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0)])
    res = solve_model("B", cand, 900.0, hydrants_df=hyd)
    s = res.selected[0]
    assert not hasattr(s, "delivered_flow")
    assert not hasattr(s, "lines")
    assert not hasattr(s, "line_capacity")
    assert not hasattr(s, "line_max_flow")


# --- Model C-soft hose accounting ------------------------------------------

def test_model_c_soft_95m_needs_four_pieces():
    cand, hyd = make_candidates([("H1", 95.0, 500.0)])
    # r=0 so decay does not shrink the 500 L/min hydrant below demand.
    res = solve_model("C-soft", cand, 500.0, params=Params(r=0.0), hydrants_df=hyd)
    assert res.demand_met
    assert res.selected[0].hose_pieces == 4  # ceil(95 / 30)
    assert res.hose_pieces_used == 4
    assert res.carried_pieces_used == 4
    assert res.extra_hose_pieces == 0


def test_model_c_soft_reports_reinforcement():
    # Four hydrants at 100 m (4 pieces each) needed for 4000 -> 16 pieces total.
    specs = [(f"H{i}", 100.0, 1000.0) for i in range(1, 5)]
    cand, hyd = make_candidates(specs)
    res = solve_model("C-soft", cand, 4000.0, params=Params(r=0.0), hydrants_df=hyd)

    assert res.demand_met
    assert res.hose_pieces_used == 16            # total pieces
    assert res.carried_pieces_used == 12         # min(total, carried)
    assert res.extra_hose_pieces == 4            # 16 - 12 reinforcement
    assert isinstance(res.extra_hose_pieces, int)


def test_model_c_soft_minimises_reinforcement():
    # Two equivalent-capacity hydrants at different distances: the nearer one
    # needs fewer pieces and should be preferred when only one is required.
    cand, hyd = make_candidates([("near", 20.0, 1000.0), ("far", 100.0, 1000.0)])
    res = solve_model("C-soft", cand, 1000.0, params=Params(r=0.0), hydrants_df=hyd)

    assert res.demand_met
    assert [s.hydrant for s in res.selected] == ["near"]  # 1 piece vs 4 pieces
    assert res.hose_pieces_used == 1
    assert res.extra_hose_pieces == 0


# --- Model C-hard fixed inventory ------------------------------------------

def test_model_c_hard_reports_shortage_when_inventory_binds():
    # 4000 L/min needs four 1000 L/min hydrants (4 pieces each = 16), but only
    # 12 pieces are carried, so at most 3 hydrants (3000 L/min) fit.
    specs = [(f"H{i}", 100.0, 1000.0) for i in range(1, 5)]
    cand, hyd = make_candidates(specs)
    res = solve_model("C-hard", cand, 4000.0, params=Params(r=0.0), hydrants_df=hyd)

    assert res.demand_met is False
    assert res.hose_pieces_used == 12
    assert res.carried_pieces_used == 12
    assert res.extra_hose_pieces is None
    assert res.total_effective_capacity == pytest.approx(3000.0)
    assert res.demand_served == pytest.approx(3000.0)
    assert res.unmet_demand == pytest.approx(1000.0)


def test_model_c_hard_within_inventory():
    # Two hydrants at 100 m (4 pieces each) = 8 pieces <= 12, no shortage.
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 100.0, 1000.0)])
    res = solve_model("C-hard", cand, 2000.0, params=Params(r=0.0), hydrants_df=hyd)
    assert res.demand_met
    assert res.hose_pieces_used == 8
    assert res.extra_hose_pieces is None
    assert res.unmet_demand == pytest.approx(0.0)


# --- full-demand re-solve with committed hydrants --------------------------

def test_committed_hydrant_locked_and_counted_once():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    # H1 already selected. Full demand 1500 -> H1 (locked) + H2, each counted once.
    res = solve_model("A", cand, 1500.0, params=Params(r=0.0), hydrants_df=hyd,
                      committed={"H1"})
    assert res.demand_met
    assert {s.hydrant for s in res.selected} == {"H1", "H2"}
    assert res.total_effective_capacity == pytest.approx(2000.0)
    assert res.demand_served == pytest.approx(1500.0)


def test_committed_hydrant_alone_satisfies_full_demand():
    cand, hyd = make_candidates([("H1", 100.0, 2000.0), ("H2", 200.0, 1000.0)])
    # H1 locked with 2000 L/min capacity covers the full 1500 on its own, so
    # no second hydrant is added and nothing is double counted.
    res = solve_model("A", cand, 1500.0, params=Params(r=0.0), hydrants_df=hyd,
                      committed={"H1"})
    assert res.demand_met
    assert [s.hydrant for s in res.selected] == ["H1"]
    assert res.demand_served == pytest.approx(1500.0)


# --- best-achievable with unmet demand -------------------------------------

@pytest.mark.parametrize("model", ["A", "B", "C-soft", "C-hard"])
def test_impossible_demand_returns_best_achievable(model):
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 600.0)])
    res = solve_model(model, cand, 1_000_000.0, hydrants_df=hyd)
    assert res.demand_met is False
    assert res.unmet_demand > 0
    assert res.demand_served > 0  # best achievable supply, not zero/None


# --- helpers ---------------------------------------------------------------

def test_helpers_hydrant_flow_and_hose_pieces():
    from domain import hose_pieces, hydrant_flow

    params = Params(r=0.058)
    assert hydrant_flow(1000.0, 0.0, params, "A") == pytest.approx(1000.0)
    assert hydrant_flow(1000.0, 0.0, params, "B") == pytest.approx(1000.0)
    expected = 1000.0 * math.exp(-0.058)
    assert hydrant_flow(1000.0, 100.0, params, "B") == pytest.approx(expected)
    assert hydrant_flow(1000.0, 100.0, params, "C-soft") == pytest.approx(expected)
    assert hydrant_flow(1000.0, 100.0, params, "C-hard") == pytest.approx(expected)

    assert hose_pieces(0.0, params) == 1
    assert hose_pieces(30.0, params) == 1
    assert hose_pieces(31.0, params) == 2
    assert hose_pieces(95.0, params) == 4


def test_unknown_model_raises():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0)])
    with pytest.raises(ValueError):
        solve_model("C", cand, 1000.0, hydrants_df=hyd)
