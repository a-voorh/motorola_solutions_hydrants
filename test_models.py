"""Deterministic tests for the A/B/C-soft/C-hard water-supply models (no Streamlit).

Model progression under test:

  * A       -- naive baseline: nominal capacity + simple distance objective.
  * B       -- deployment-time: nominal capacity + route/setup time.
  * C-soft  -- hose-aware: inventory + parallel lines + friction-loss proxy,
               reinforcement allowed.
  * C-hard  -- hose-aware: same, with a hard inventory limit.
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


# --- 1. Model A retains its current behaviour ------------------------------

def test_model_a_uses_nominal_capacity_and_distance_objective():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    # q and gamma must not affect A: full nominal capacity, objective = d / v.
    res = solve_model("A", cand, 1000.0, params=Params(q=10.0, gamma=0.001), hydrants_df=hyd)

    assert res.demand_met
    assert [s.hydrant for s in res.selected] == ["H1"]
    assert res.selected[0].effective_capacity == pytest.approx(1000.0)
    assert res.demand_served == pytest.approx(1000.0)
    assert res.deployment_time == pytest.approx(100.0 / 5.0)
    # H1 at 100 m -> ceil(100 / 15) = 7 pieces (one line).
    assert res.selected[0].hose_pieces == 7
    assert res.selected[0].lines == 1


# --- 2. Model B uses nominal capacity (no exponential decay) ----------------

def test_model_b_uses_nominal_capacity_no_decay():
    cand, hyd = make_candidates([("H1", 100.0, 2000.0)])
    # A tiny gamma must NOT shrink B's contribution (B has no decay/friction).
    res = solve_model("B", cand, 1500.0, params=Params(q=10.0, gamma=0.001), hydrants_df=hyd)

    assert res.demand_met
    assert [s.hydrant for s in res.selected] == ["H1"]
    assert res.selected[0].effective_capacity == pytest.approx(2000.0)


# --- 3. Model B accounts for deployment/setup time --------------------------

def test_model_b_accounts_for_deployment_and_setup_time():
    cand, hyd = make_candidates([("H1", 100.0, 2000.0)])
    res = solve_model("B", cand, 1000.0, params=Params(v=5.0, q=10.0), hydrants_df=hyd)

    assert res.demand_met
    assert res.deployment_time == pytest.approx(100.0 / 5.0 + 10.0)


# --- 4. Hose-piece counts use HOSE_PIECE_M (15 m), not a hard-coded 30 m ----

def test_hose_pieces_use_15m_piece_length():
    from domain import hose_pieces

    params = Params()
    assert params.hose_piece_m == 15.0
    assert hose_pieces(15.0, params) == 1
    assert hose_pieces(16.0, params) == 2
    assert hose_pieces(30.0, params) == 2
    assert hose_pieces(95.0, params) == 7
    assert hose_pieces(100.0, params) == 7


def test_model_reports_hose_pieces_with_15m_pieces():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0)])
    res = solve_model("B", cand, 1000.0, params=Params(q=10.0), hydrants_df=hyd)
    assert res.selected[0].hose_pieces == 7  # ceil(100 / 15)


# --- 5/6/7. Model C usable-capacity helper semantics ------------------------

def test_usable_capacity_decreases_with_distance():
    from domain import usable_capacity

    params = Params(gamma=10000.0)
    near = usable_capacity(2000.0, 100.0, params, 1)
    far = usable_capacity(2000.0, 400.0, params, 1)
    assert near > far


def test_usable_capacity_increases_with_parallel_lines():
    from domain import usable_capacity

    params = Params(gamma=10000.0)
    one = usable_capacity(2000.0, 100.0, params, 1)
    two = usable_capacity(2000.0, 100.0, params, 2)
    assert two > one


def test_usable_capacity_never_exceeds_nominal_capacity():
    from domain import usable_capacity

    params = Params(gamma=1e9)
    for n in (1, 2, 5):
        assert usable_capacity(1200.0, 100.0, params, n) == pytest.approx(1200.0)


# --- 8. Two lines consume twice as many hose pieces -------------------------

def test_two_lines_consume_twice_the_hose_pieces():
    # 2000 L/min nominal at 100 m: one line gives 1000 (gamma=10000), two give
    # the full 2000, so C-soft must select two lines to meet the 2000 demand.
    cand, hyd = make_candidates([("H1", 100.0, 2000.0)])
    res = solve_model("C-soft", cand, 2000.0, params=Params(gamma=10000.0), hydrants_df=hyd)

    assert res.demand_met
    s = res.selected[0]
    assert s.lines == 2
    assert s.hose_pieces == 7
    assert s.hose_pieces_total == 14
    assert s.hose_pieces_total == 2 * s.hose_pieces


# --- 9. C-hard never exceeds available hose inventory -----------------------

def test_c_hard_never_exceeds_inventory():
    # Five 1000 L/min hydrants at 100 m (7 pieces each). Carried = 30 pieces,
    # so at most four hydrants (28 pieces) fit -> 1000 L/min unmet.
    specs = [(f"H{i}", 100.0, 1000.0) for i in range(1, 6)]
    cand, hyd = make_candidates(specs)
    res = solve_model("C-hard", cand, 5000.0, params=Params(gamma=10000.0), hydrants_df=hyd)

    assert res.demand_met is False
    assert res.hose_pieces_used <= 30
    assert res.hose_pieces_used == 28  # 4 hydrants * 7 pieces
    assert res.carried_pieces_used == 28
    assert res.extra_hose_pieces is None
    assert res.total_effective_capacity == pytest.approx(4000.0)
    assert res.unmet_demand == pytest.approx(1000.0)


# --- 10. C-soft can request reinforcement hose ------------------------------

def test_c_soft_requests_reinforcement():
    # Five 1000 L/min hydrants at 100 m (7 pieces each) = 35 pieces needed,
    # but only 30 carried -> 5 reinforcement pieces.
    specs = [(f"H{i}", 100.0, 1000.0) for i in range(1, 6)]
    cand, hyd = make_candidates(specs)
    res = solve_model("C-soft", cand, 5000.0, params=Params(gamma=10000.0), hydrants_df=hyd)

    assert res.demand_met
    assert res.hose_pieces_used == 35
    assert res.carried_pieces_used == 30
    assert res.extra_hose_pieces == 5
    assert isinstance(res.extra_hose_pieces, int)


# --- 11. Changing gamma changes the optimal configuration -------------------

def test_changing_gamma_changes_optimal_line_configuration():
    cand, hyd = make_candidates([("H1", 100.0, 1200.0)])

    # High gamma: one line reaches nominal 1200 -> one line suffices.
    hi = solve_model("C-soft", cand, 1000.0, params=Params(gamma=12000.0), hydrants_df=hyd)
    assert hi.demand_met
    assert hi.selected[0].lines == 1

    # Low gamma: one line only delivers 600 < 1000 -> two lines are required.
    lo = solve_model("C-soft", cand, 1000.0, params=Params(gamma=6000.0), hydrants_df=hyd)
    assert lo.demand_met
    assert lo.selected[0].lines == 2


# --- 12. Failed/reserved-hose behaviour still works -------------------------

def test_failed_pieces_reduce_available_inventory():
    # Three 1000 L/min hydrants at 100 m (7 pieces each). With 10 failed pieces
    # reserved the budget is 20 -> only two hydrants (14 pieces) fit.
    specs = [(f"H{i}", 100.0, 1000.0) for i in range(1, 4)]
    cand, hyd = make_candidates(specs)

    full = solve_model("C-hard", cand, 3000.0, params=Params(gamma=10000.0), hydrants_df=hyd)
    assert full.demand_met  # 21 pieces <= 30 budget

    degraded = solve_model("C-hard", cand, 3000.0, params=Params(gamma=10000.0),
                           hydrants_df=hyd, failed_pieces=10)
    assert degraded.demand_met is False
    assert degraded.hose_pieces_used == 14  # <= 30 - 10
    assert degraded.unmet_demand == pytest.approx(1000.0)


def test_committed_hydrant_stays_locked():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 1000.0)])
    res = solve_model("A", cand, 1500.0, params=Params(), hydrants_df=hyd, committed={"H1"})
    assert res.demand_met
    assert "H1" in {s.hydrant for s in res.selected}


# --- best-achievable with unmet demand -------------------------------------

@pytest.mark.parametrize("model", ["A", "B", "C-soft", "C-hard"])
def test_impossible_demand_returns_best_achievable(model):
    cand, hyd = make_candidates([("H1", 100.0, 1000.0), ("H2", 200.0, 600.0)])
    res = solve_model(model, cand, 1_000_000.0, hydrants_df=hyd)
    assert res.demand_met is False
    assert res.unmet_demand > 0
    assert res.demand_served > 0  # best achievable supply, not zero/None


def test_unknown_model_raises():
    cand, hyd = make_candidates([("H1", 100.0, 1000.0)])
    with pytest.raises(ValueError):
        solve_model("C", cand, 1000.0, hydrants_df=hyd)
