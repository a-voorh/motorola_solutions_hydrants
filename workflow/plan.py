"""Plan state assembly and rendering helpers (pure, no Streamlit)."""

from domain import DEFAULT_Q, DEFAULT_V


def _make_selected_info(candidates, ids):
    """Build the {id: {capacity, distance}} map from a candidates table."""
    return {
        h: {
            "capacity": float(candidates.loc[h, "Capacity_L_min"]),
            "distance": float(candidates.loc[h, "Distance_m"]),
        }
        for h in ids
    }


def _capacity_of(selected):
    """Nominal (synthetic) capacity of the selected hydrants."""
    return sum(info["capacity"] for info in selected.values())


def _plan_objective(selected, v=DEFAULT_V, q=DEFAULT_Q):
    """Full-plan deployment time = sum(distance/v + q) over selected."""
    return sum(info["distance"] / v + q for info in selected.values())


def _candidate_pool(plan, hydrants_df):
    """Hydrants not yet selected and not unavailable."""
    excl = set(plan["unavailable"]) | set(plan["selected"].keys())
    return hydrants_df[~hydrants_df["Hydrant"].isin(excl)]


def _plan_summary_text(plan):
    """One-line plan summary for the chat log."""
    res = plan.get("result")
    if res is not None:
        base = (f"model {res.model}; selected {[s.hydrant for s in res.selected]}; "
                f"serves {res.demand_served:g} L/min")
        stated = plan.get("stated_minimum_flow_l_min")
        if stated is not None:
            target = plan.get("planning_target_flow_l_min")
            base += f"; min {stated:g}, target {target:g} L/min"
            if not plan.get("minimum_met"):
                base += (f" (minimum not met, short "
                         f"{plan['operational_shortfall_l_min']:g} L/min)")
            elif not plan.get("target_met"):
                base += (f" (target shortfall "
                         f"{plan['planning_target_shortfall_l_min']:g} L/min)")
        else:
            base += f"; target {res.demand:g} L/min"
        return base
    demand = "unknown" if plan["effective_demand"] is None else f"{plan['effective_demand']:g} L/min"
    selected = list(plan["selected"].keys())
    nom = _capacity_of(plan["selected"])
    return f"demand {demand}; selected {selected} (nominal {nom:g} L/min)"


def _plan_from_result(lat, lon, demand, result, params, unavailable, flow=None):
    """Assemble the plan dict from a :class:`ModelResult`."""
    plan = {
        "location": (lat, lon),
        "effective_demand": demand,
        "model": result.model,
        "result": result,
        "selected": {
            s.hydrant: {"capacity": s.nominal_capacity, "distance": s.distance_m}
            for s in result.selected
        },
        "unavailable": list(unavailable),
        "objective": result.deployment_time,
        "radius": result.radius,
        "insufficient": not result.demand_met,
        "distance_method": result.distance_method,
        "committed_pieces": result.hose_pieces_used,
        "params": params,
    }
    if flow:
        plan.update(flow)
    return plan
