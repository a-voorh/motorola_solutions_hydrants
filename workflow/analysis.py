"""Workflow use cases: analyse an incident, apply updates, and compare models.

This module depends on interfaces (``domain``, ``extraction``, ``solver``,
``routing``) but never on Streamlit.
"""

import copy

import pandas as pd

from domain import (
    DEFAULT_CANDIDATE_MARGIN_M,
    DEFAULT_MAX_RADIUS,
    DEFAULT_PLANNING_RESERVE_PERCENT,
    DEFAULT_RADIUS_EXTENSION_M,
    DEFAULT_RADIUS_STEP,
    DEFAULT_START_RADIUS,
    MODEL_NAMES,
    IncidentRequest,
    Params,
    flow_tolerance,
)
from extraction import detect_update, extract_flow
from routing import _ensure_committed, _nearby, build_candidates
from solver import build_recommendation, solve_model
from workflow.compare import compare_models
from workflow.plan import (
    _candidate_pool,
    _make_selected_info,
    _plan_from_result,
    _plan_objective,
    _plan_summary_text,
)
from workflow.planning import planning_target_flow, summarize_flow


def analyse_incident(request, hydrants_df, model="B", params=None, *,
                     start_radius=DEFAULT_START_RADIUS,
                     radius_step=DEFAULT_RADIUS_STEP,
                     max_radius=DEFAULT_MAX_RADIUS,
                     candidate_margin=DEFAULT_CANDIDATE_MARGIN_M,
                     distance_method="gis", graph=None):
    """Analyse one incident request -> (plan, event, comparison).

    ``request`` carries the raw transcript, location, and planning-reserve
    percentage. The stated minimum flow is extracted from the transcript here.
    ``candidate_margin`` keeps extra nearby hydrants in the candidate pool past
    the covering radius (for the dispatcher to force-include later).
    """
    if model not in MODEL_NAMES:
        raise ValueError(f"Unknown model {model!r}")
    params = params or Params()

    transcript = request.transcript
    lat, lon = request.location
    reserve = request.planning_reserve_percent

    flow, stated = extract_flow(transcript)

    if not stated:
        nearest = _nearby(lat, lon, 1e9, hydrants_df, distance_method, max_results=1, graph=graph)
        selected = _make_selected_info(nearest, list(nearest.index)) if not nearest.empty else {}
        plan = {
            "location": (lat, lon),
            "effective_demand": None,
            "model": model,
            "result": None,
            "selected": selected,
            "unavailable": [],
            "objective": _plan_objective(selected, params.v, params.q) if selected else None,
            "radius": None,
            "insufficient": False,
            "distance_method": distance_method,
            "committed_pieces": 0,
            "params": params,
            "planning_reserve_percent": reserve,
            "transcript": transcript,
        }
        event = {"kind": "initial", "message": transcript, "flow": None,
                 "hydrant": None, "retained": None, "added": None,
                 "summary": _plan_summary_text(plan)}
        return plan, event, []

    demand = planning_target_flow(flow, reserve)
    radius, candidates, sufficient = build_candidates(
        lat, lon, demand, hydrants_df, start_radius, radius_step, max_radius,
        params, distance_method, model, graph, candidate_margin,
    )
    result = solve_model(model, candidates, demand, params, hydrants_df,
                         radius=radius, distance_method=distance_method)
    flow_summary = summarize_flow(flow, reserve, result.demand_served)
    plan = _plan_from_result(lat, lon, demand, result, params, [], flow=flow_summary)
    plan["candidates"] = candidates
    plan["transcript"] = transcript

    comparison = compare_models(candidates, demand, params, hydrants_df,
                                radius=radius, distance_method=distance_method)

    event = {"kind": "initial", "message": transcript, "flow": flow,
             "hydrant": None, "retained": None, "added": None,
             "summary": _plan_summary_text(plan)}
    return plan, event, comparison


def run_initial_analysis(lat, lon, transcript, hydrants_df, model="B",
                         params=None,
                         start_radius=DEFAULT_START_RADIUS,
                         radius_step=DEFAULT_RADIUS_STEP,
                         max_radius=DEFAULT_MAX_RADIUS,
                         planning_reserve_percent=DEFAULT_PLANNING_RESERVE_PERCENT,
                         distance_method="gis",
                         graph=None):
    """Backwards-compatible wrapper: analyse a transcript + location -> (plan, event)."""
    request = IncidentRequest(transcript=transcript, location=(lat, lon),
                              planning_reserve_percent=planning_reserve_percent)
    plan, event, _comparison = analyse_incident(
        request, hydrants_df, model, params,
        start_radius=start_radius, radius_step=radius_step, max_radius=max_radius,
        distance_method=distance_method, graph=graph,
    )
    return plan, event


def recompute_plan(plan, hydrants_df, model=None, params=None, *,
                   exclude=(),
                   require=(),
                   start_radius=DEFAULT_START_RADIUS,
                   radius_step=DEFAULT_RADIUS_STEP,
                   max_radius=DEFAULT_MAX_RADIUS,
                   radius_extension=DEFAULT_RADIUS_EXTENSION_M,
                   candidate_margin=DEFAULT_CANDIDATE_MARGIN_M,
                   distance_method="gis",
                   graph=None):
    """Recompute a demand-known plan -> new plan dict.

    ``exclude`` lists hydrants to drop from the candidate pool for this run;
    ``require`` lists hydrants to lock into the solution (hard requirement, in
    addition to the already-deployed survivors). Both are one-shot and are not
    persisted on the plan. ``radius_extension`` is added to ``max_radius`` so
    recomputes may search farther than the initial analysis did;
    ``candidate_margin`` pads the pool past the covering radius.
    """
    new = copy.deepcopy(plan)
    model_name = model or plan.get("model", "B")
    params = params or plan.get("params") or Params()
    reserve = plan.get("planning_reserve_percent", DEFAULT_PLANNING_RESERVE_PERCENT)
    new["model"] = model_name
    new["distance_method"] = distance_method
    new["planning_reserve_percent"] = reserve

    demand = new.get("effective_demand")
    if demand is None:
        return new

    excluded = set(exclude or ())
    required = set(require or ())
    unavailable = new.get("unavailable", [])

    res = plan.get("result")
    if res is None:
        survivors = set()
        total_pieces = 0
        committed_lines = {}
    else:
        survivors = {s.hydrant for s in res.selected if s.hydrant not in unavailable}
        total_pieces = plan.get("committed_pieces") or 0
        committed_lines = {s.hydrant: s.lines for s in res.selected
                           if s.hydrant in survivors and s.lines}

    # Hose still committed from failed hydrants (kept while not recoverable).
    # Count each surviving hydrant's TOTAL pieces across its parallel lines so a
    # two-line survivor does not inflate the lost-hose reserve.
    active_pieces = sum(
        (s.hose_pieces_total if s.hose_pieces_total is not None else s.hose_pieces)
        for s in (res.selected if res else [])
        if s.hydrant not in unavailable and s.hose_pieces is not None
    )
    failed_pieces = max(0, total_pieces - active_pieces)

    pool = hydrants_df[~hydrants_df["Hydrant"].isin(set(unavailable) | excluded)]
    radius, candidates, sufficient = build_candidates(
        new["location"][0], new["location"][1], demand, pool,
        start_radius, radius_step, max_radius + radius_extension, params,
        distance_method, model_name, graph, candidate_margin,
    )
    candidates = _ensure_committed(candidates, survivors, res)

    # Required hydrants must be in the candidate set even if the demand-covering
    # radius would otherwise drop them (they sit within the extended radius).
    missing = required - set(candidates.index)
    if missing:
        full = _nearby(new["location"][0], new["location"][1],
                       max_radius + radius_extension, pool, distance_method,
                       graph=graph)
        extra = full[full.index.isin(missing)]
        if len(extra):
            candidates = pd.concat([candidates, extra])
            radius = max(radius, float(extra["Distance_m"].max()))

    committed = survivors | required
    result = solve_model(model_name, candidates, demand, params, hydrants_df,
                         committed=committed,
                         committed_lines=committed_lines,
                         failed_pieces=failed_pieces,
                         radius=radius, distance_method=distance_method)

    flow_summary = summarize_flow(new["stated_minimum_flow_l_min"], reserve,
                                  result.demand_served)
    new.update(_plan_from_result(new["location"][0], new["location"][1], demand,
                                 result, params, new["unavailable"],
                                 flow=flow_summary))
    new["candidates"] = candidates
    return new


def apply_update(plan, message, hydrants_df, model=None, params=None,
                 start_radius=DEFAULT_START_RADIUS,
                 radius_step=DEFAULT_RADIUS_STEP,
                 max_radius=DEFAULT_MAX_RADIUS,
                 radius_extension=DEFAULT_RADIUS_EXTENSION_M,
                 planning_reserve_percent=None,
                 distance_method="gis",
                 graph=None):
    """Apply one radio update to ``plan`` -> (new_plan, det, error)."""
    det = detect_update(message)
    demand_update = det.stated and det.demand_phrase
    if not det.failure and not demand_update:
        return None, None, "unrecognized"

    new = copy.deepcopy(plan)
    model_name = model or plan.get("model", "B")
    params = params or plan.get("params") or Params()
    reserve = (planning_reserve_percent if planning_reserve_percent is not None
               else plan.get("planning_reserve_percent", DEFAULT_PLANNING_RESERVE_PERCENT))
    new["model"] = model_name
    new["distance_method"] = distance_method
    new["planning_reserve_percent"] = reserve

    failed = det.failure and det.hydrant
    if failed and det.hydrant not in new["unavailable"]:
        new["unavailable"].append(det.hydrant)

    if demand_update:
        new["stated_minimum_flow_l_min"] = det.flow
        new["effective_demand"] = planning_target_flow(det.flow, reserve)

    # No flow known yet -> legacy nearest-hydrant handling.
    if new["effective_demand"] is None:
        if failed:
            new["selected"].pop(det.hydrant, None)
        if not new["selected"]:
            pool = _candidate_pool(new, hydrants_df)
            nearest = _nearby(new["location"][0], new["location"][1], 1e9, pool,
                              distance_method, max_results=1, graph=graph)
            if not nearest.empty:
                new["selected"] = _make_selected_info(nearest, list(nearest.index))
        new["radius"] = None
        new["insufficient"] = False
        new["result"] = None
        new["objective"] = (_plan_objective(new["selected"], params.v, params.q)
                            if new["selected"] else None)
        return new, det, None

    # A pure demand increase already covered by the deployed configuration needs
    # no recompute: update the plan's metadata only and signal "covered".
    if demand_update and not failed:
        res = plan.get("result")
        target = new["effective_demand"]
        if res is not None and res.total_effective_capacity >= target - flow_tolerance(target):
            new_res = new["result"]
            new_res.demand = target
            new_res.demand_served = target
            new_res.unmet_demand = 0.0
            new_res.demand_met = True
            new_res.recommendation = build_recommendation(new_res)
            new.update(summarize_flow(new["stated_minimum_flow_l_min"], reserve, target))
            new["insufficient"] = False
            return new, det, "covered"

    # --- demand known: recompute with committed survivors locked ---
    new = recompute_plan(new, hydrants_df, model_name, params,
                         start_radius=start_radius, radius_step=radius_step,
                         max_radius=max_radius, radius_extension=radius_extension,
                         distance_method=distance_method, graph=graph)
    return new, det, None


def process_update(plan, message, hydrants_df, model=None, params=None,
                   start_radius=DEFAULT_START_RADIUS,
                   radius_step=DEFAULT_RADIUS_STEP,
                   max_radius=DEFAULT_MAX_RADIUS,
                   radius_extension=DEFAULT_RADIUS_EXTENSION_M,
                   planning_reserve_percent=None,
                   distance_method="gis",
                   graph=None):
    """Run an update and build the event (retained/added) for logging."""
    new, det, error = apply_update(
        plan, message, hydrants_df, model, params, start_radius, radius_step,
        max_radius, radius_extension, planning_reserve_percent, distance_method,
        graph,
    )
    if error == "unrecognized":
        return None, None, error
    if error == "covered":
        event = {
            "kind": "demand",
            "message": message,
            "flow": det.flow if det.stated else None,
            "hydrant": det.hydrant,
            "retained": list(plan["selected"].keys()),
            "added": [],
            "covered": True,
            "summary": _plan_summary_text(new),
        }
        return new, event, None

    retained = [h for h in plan["selected"] if h in new["selected"]]
    added = [h for h in new["selected"] if h not in plan["selected"]]

    new_reinforcement_pieces = None
    if (new.get("result") is not None and new["result"].extra_hose_pieces is not None
            and new["result"].model != "C-hard"):
        old_extra = (plan["result"].extra_hose_pieces
                     if plan.get("result") is not None and plan["result"].extra_hose_pieces is not None
                     else 0)
        new_reinforcement_pieces = max(0, new["result"].extra_hose_pieces - old_extra)

    if det.failure and det.stated and det.demand_phrase:
        kind = "failure+demand"
    elif det.failure:
        kind = "failure"
    else:
        kind = "demand"

    event = {
        "kind": kind,
        "message": message,
        "flow": det.flow if det.stated else None,
        "hydrant": det.hydrant,
        "retained": retained,
        "added": added,
        "new_reinforcement_pieces": new_reinforcement_pieces,
        "summary": _plan_summary_text(new),
    }
    return new, event, None
