"""Pure water-supply optimization: Models A/B/C-soft/C-hard sharing one MILP core.

No UI and no message-parsing. Each selected hydrant uses one connection and
contributes its full modelled capacity, subject only to distance decay where
applicable.

Variables (for each candidate hydrant ``i``)::

    x_i in {0, 1}          hydrant selected (one connection)
    u  >= 0                unmet demand (slack)
    o  >= 0, integer       reinforcement hose pieces (Model C-soft only)

Shared constraint::

    sum(a_i * x_i) + u >= demand
    where a_i = C_i                         for Model A
              = C_i * exp(-r * D_i / 100)   for Models B / C-soft / C-hard

Inventory constraints (h_i = max(1, ceil(D_i / 30)), N = carried pieces):

    C-soft:  sum(h_i * x_i) <= (N - failed_pieces) + o
    C-hard:  sum(h_i * x_i) <=  N - failed_pieces

Models A and B impose no inventory constraint, but every model reports hose needs
to the dispatcher (``hose_pieces_used`` = total, ``carried_pieces_used`` =
min(total, budget), ``extra_hose_pieces`` = max(0, total - budget); C-hard leaves
``extra_hose_pieces`` as ``None``).

Lexicographic objectives:
  A          min u, then min sum((D_i / v) * x_i)
  B          min u, then min sum((D_i / v + q) * x_i)
  C-soft     min u, then min o, then min sum((30 * h_i / v + q) * x_i)
  C-hard     min u, then min sum((30 * h_i / v + q) * x_i)

The MILP is always feasible because ``u`` absorbs any shortfall, so every model
returns a best-achievable plan (possibly with positive unmet demand).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp

from domain import (
    MODEL_NAMES,
    HydrantLine,
    ModelResult,
    Params,
    flow_tolerance,
    hose_pieces,
    hydrant_flow,
)


def _tol(demand: float) -> float:
    """Tolerance for treating unmet demand as effectively zero."""
    return flow_tolerance(demand)


# --------------------------------------------------------------------------
# MILP construction
# --------------------------------------------------------------------------

def _build_milp(model, candidates, demand, params, committed, failed_pieces):
    """Return the fixed part of the MILP (bounds, constraints) + objective hooks."""
    idx = list(candidates.index)
    n = len(idx)

    D = candidates["Distance_m"].astype(float).to_numpy()
    C = candidates["Capacity_L_min"].astype(float).to_numpy()

    a = np.array([hydrant_flow(c, d, params, model) for c, d in zip(C, D)], dtype=float)
    h = np.array([hose_pieces(d, params) for d in D], dtype=float)

    committed = set(committed) if committed else set()
    is_committed = np.array([h_ in committed for h_ in idx], dtype=bool)

    # Variable layout: [x (n), u (1), o (1, C-soft only)]
    has_o = model == "C-soft"
    X0, U0 = 0, n
    O0 = n + 1
    num_vars = n + (2 if has_o else 1)

    # Bounds
    lb = np.zeros(num_vars)
    ub = np.full(num_vars, np.inf)
    ub[X0:X0 + n] = 1.0
    lb[X0:X0 + n] = np.where(is_committed, 1.0, 0.0)

    # Integrality
    integrality = np.zeros(num_vars, dtype=int)
    integrality[X0:X0 + n] = 1
    if has_o:
        integrality[O0] = 1

    # Sparse constraint matrix (rows x num_vars)
    rows = []
    cols = []
    vals = []
    lb_rows = []
    ub_rows = []

    def _row(cols_vals, lo, hi):
        r = len(lb_rows)
        for (c, v) in cols_vals:
            rows.append(r)
            cols.append(c)
            vals.append(v)
        lb_rows.append(lo)
        ub_rows.append(hi)

    # sum(a_i * x_i) + u >= demand
    _row([(X0 + i, a[i]) for i in range(n)] + [(U0, 1.0)], demand, np.inf)

    budget = params.carried_pieces - failed_pieces
    if has_o:
        # sum(h_i * x_i) - o <= budget
        _row([(X0 + i, h[i]) for i in range(n)] + [(O0, -1.0)], -np.inf, budget)
    elif model == "C-hard":
        # sum(h_i * x_i) <= budget
        _row([(X0 + i, h[i]) for i in range(n)], -np.inf, budget)

    A = sparse.coo_matrix((vals, (rows, cols)), shape=(len(lb_rows), num_vars)).tocsr()
    bounds = Bounds(lb, ub)

    return {
        "idx": idx,
        "n": n,
        "D": D,
        "C": C,
        "a": a,
        "h": h,
        "has_o": has_o,
        "X0": X0, "U0": U0, "O0": O0,
        "num_vars": num_vars,
        "A": A,
        "lb_rows": np.array(lb_rows),
        "ub_rows": np.array(ub_rows),
        "bounds": bounds,
        "integrality": integrality,
    }


def _objective(pb, stage):
    """Objective coefficient vector for stage 'u' or 'o'."""
    c = np.zeros(pb["num_vars"])
    if stage == "u":
        c[pb["U0"]] = 1.0
    elif stage == "o":
        c[pb["O0"]] = 1.0
    else:
        raise ValueError(f"unknown stage {stage!r}")
    return c


def _deploy_objective(pb, params, model):
    c = np.zeros(pb["num_vars"])
    n = pb["n"]
    D = pb["D"]
    h = pb["h"]
    if model == "A":
        c[pb["X0"]:pb["X0"] + n] = D / params.v
    elif model == "B":
        c[pb["X0"]:pb["X0"] + n] = D / params.v + params.q
    else:  # C-soft / C-hard
        c[pb["X0"]:pb["X0"] + n] = params.hose_piece_m * h / params.v + params.q
    return c


def _solve_stage(pb, c, extra_ub=None):
    """Solve one stage, optionally pinning u/o to their previous optima."""
    A = pb["A"]
    lb = pb["lb_rows"]
    ub = pb["ub_rows"]
    if extra_ub is not None:
        A = sparse.vstack([A, extra_ub[0]])
        lb = np.concatenate([lb, np.atleast_1d(extra_ub[1]).astype(float)])
        ub = np.concatenate([ub, np.atleast_1d(extra_ub[2]).astype(float)])

    constraints = [LinearConstraint(A, lb=lb, ub=ub)]
    res = milp(
        c=c,
        constraints=constraints,
        integrality=pb["integrality"],
        bounds=pb["bounds"],
        options={"disp": False},
    )
    return res


def _pin_row(pb, var_index, value, tol=0.0):
    """Extra constraint row pinning ``var_index <= value + tol``."""
    row = sparse.csr_matrix(
        ([1.0], ([0], [var_index])), shape=(1, pb["num_vars"])
    )
    return row, -np.inf, value + tol


def _stack_pins(pb, *pins):
    """Stack several single-row upper-bound pins into one constraint block."""
    rows = sparse.vstack([p[0] for p in pins])
    lb = np.array([p[1] for p in pins])
    ub = np.array([p[2] for p in pins])
    return rows, lb, ub


def _solve_model_raw(model, candidates, demand, params, committed, failed_pieces):
    """Return (pb, res) for the full demand with committed hydrants locked."""
    pb = _build_milp(model, candidates, demand, params, committed, failed_pieces)

    tol = _tol(demand)

    res1 = _solve_stage(pb, _objective(pb, "u"))
    if res1.status != 0:
        raise RuntimeError(f"Model {model} stage 1 failed (status {res1.status})")
    u_star = res1.x[pb["U0"]]

    pin_u = _pin_row(pb, pb["U0"], u_star, tol)

    if model == "C-soft":
        res2 = _solve_stage(pb, _objective(pb, "o"), extra_ub=pin_u)
        if res2.status != 0:
            raise RuntimeError(f"Model {model} stage 2 failed (status {res2.status})")
        o_star = res2.x[pb["O0"]]
        pins = _stack_pins(pb, pin_u, _pin_row(pb, pb["O0"], o_star, 0.0))
        res = _solve_stage(pb, _deploy_objective(pb, params, model), extra_ub=pins)
    else:
        res = _solve_stage(pb, _deploy_objective(pb, params, model), extra_ub=pin_u)

    if res.status != 0:
        raise RuntimeError(f"Model {model} final stage failed (status {res.status})")

    return pb, res


# --------------------------------------------------------------------------
# Dispatcher + result assembly
# --------------------------------------------------------------------------

def solve_model(model, candidates, demand, params=None, hydrants_df=None,
                committed=None, failed_pieces=0, radius=None, distance_method="gis"):
    """Solve one model and return a :class:`ModelResult`.

    ``candidates`` is a DataFrame indexed by ``Hydrant`` with columns
    ``Distance_m`` and ``Capacity_L_min`` (must include any committed hydrants
    so they can be locked). ``committed`` is an iterable of hydrant ids already
    selected that must stay selected (locked to ``x_i = 1``); ``failed_pieces``
    is the total hose pieces still committed from failed hydrants (C models).
    """
    if model not in MODEL_NAMES:
        raise ValueError(f"Unknown model {model!r}; expected one of {MODEL_NAMES}")
    if params is None:
        params = Params()
    if len(candidates) == 0:
        return _empty_result(model, demand, params, radius, distance_method)

    pb, res = _solve_model_raw(model, candidates, demand, params, committed, failed_pieces)

    x = res.x
    n = pb["n"]
    idx = pb["idx"]
    x_vals = np.rint(x[pb["X0"]:pb["X0"] + n]).astype(int)

    lat_map = {}
    lon_map = {}
    if hydrants_df is not None:
        hyd = hydrants_df.set_index("Hydrant")
        lat_map = hyd["Latitude"].to_dict()
        lon_map = hyd["Longitude"].to_dict()

    selected = []
    for i, hid in enumerate(idx):
        if x_vals[i] <= 0:
            continue
        selected.append(HydrantLine(
            hydrant=str(hid),
            latitude=lat_map.get(hid, float("nan")),
            longitude=lon_map.get(hid, float("nan")),
            distance_m=float(pb["D"][i]),
            nominal_capacity=float(pb["C"][i]),
            effective_capacity=float(pb["a"][i]),
            hose_pieces=int(pb["h"][i]),
        ))

    total_nominal = sum(s.nominal_capacity for s in selected)
    total_effective = sum(s.effective_capacity for s in selected)
    demand_served = min(float(demand), total_effective)
    unmet = max(0.0, float(demand) - total_effective)

    total_pieces = int(sum(s.hose_pieces for s in selected))
    budget = params.carried_pieces - failed_pieces
    if model == "C-hard":
        hose_pieces_used = total_pieces
        carried_pieces_used = total_pieces
        extra_hose_pieces = None
    else:  # A, B, C-soft
        hose_pieces_used = total_pieces
        carried_pieces_used = min(total_pieces, budget)
        extra_hose_pieces = max(0, total_pieces - budget)

    result = ModelResult(
        model=model,
        demand=float(demand),
        demand_served=demand_served,
        unmet_demand=unmet,
        demand_met=unmet <= _tol(demand),
        total_nominal_capacity=float(total_nominal),
        total_effective_capacity=float(total_effective),
        deployment_time=float(res.fun),
        hose_pieces_used=hose_pieces_used,
        carried_pieces_used=carried_pieces_used,
        extra_hose_pieces=extra_hose_pieces,
        radius=radius,
        distance_method=distance_method,
        selected=selected,
    )
    result.recommendation = build_recommendation(result)
    return result


def _empty_result(model, demand, params, radius, distance_method):
    result = ModelResult(
        model=model,
        demand=float(demand),
        demand_served=0.0,
        unmet_demand=float(demand),
        demand_met=False,
        total_nominal_capacity=0.0,
        total_effective_capacity=0.0,
        deployment_time=0.0,
        hose_pieces_used=None,
        carried_pieces_used=None,
        extra_hose_pieces=None,
        radius=radius,
        distance_method=distance_method,
        selected=[],
    )
    result.recommendation = build_recommendation(result)
    return result


# --------------------------------------------------------------------------
# Recommendation
# --------------------------------------------------------------------------

def build_recommendation(result):
    if not result.selected:
        return (
            f"No hydrants can be deployed for the {result.demand:g} L/min planning target; "
            f"{result.unmet_demand:g} L/min short of target."
        )

    parts = []
    for s in result.selected:
        piece = f"{s.hydrant}: {s.effective_capacity:.0f} L/min"
        if s.hose_pieces is not None:
            piece += f" ({s.hose_pieces} pieces)"
        parts.append(piece)
    hydrants = "; ".join(parts)

    txt = f"Deploy {hydrants}. Serves {result.demand_served:g} L/min"
    if result.demand_met:
        txt += " (planning target met)."
    else:
        txt += f" of the {result.demand:g} L/min planning target; {result.unmet_demand:g} L/min short."

    if result.hose_pieces_used is not None:
        txt += f" Uses {result.hose_pieces_used} hose pieces"
        if result.extra_hose_pieces:
            txt += f", {result.extra_hose_pieces} extra (reinforcement)."
        else:
            txt += " (within carried stock)."

    txt += f" Deployment time ≈ {result.deployment_time:.2f} units."
    return txt
