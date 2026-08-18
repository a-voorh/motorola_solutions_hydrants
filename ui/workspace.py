"""Shared dispatcher workspace used by both Live Dialog and Scripts modes.

Natural-language dialog -> interpreted configuration -> existing deterministic
optimizer -> recommendation (with Accept/Decline) -> committed plan.

The "interpreter" is the existing deterministic parser
(``extraction.detect_update`` / ``extraction.extract_location``). The optimizer's
result is staged as ``proposed_plan`` and shown with Accept/Decline; Accept
commits it as the active ``plan``, Decline discards it. Both modes share the
same ``st.session_state`` objects.
"""

import requests
import streamlit as st
from ui.transcriptReader import parse_with_openai, geocode_location

from domain import CARRIED_PIECES, MODEL_OPTION_LABELS, IncidentRequest
from extraction import detect_update, extract_location
from graph_cache import get_graph
from ui.components import render_result
from ui.map import render_hydrant_map
from workflow import analyse_incident, process_update

_DISTANCE_LABELS = {
    "network": "Street network",
    "gis": "GIS (geodesic)",
    "manhattan": "Manhattan",
}


def _settings():
    """Read the shared operational settings (with defaults)."""
    return {
        "model": st.session_state.get("model", "B"),
        "reserve": st.session_state.get("planning_reserve", 50.0),
        "distance_method": st.session_state.get("distance_method", "network"),
        "start_radius": st.session_state.get("start_radius", 30),
        "radius_step": st.session_state.get("radius_step", 30),
        "max_radius": st.session_state.get("max_radius", 1500),
    }


def _resolve_network(lat, lon, max_radius):
    """Return (distance_method, graph), falling back to GIS if routing fails."""
    try:
        return "network", get_graph(lat, lon, max_radius + 200)
    except Exception as e:  # offline / OSM error
        st.warning(f"Street-network routing unavailable ({e}). Falling back to GIS.")
        return "gis", None


def _append_live(role, text):
    st.session_state.setdefault("live_messages", []).append({"role": role, "text": text})


def clear_dialog():
    """Clear transient dialog/proposal state (mode or page switch).

    The committed ``plan`` (shared operational state) is intentionally kept;
    only the chat history and any pending recommendation are reset.
    """
    st.session_state.pop("live_messages", None)
    st.session_state.pop("live_dialog_input", None)
    st.session_state.pop("proposed_plan", None)
    st.session_state.pop("proposed_comparison", None)
    st.session_state.pop("proposed_event", None)
    st.session_state["awaiting_decision"] = False


def _seed_config(**values):
    """Queue config values to be applied to the sidebar inputs on the next run."""
    seed = st.session_state.get("_seed_config", {})
    seed.update(values)
    st.session_state["_seed_config"] = seed


def _run_analysis(demand, lat, lon, hydrants_df):
    """Run a fresh incident analysis -> (plan, event, comparison)."""
    s = _settings()
    method, graph = s["distance_method"], None
    if method == "network":
        method, graph = _resolve_network(lat, lon, s["max_radius"])
    st.session_state["live_method"] = method
    st.session_state["live_graph"] = graph
    request = IncidentRequest(
        transcript=f"We need {demand:g} L/min",
        location=(lat, lon),
        planning_reserve_percent=s["reserve"],
    )
    return analyse_incident(
        request, hydrants_df, s["model"],
        start_radius=s["start_radius"], radius_step=s["radius_step"],
        max_radius=s["max_radius"], distance_method=method, graph=graph,
    )


def _run_update(plan, message, hydrants_df):
    """Apply a failure/demand update -> (new_plan, event, error)."""
    s = _settings()
    method, graph = s["distance_method"], None
    if method == "network":
        method, graph = _resolve_network(plan["location"][0], plan["location"][1], s["max_radius"])
    st.session_state["live_method"] = method
    st.session_state["live_graph"] = graph
    return process_update(
        plan, message, hydrants_df, s["model"],
        start_radius=s["start_radius"], radius_step=s["radius_step"],
        max_radius=s["max_radius"], distance_method=method, graph=graph,
    )


def propose_from_message(message, hydrants_df, location=None):
    """Interpret a message using OpenAI, run the optimizer, and stage a proposal.

    Returns the human-readable interpretation summary. The optimizer result is
    stored in ``st.session_state["proposed_plan"]`` (with its comparison/event);
    ``st.session_state["awaiting_decision"]`` is set when there is a proposal to
    accept or decline.
    """
    _append_live("user", message)

    api_lat, api_lon, api_flow = None, None, 0.0

    print(f"\n[DEBUG 1] Starting parse_with_openai for: '{message}'", flush=True)
    st.write(f"🔍 Parsing message: `{message}`")

    try:
        parsed = parse_with_openai(message)
        print(f"[DEBUG 2] Raw parsed object: {parsed}", flush=True)
        st.write(f"📋 Parsed raw result: `{parsed}`")

        # Use the actual attribute names defined in ParsedMessage
        # (or fallback safely using getattr)
        api_lat = getattr(parsed, "latitude", None) or getattr(parsed, "x", None)
        api_lon = getattr(parsed, "longitude", None) or getattr(parsed, "y", None)
        api_flow = getattr(parsed, "water_lpm", 0.0) or getattr(parsed, "w", 0.0)
        loc_name = getattr(parsed, "location_name", None)

        print(f"[DEBUG 3] Extracted values -> Lat: {api_lat}, Lon: {api_lon}, Flow: {api_flow}, Place: {loc_name}", flush=True)

        # Geocode if coordinates are missing but a place name exists
        if (api_lat is None or api_lon is None) and loc_name:
            print(f"[DEBUG 4] Geocoding place name: '{loc_name}'", flush=True)
            try:
                api_lat, api_lon = geocode_location(loc_name)
                print(f"[DEBUG 5] Geocoding success: ({api_lat}, {api_lon})", flush=True)
            except Exception as geo_err:
                print(f"[DEBUG 5 Error] Geocode failed: {geo_err}", flush=True)
                st.warning(f"Could not geocode location '{loc_name}': {geo_err}")

    except Exception as e:
        print(f"[DEBUG ERROR] OpenAI parsing failed: {e}", flush=True)
        st.error(f"OpenAI parsing failed: {e}")
        # Note: Do not let errors fail silently

    # 2. Determine target incident coordinates
    if api_lat is not None and api_lon is not None:
        loc = (api_lat, api_lon)
    else:
        loc = location or extract_location(message)

    plan = st.session_state.get("plan")

    if loc:
        _seed_config(lat=loc[0], lon=loc[1])

    def _fallback_location():
        if loc is not None:
            return loc
        lat = st.session_state.get("lat")
        lon = st.session_state.get("lon")
        return (lat, lon) if (lat is not None and lon is not None) else None

    proposed = None
    comparison = None
    event = None

    # 3. Handle analysis triggers based on parsed water demand and location
    if api_flow > 0:
        fb = _fallback_location()
        if fb is None:
            summary = "Set a location (or include coordinates/landmarks) before requesting a flow."
        else:
            proposed, event, comparison = _run_analysis(api_flow, fb[0], fb[1], hydrants_df)
            summary = f"Initial request {api_flow:g} L/min at {fb[0]:.4f}, {fb[1]:.4f}"
    elif loc:
        if plan is not None and plan.get("stated_minimum_flow_l_min") is not None:
            proposed, event, comparison = _run_analysis(
                plan["stated_minimum_flow_l_min"], loc[0], loc[1], hydrants_df
            )
            summary = f"Location updated to {loc[0]:g}, {loc[1]:g}"
        else:
            summary = f"Location set to {loc[0]:g}, {loc[1]:g}. Describe the required flow."
    else:
        summary = "No configuration change detected."

    # 4. Save and stage the proposal for decision
    if proposed is not None:
        st.session_state["proposed_plan"] = proposed
        st.session_state["proposed_comparison"] = comparison
        st.session_state["proposed_event"] = event
    st.session_state["awaiting_decision"] = proposed is not None

    _append_live("assistant", summary)
    print('Summary')
    if loc:
        print(f"[DEBUG] Location (lat, lon): {loc}")
    print(f"[DEBUG] Water flow (L/min): {api_flow}")
    return summary


def _commit(hydrants_df):
    proposed = st.session_state.pop("proposed_plan", None)
    if proposed is None:
        return
    st.session_state["plan"] = proposed
    st.session_state["comparison"] = st.session_state.pop("proposed_comparison", [])
    event = st.session_state.pop("proposed_event", None)
    if event:
        st.session_state["event_log"] = st.session_state.get("event_log", []) + [event]
    if proposed.get("stated_minimum_flow_l_min") is not None:
        _seed_config(demand=proposed["stated_minimum_flow_l_min"])
    loc = proposed.get("location")
    if loc:
        _seed_config(lat=loc[0], lon=loc[1])
    st.session_state["awaiting_decision"] = False
    _append_live("assistant", "Recommendation accepted.")


def _discard():
    st.session_state.pop("proposed_plan", None)
    st.session_state.pop("proposed_comparison", None)
    st.session_state.pop("proposed_event", None)
    st.session_state["awaiting_decision"] = False
    _append_live("assistant", "Recommendation declined.")


def _render_dialog(hydrants_df):
    for m in st.session_state.get("live_messages", []):
        with st.chat_message(m["role"]):
            st.write(m["text"])

    with st.form("live_dialog_form", clear_on_submit=True):
        msg = st.text_input(
            "Describe the situation",
            key="live_dialog_input",
            placeholder="e.g. 'We need 4000 L/min at 55.664178, 12.607972'",
        )
        submitted = st.form_submit_button("Send", key="live_send_btn")

    if submitted and msg:
        print(msg)
        propose_from_message(msg, hydrants_df)
        st.rerun()


def _render_sidebar_config(hydrants_df):
    plan = st.session_state.get("plan")

    with st.sidebar:
        st.header("Configuration")

        seed = st.session_state.pop("_seed_config", None)
        if seed:
            if seed.get("demand") is not None:
                st.session_state["demand_lpm"] = seed["demand"]
            if seed.get("lat") is not None:
                st.session_state["lat"] = seed["lat"]
            if seed.get("lon") is not None:
                st.session_state["lon"] = seed["lon"]

        st.selectbox(
            "Model",
            options=list(MODEL_OPTION_LABELS),
            format_func=lambda m: MODEL_OPTION_LABELS[m],
            index=1,
            key="model",
        )

        if "demand_lpm" not in st.session_state:
            st.session_state["demand_lpm"] = (
                plan["stated_minimum_flow_l_min"]
                if plan and plan.get("stated_minimum_flow_l_min") is not None else 4000.0
            )
        demand = st.number_input("Demand (L/min)", key="demand_lpm", min_value=0.0, step=100.0)

        st.number_input(
            "Demand buffer (%)",
            key="planning_reserve",
            min_value=0.0, value=50.0, step=5.0,
            help="Extra capacity added on top of the stated demand.",
        )

        col_lat, col_lon = st.columns(2)
        with col_lat:
            lat = st.number_input("Latitude", key="lat", value=None, format="%.6f")
        with col_lon:
            lon = st.number_input("Longitude", key="lon", value=None, format="%.6f")

        st.radio(
            "Distance method",
            options=["network", "gis", "manhattan"],
            format_func=lambda m: _DISTANCE_LABELS[m],
            index=0,
            key="distance_method",
            horizontal=True,
        )

        if st.button("Update & re-run", key="live_run_btn", disabled=(lat is None or lon is None)):
            proposed, event, comparison = _run_analysis(demand, lat, lon, hydrants_df)
            st.session_state["proposed_plan"] = proposed
            st.session_state["proposed_comparison"] = comparison
            st.session_state["proposed_event"] = event
            st.session_state["awaiting_decision"] = True
            st.rerun()

        st.divider()

        if plan is None:
            st.caption("No active incident.")
        else:
            result = plan.get("result")
            st.write(f"**Model:** {plan.get('model')}")
            if result is not None:
                st.write(f"**Delivered:** {result.demand_served:g} L/min")
                st.write(f"**Unmet:** {result.unmet_demand:g} L/min")
                if result.hose_pieces_used is not None:
                    if result.model in ("A", "B", "C-soft"):
                        st.write(f"**Hose:** {result.carried_pieces_used} carried, "
                                 f"{result.extra_hose_pieces} extra, {result.hose_pieces_used} total")
                    else:
                        st.write(f"**Hose:** {result.hose_pieces_used} of {CARRIED_PIECES} pieces")
            committed = list(plan.get("selected", {}).keys())
            unavailable = plan.get("unavailable", [])
            st.write(f"**Committed:** {', '.join(committed) if committed else '—'}")
            st.write(f"**Unavailable:** {', '.join(unavailable) if unavailable else '—'}")


def _render_plan_output(plan, hydrants_df):
    render_result(plan)
    candidates = plan.get("candidates")
    if candidates is None or candidates.empty:
        return
    locs = hydrants_df.set_index("Hydrant")[["Latitude", "Longitude"]]
    candidates = candidates.join(locs)
    fire_lat, fire_lon = plan["location"]
    method = plan.get("distance_method", "gis")
    graph = st.session_state.get("live_graph")
    render_hydrant_map(
        fire_lat, fire_lon, candidates, plan["result"].selected,
        plan.get("radius") or 500,
        graph=graph, street_routes=(method == "network"),
        unavailable=plan.get("unavailable"), hydrants_df=hydrants_df,
    )


def _render_recommendation(hydrants_df):
    proposed = st.session_state.get("proposed_plan")
    plan = st.session_state.get("plan")

    if proposed is not None:
        st.subheader("Proposed recommendation")
        col_acc, col_dec = st.columns(2)
        with col_acc:
            if st.button("Accept", key="live_accept_btn", type="primary"):
                _commit(hydrants_df)
                st.rerun()
        with col_dec:
            if st.button("Decline", key="live_decline_btn"):
                _discard()
                st.rerun()
        _render_plan_output(proposed, hydrants_df)
    elif plan is not None and plan.get("result") is not None:
        st.subheader("Current recommendation")
        _render_plan_output(plan, hydrants_df)
    else:
        st.info("No recommendation yet. Describe the situation in the chat "
                "(e.g. 'We need 4000 L/min at 55.664178, 12.607972').")


def render_workspace(hydrants_df):
    """Render the shared dispatcher workspace (config sidebar + chat + recommendation)."""
    _render_sidebar_config(hydrants_df)

    col_chat, col_output = st.columns([3, 2])
    with col_chat:
        st.subheader("Dialog")
        _render_dialog(hydrants_df)
    with col_output:
        st.subheader("Recommendation")
        _render_recommendation(hydrants_df)
