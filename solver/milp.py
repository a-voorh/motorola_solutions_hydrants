"""Pure water-supply optimization: Models A/B/C-soft/C-hard sharing one MILP core.

No UI and no message-parsing. The four models form a clear progression:

  * Model A    -- naive baseline: nominal capacity + simple distance objective.
  * Model B    -- deployment-time: nominal capacity + route/deployment time.
  * C-soft     -- hose-aware: hose inventory + parallel lines + friction-loss
                  proxy, reinforcement allowed.
  * C-hard     -- hose-aware: same, but hard inventory limit (no reinforcement).

Variables
---------

Models A / B (one binary per candidate hydrant ``i``)::

    x_i in {0, 1}          hydrant selected (nominal capacity ``C_i``)
    u  >= 0                unmet demand (slack)

Models C-soft / C-hard (one binary per hydrant/line configuration)::

    y[i, n] in {0, 1}      use hydrant i with exactly n parallel lines
                           (n = 1 .. max_lines_per_hydrant), at most one per i
    u  >= 0                unmet demand (slack)
    o  >= 0, integer       reinforcement hose pieces (C-soft only)

Precomputed constants (so Model C stays a MILP)::

    a[i, n] = min(C_i, n * gamma / sqrt(d_i))   usable capacity (L/min)
    h_i     = max(1, ceil(d_i / HOSE_PIECE_M))  pieces for ONE line

Constraints
-----------

    sum(a_i * x_i) + u >= demand                          (A / B, a_i = C_i)
    sum_n y[i, n] <= 1                                    (C, per hydrant)
    sum_i sum_n a[i, n] * y[i, n] + u >= demand           (C)

Inventory (budget = carried_pieces - failed_pieces):

    C-soft:  sum_i sum_n n * h_i * y[i, n] <= budget + o
    C-hard:  sum_i sum_n n * h_i * y[i, n] <= budget

Lexicographic objectives (each stage minimizes one term, then pins it):

    A          min u, then min sum((d_i / v) * x_i)
    B          min u, then min sum((d_i / v + q) * x_i)
    C-soft     min u, then min o, then min effort
    C-hard     min u, then min effort

where C effort per configuration is ``n * (h_i * hose_piece_m / v + q)``.

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
    deployment_time,
    flow_tolerance,
    hose_pieces,
    usable_capacity,
)


def _tol(demand: float) -> float:
    """Tolerance for treating unmet demand as effectively zero."""
    return flow_tolerance(demand)


# --------------------------------------------------------------------------
# MILP construction
# --------------------------------------------------------------------------

def _build_milp(model, candidates, demand, params, committed, failed_pieces, committed_lines=None):
    """Return the fixed part of the MILP (bounds, constraints) + objective hooks."""
    idx = list(candidates.index)
    n = len(idx)

    D = candidates["Distance_m"].astype(float).to_numpy()
    C = candidates["Capacity_L_min"].astype(float).to_numpy()

    committed = set(committed) if committed else set()
    is_committed = np.array([h_ in committed for h_ in idx], dtype=bool)
    committed_lines = committed_lines or {}

    is_c = model in ("C-soft", "C-hard")
    has_o = model == "C-soft"

    if not is_c:
        # --- Models A / B: one binary per hydrant, nominal capacity. ---
        a = C.astype(float)
        h = np.array([hose_pieces(d, params) for d in D], dtype=float)

        X0, U0 = 0, n
        num_vars = n + 1

        lb = np.zeros(num_vars)
        ub = np.full(num_vars, np.inf)
        ub[X0:X0 + n] = 1.0
        lb[X0:X0 + n] = np.where(is_committed, 1.0, 0.0)

        integrality = np.zeros(num_vars, dtype=int)
        integrality[X0:X0 + n] = 1

        rows, cols, vals = [], [], []
        lb_rows, ub_rows = [], []

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

        A = sparse.coo_matrix((vals, (rows, cols)), shape=(len(lb_rows), num_vars)).tocsr()

        pb = {
            "idx": idx, "n": n, "is_c": False,
            "D": D, "C": C, "a": a, "h": h,
            "has_o": False, "X0": X0, "U0": U0,
            "num_vars": num_vars, "A": A,
            "lb_rows": np.array(lb_rows), "ub_rows": np.array(ub_rows),
            "bounds": Bounds(lb, ub), "integrality": integrality,
        }
        return pb

    # --- Models C-soft / C-hard: L binaries per hydrant. ---
    L = params.max_lines_per_hydrant
    h = np.array([hose_pieces(d, params) for d in D], dtype=float)

    # a[i, k] = usable capacity with n = k + 1 parallel lines.
    a = np.array(
        [[usable_capacity(C[i], D[i], params, k + 1) for k in range(L)]
         for i in range(n)],
        dtype=float,
    )

    Y0, U0 = 0, n * L
    O0 = n * L + 1
    num_vars = n * L + (2 if has_o else 1)

    lb = np.zeros(num_vars)
    ub = np.full(num_vars, np.inf)
    ub[Y0:Y0 + n * L] = 1.0

    integrality = np.zeros(num_vars, dtype=int)
    integrality[Y0:Y0 + n * L] = 1
    if has_o:
        integrality[O0] = 1

    rows, cols, vals = [], [], []
    lb_rows, ub_rows = [], []

    def _row(cols_vals, lo, hi):
        r = len(lb_rows)
        for (c, v) in cols_vals:
            rows.append(r)
            cols.append(c)
            vals.append(v)
        lb_rows.append(lo)
        ub_rows.append(hi)

    def _y(i, k):
        return Y0 + i * L + k

    # At most one configuration per hydrant (and exactly one if committed).
    # A committed hydrant must keep at least its previous number of lines.
    for i in range(n):
        _row([(_y(i, k), 1.0) for k in range(L)], -np.inf, 1.0)
        if is_committed[i]:
            _row([(_y(i, k), 1.0) for k in range(L)], 1.0, np.inf)
            prev = committed_lines.get(idx[i], 1)
            if prev > 1:
                _row([(_y(i, k), 1.0) for k in range(prev - 1)], -np.inf, 0.0)

    # sum_i sum_n a[i, n] * y[i, n] + u >= demand
    _row(
        [(_y(i, k), a[i, k]) for i in range(n) for k in range(L)] + [(U0, 1.0)],
        demand, np.inf,
    )

    budget = params.carried_pieces - failed_pieces
    # Hose consumption: sum_i sum_n n * h_i * y[i, n]  (n = k + 1).
    if has_o:
        # sum n * h_i * y - o <= budget
        _row(
            [(_y(i, k), (k + 1) * h[i]) for i in range(n) for k in range(L)] + [(O0, -1.0)],
            -np.inf, budget,
        )
    else:
        _row(
            [(_y(i, k), (k + 1) * h[i]) for i in range(n) for k in range(L)],
            -np.inf, budget,
        )

    A = sparse.coo_matrix((vals, (rows, cols)), shape=(len(lb_rows), num_vars)).tocsr()

    pb = {
        "idx": idx, "n": n, "is_c": True, "L": L,
        "D": D, "C": C, "a": a, "h": h,
        "has_o": has_o, "Y0": Y0, "U0": U0, "O0": O0,
        "num_vars": num_vars, "A": A,
        "lb_rows": np.array(lb_rows), "ub_rows": np.array(ub_rows),
        "bounds": Bounds(lb, ub), "integrality": integrality,
    }
    return pb


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
    if not pb["is_c"]:
        X0 = pb["X0"]
        if model == "A":
            c[X0:X0 + n] = D / params.v
        else:  # B
            c[X0:X0 + n] = D / params.v + params.q
        return c
    # C-soft / C-hard
    Y0 = pb["Y0"]
    L = pb["L"]
    for i in range(n):
        for k in range(L):
            c[Y0 + i * L + k] = deployment_time(D[i], params, n_lines=k + 1)
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


def _solve_model_raw(model, candidates, demand, params, committed, failed_pieces, committed_lines=None):
    """Return (pb, res) for the full demand with committed hydrants locked."""
    pb = _build_milp(model, candidates, demand, params, committed, failed_pieces, committed_lines)

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
                committed=None, failed_pieces=0, radius=None, distance_method="gis",
                committed_lines=None):
    """Solve one model and return a :class:`ModelResult`.

    ``candidates`` is a DataFrame indexed by ``Hydrant`` with columns
    ``Distance_m`` and ``Capacity_L_min`` (must include any committed hydrants
    so they can be locked). ``committed`` is an iterable of hydrant ids already
    selected that must stay selected; ``committed_lines`` maps those ids to the
    number of parallel lines they already have deployed (C models keep at least
    that many). ``failed_pieces`` is the total hose pieces still committed from
    failed hydrants (C models).
    """
    if model not in MODEL_NAMES:
        raise ValueError(f"Unknown model {model!r}; expected one of {MODEL_NAMES}")
    if params is None:
        params = Params()
    if len(candidates) == 0:
        return _empty_result(model, demand, params, radius, distance_method)

    pb, res = _solve_model_raw(model, candidates, demand, params, committed, failed_pieces, committed_lines)

    x = res.x
    n = pb["n"]
    idx = pb["idx"]

    lat_map = {}
    lon_map = {}
    if hydrants_df is not None:
        hyd = hydrants_df.set_index("Hydrant")
        lat_map = hyd["Latitude"].to_dict()
        lon_map = hyd["Longitude"].to_dict()

    selected = []
    if not pb["is_c"]:
        x_vals = np.rint(x[pb["X0"]:pb["X0"] + n]).astype(int)
        for i, hid in enumerate(idx):
            if x_vals[i] <= 0:
                continue
            h_i = int(pb["h"][i])
            selected.append(HydrantLine(
                hydrant=str(hid),
                latitude=lat_map.get(hid, float("nan")),
                longitude=lon_map.get(hid, float("nan")),
                distance_m=float(pb["D"][i]),
                nominal_capacity=float(pb["C"][i]),
                effective_capacity=float(pb["a"][i]),
                hose_pieces=h_i,
                lines=1,
                hose_pieces_total=h_i,
            ))
    else:
        L = pb["L"]
        Y0 = pb["Y0"]
        for i, hid in enumerate(idx):
            y_vals = np.rint(x[Y0 + i * L:Y0 + i * L + L]).astype(int)
            n_lines = int(y_vals.sum())
            if n_lines == 0:
                continue
            k = int(np.argmax(y_vals))  # the selected configuration index
            lines = k + 1
            h_i = int(pb["h"][i])
            selected.append(HydrantLine(
                hydrant=str(hid),
                latitude=lat_map.get(hid, float("nan")),
                longitude=lon_map.get(hid, float("nan")),
                distance_m=float(pb["D"][i]),
                nominal_capacity=float(pb["C"][i]),
                effective_capacity=float(pb["a"][i, k]),
                hose_pieces=h_i,
                lines=lines,
                hose_pieces_total=lines * h_i,
            ))

    total_nominal = sum(s.nominal_capacity for s in selected)
    total_effective = sum(s.effective_capacity for s in selected)
    demand_served = min(float(demand), total_effective)
    unmet = max(0.0, float(demand) - total_effective)

    total_pieces = int(sum((s.hose_pieces_total if s.hose_pieces_total is not None else 0)
                           for s in selected))
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
        if s.lines and s.lines > 1:
            piece += f" ({s.lines} lines)"
        if s.hose_pieces_total is not None:
            piece += f" ({s.hose_pieces_total} pieces)"
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
