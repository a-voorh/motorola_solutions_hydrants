"""Model playground: vary parameters and inspect any model's output."""

import pandas as pd
import streamlit as st

from data import get_hydrants
from domain import (
    CARRIED_PIECES,
    DEFAULT_GAMMA,
    DEFAULT_MAX_LINES_PER_HYDRANT,
    HOSE_PIECE_M,
    MODEL_OPTION_LABELS,
    Params,
)
from graph_cache import get_graph
from routing import build_candidates
from solver import solve_model
from ui.map import (
    consume_pending_click,
    ensure_incident_location,
    render_incident_map,
    set_location_from_click,
)
from workflow import flow_status_lines, planning_target_flow, summarize_flow

st.title("Model playground")

hydrants = get_hydrants()

with st.sidebar:
    st.header("Model")
    model = st.selectbox(
        "Water-supply optimization model",
        options=list(MODEL_OPTION_LABELS),
        format_func=lambda m: MODEL_OPTION_LABELS[m],
        index=1,  # default to Model B (the current/decayed setup-time model)
        key="model",
    )

    st.header("Model parameters")
    q = st.number_input("Deployment time (q, time units)", key="q", min_value=0.0, value=10.0, step=0.5)
    v = st.number_input("Hose deployment rate (v)", key="v", min_value=0.01, value=5.0, step=0.1)
    hose_piece_m = st.number_input("Hose piece length (m)", key="hose_piece_m",
                                   min_value=1.0, value=HOSE_PIECE_M, step=1.0)
    carried_pieces = st.number_input("Carried hose pieces", key="carried_pieces",
                                     min_value=1, value=CARRIED_PIECES, step=1)
    max_lines = st.number_input("Max parallel lines per hydrant", key="max_lines",
                                min_value=1, value=DEFAULT_MAX_LINES_PER_HYDRANT, step=1)
    gamma = st.number_input("Hydraulic calibration gamma (L/min·√m)", key="gamma",
                            min_value=0.0, value=DEFAULT_GAMMA, step=100.0, format="%.1f")
    st.caption("gamma is an experimental hydraulic calibration parameter — "
               "not physically calibrated.")
    planning_reserve = st.number_input("Planning reserve (%)", key="planning_reserve",
                                       min_value=0.0, value=50.0, step=5.0)
    st.caption("Prototype assumption — not an operational firefighting standard.")

    st.header("Search parameters")
    radius_step = st.number_input("Radius step (m)", key="radius_step", min_value=10, value=30, step=10)
    start_radius = st.number_input("Starting radius (m)", key="start_radius", min_value=0, value=30, step=10)
    max_radius = st.number_input("Maximum radius (m)", key="fire_radius", min_value=start_radius, value=1500, step=10)

consume_pending_click()
ensure_incident_location()

col1, col2, col3 = st.columns(3)
with col1:
    lat = st.number_input("Fire latitude", key="fire_lat", value=None, format="%.6f")
with col2:
    lon = st.number_input("Fire longitude", key="fire_lon", value=None, format="%.6f")
with col3:
    flow = st.number_input("Required flow (L/min)", value=4000, min_value=0)

distance_method = st.radio(
    "Distance method",
    options=["network", "gis", "manhattan"],
    format_func=lambda m: {"network": "Street network", "gis": "GIS (geodesic)", "manhattan": "Manhattan"}[m],
    index=0,
    key="distance_method",
    horizontal=True,
)

location_ok = lat is not None and lon is not None
if not location_ok:
    st.info("Enter a fire location (latitude and longitude).")

if st.button("Run model", key="run_btn", disabled=not location_ok):
    params = Params(v=v, q=q, gamma=gamma, hose_piece_m=hose_piece_m,
                    carried_pieces=carried_pieces, max_lines_per_hydrant=max_lines)
    demand = planning_target_flow(flow, planning_reserve)

    method, graph = distance_method, None
    if distance_method == "network":
        try:
            graph = get_graph(lat, lon, max_radius + 200)
        except Exception as e:
            st.warning(f"Street-network routing unavailable ({e}). Falling back to GIS.")
            method = "gis"

    radius, candidates, sufficient = build_candidates(
        lat, lon, demand, hydrants, start_radius, radius_step, max_radius,
        params, method, model, graph,
    )
    result = solve_model(model, candidates, demand, params, hydrants,
                         radius=radius, distance_method=method)

    locs = hydrants.set_index("Hydrant")[["Latitude", "Longitude"]]
    st.session_state["run_fire"] = (lat, lon)
    st.session_state["run_method"] = method
    st.session_state["run_graph"] = graph
    st.session_state["run_radius"] = radius
    st.session_state["run_sufficient"] = sufficient
    st.session_state["run_candidates"] = candidates.join(locs)
    st.session_state["run_result"] = result
    st.session_state["run_demand"] = demand


result = st.session_state.get("run_result")

# --- always-visible map: movable incident marker + latest run overlay ---
map_data = render_incident_map(
    lat, lon, hydrants, key="playground_map",
    candidates=st.session_state.get("run_candidates"),
    selected=(result.selected if result else None),
    radius=st.session_state.get("run_radius"),
    graph=st.session_state.get("run_graph"),
    street_routes=(st.session_state.get("run_method", distance_method) == "network"),
)
set_location_from_click(map_data)
st.caption("Click the map to move the incident marker.")

if result is None:
    st.info("Click 'Run model' to see the result for the current marker location.")
else:
    demand = st.session_state["run_demand"]
    radius = st.session_state["run_radius"]
    method = st.session_state["run_method"]
    sufficient = st.session_state["run_sufficient"]
    st.write(f"Candidate set radius: **{radius} m** (sufficient: {sufficient})")

    flow_summary = summarize_flow(flow, planning_reserve, result.demand_served)
    st.write(
        f"Stated minimum request **{flow_summary['stated_minimum_flow_l_min']:g} L/min**, "
        f"planning reserve **{flow_summary['planning_reserve_l_min']:g} L/min "
        f"({flow_summary['planning_reserve_percent']:g}%)**, "
        f"planning target **{flow_summary['planning_target_flow_l_min']:g} L/min**."
    )
    for line in flow_status_lines(flow_summary):
        if "not met" in line:
            st.error(line)
        elif "shortfall" in line:
            st.warning(line)
        else:
            st.success(line)

    if result.selected:
        rows = [
            {
                "Hydrant": s.hydrant,
                "Distance (m)": round(s.distance_m, 1),
                "Nominal cap (L/min)": int(s.nominal_capacity),
                "Lines": s.lines,
                "Effective cap (L/min)": round(s.effective_capacity, 0),
                "Hose pieces/line": s.hose_pieces if s.hose_pieces is not None else "n/a",
                "Total pieces": s.hose_pieces_total if s.hose_pieces_total is not None else "n/a",
            }
            for s in result.selected
        ]
        st.subheader("Selected hydrants")
        st.dataframe(pd.DataFrame(rows))
    else:
        st.write("No hydrants selected.")

    st.write(f"Demand served: **{result.demand_served:g} L/min**")
    st.write(f"Unmet demand: **{result.unmet_demand:g} L/min**")
    st.write(f"Total nominal capacity: **{result.total_nominal_capacity:g} L/min**")
    st.write(f"Total effective capacity: **{result.total_effective_capacity:g} L/min**")
    if result.hose_pieces_used is not None:
        if result.model in ("A", "B", "C-soft"):
            st.write(f"Hose: **{result.carried_pieces_used} carried pieces used**, "
                     f"**{result.extra_hose_pieces} extra** (reinforcement), "
                     f"**{result.hose_pieces_used} total pieces**")
        elif result.model == "C-hard":
            st.write(f"Hose: **{result.hose_pieces_used} of {carried_pieces} pieces used**")
    else:
        st.write("Hose inventory: not applicable")
    st.write(f"Deployment time: **{result.deployment_time:.2f} time units**")
    st.info(result.recommendation)
