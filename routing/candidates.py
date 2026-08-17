"""Candidate-set building: radius sweep + distance-method dispatch.

``build_candidates`` produces the shared candidate set used by every model;
``_nearby`` dispatches to the Manhattan / geodesic / network distance functions;
``_ensure_committed`` keeps already-deployed hydrants in the candidate set so
their lines can be frozen on update.
"""

import pandas as pd

from domain import (
    DEFAULT_MAX_RADIUS,
    DEFAULT_RADIUS_STEP,
    DEFAULT_START_RADIUS,
    Params,
    max_usable_capacity,
)

from routing.geodesic import nearby_hydrants_geodesic
from routing.manhattan import nearby_hydrants


def _nearby(lat, lon, radius_m, hydrants_df, distance_method="gis", max_results=None, graph=None):
    """Dispatch to the network, geodesic, or Manhattan candidate search."""
    if distance_method == "network":
        from routing.network import nearby_hydrants_network
        if graph is None:
            raise ValueError("graph is required for network routing")
        return nearby_hydrants_network(lat, lon, radius_m, hydrants_df, graph, max_results=max_results)
    if distance_method == "gis":
        return nearby_hydrants_geodesic(lat, lon, radius_m, hydrants_df, max_results=max_results)
    return nearby_hydrants(lat, lon, radius_m, hydrants_df, max_results=max_results)


def _max_deliverable_sum(candidates, params, model="B"):
    """Sum of candidate capacity used as the radius-sweep feasibility bound.

    For Models A / B the deliverable is nominal capacity. For the friction-limited
    C models it is each hydrant's maximum usable capacity over the allowed line
    configurations (``max_n a[i, n]``), so farther hydrants are not prematurely
    excluded. This is an upper bound: with a tight hose inventory not every
    hydrant can run its maximum number of lines simultaneously.
    """
    if model in ("C-soft", "C-hard"):
        total = 0.0
        for cap, d in zip(candidates["Capacity_L_min"], candidates["Distance_m"]):
            total += max_usable_capacity(cap, d, params)
        return total
    return float(candidates["Capacity_L_min"].sum())


def build_candidates(lat, lon, demand, hydrants_df,
                     start_radius=DEFAULT_START_RADIUS,
                     radius_step=DEFAULT_RADIUS_STEP,
                     max_radius=DEFAULT_MAX_RADIUS,
                     params=None,
                     distance_method="gis",
                     model="B",
                     graph=None):
    """Sweep radii and return ``(radius, candidates, sufficient)``.

    Finds the smallest radius whose sum of candidate capacity (per ``model``:
    nominal for A/B, max-usable for C) covers ``demand``. If none does within
    ``max_radius``, returns the max-radius candidate set with
    ``sufficient=False`` (so the solver can still return a best-achievable
    plan with positive unmet demand).
    """
    params = params or Params()

    if distance_method == "network":
        from routing.network import nearby_hydrants_network
        if graph is None:
            raise ValueError("graph is required for network routing")
        # +200 m margin: hydrants beyond max_radius are never needed.
        full = nearby_hydrants_network(lat, lon, max_radius + 200, hydrants_df, graph)
        radius = start_radius
        while radius <= max_radius:
            within = full[full["Distance_m"] <= radius]
            if _max_deliverable_sum(within, params, model) >= demand:
                return radius, within, True
            radius += radius_step
        at_max = full[full["Distance_m"] <= max_radius]
        return max_radius, at_max, False

    radius = start_radius
    while radius <= max_radius:
        near = _nearby(lat, lon, radius, hydrants_df, distance_method)
        if _max_deliverable_sum(near, params, model) >= demand:
            return radius, near, True
        radius += radius_step
    at_max = _nearby(lat, lon, max_radius, hydrants_df, distance_method)
    return max_radius, at_max, False


def _ensure_committed(candidates, committed, result):
    """Make sure every committed (surviving) hydrant is in ``candidates``.

    Committed hydrants were selected within a previous radius, so they are near
    the incident; this just guards against a smaller gap-sweep radius dropping
    them (they must still be lockable).
    """
    if result is None or not committed:
        return candidates
    missing = [h for h in committed if h not in candidates.index]
    if not missing:
        return candidates
    sel = {s.hydrant: s for s in result.selected}
    rows = {
        h: {"Distance_m": sel[h].distance_m, "Capacity_L_min": sel[h].nominal_capacity}
        for h in missing if h in sel
    }
    if rows:
        extra = pd.DataFrame(rows).T
        extra.index.name = "Hydrant"
        candidates = pd.concat([candidates, extra])
    return candidates
