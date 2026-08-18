"""Shared dispatcher workspace used by both Live Dialog and Scripts modes.

Natural-language dialog -> interpreted configuration -> existing deterministic
optimizer -> recommendation (with Accept/Decline) -> committed plan.

The "interpreter" is the existing deterministic parser
(``extraction.detect_update`` / ``extraction.extract_location``). The optimizer's
result is staged as ``proposed_plan`` and shown with Accept/Decline; Accept
commits it as the active ``plan``, Decline discards it. Both modes share the
same ``st.session_state`` objects.
"""

import streamlit as st

from domain import CARRIED_PIECES, DEFAULT_RADIUS_EXTENSION_M, MODEL_OPTION_LABELS, IncidentRequest
from extraction import detect_update, extract_location
from graph_cache import get_graph
from ui.components import render_result
from ui.map import render_hydrant_map
from workflow import analyse_incident, process_update, recompute_plan, _plan_summary_text

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
        "radius_extension": st.session_state.get("radius_extension", DEFAULT_RADIUS_EXTENSION_M),
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
        method, graph = _resolve_network(plan["location"][0], plan["location"][1],
                                         s["max_radius"] + s["radius_extension"])
    st.session_state["live_method"] = method
    st.session_state["live_graph"] = graph
    return process_update(
        plan, message, hydrants_df, s["model"],
        start_radius=s["start_radius"], radius_step=s["radius_step"],
        max_radius=s["max_radius"], radius_extension=s["radius_extension"],
        distance_method=method, graph=graph,
    )


def propose_from_message(message, hydrants_df, location=None):
    """Interpret a message, run the optimizer, and stage a proposal.

    Returns the human-readable interpretation summary. The optimizer result is
    stored in ``st.session_state["proposed_plan"]`` (with its comparison/event);
    ``st.session_state["awaiting_decision"]`` is set when there is a proposal to
    accept or decline.
    """
    _append_live("user", message)
    facts = detect_update(message)
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

    if facts.failure:
        if plan is None:
            summary = "No active incident yet — describe the situation first."
        else:
            proposed, event, error = _run_update(plan, message, hydrants_df)
            summary = f"Hydrant {facts.hydrant} out of service" if not error else "Could not apply the update."
    elif facts.stated and facts.demand_phrase:
        if plan is None:
            summary = "No active incident yet — describe the situation first."
        else:
            proposed, event, error = _run_update(plan, message, hydrants_df)
            summary = f"New demand {facts.flow:g} L/min" if not error else "Could not apply the update."
    elif facts.stated:
        if plan is None:
            fb = _fallback_location()
            if fb is None:
                summary = "Set a location (or include coordinates) before requesting a flow."
            else:
                proposed, event, comparison = _run_analysis(facts.flow, fb[0], fb[1], hydrants_df)
                summary = f"Initial request {facts.flow:g} L/min"
        else:
            summary = "Flow stated but not a demand update — say 'increase demand to …' to change it."
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

    if proposed is not None:
        st.session_state["proposed_plan"] = proposed
        st.session_state["proposed_comparison"] = comparison
        st.session_state["proposed_event"] = event
    st.session_state["awaiting_decision"] = proposed is not None

    _append_live("assistant", summary)
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


def _discard(hydrants_df):
    """Decline the proposal, then recompute with its new hydrants excluded.

    Already-committed hydrants stay committed; only the hydrants the proposal
    *added* are excluded, and the search radius is extended so alternatives can
    be found. The replacement is staged as a fresh proposal for Accept/Decline.
    """
    proposed = st.session_state.pop("proposed_plan", None)
    st.session_state.pop("proposed_comparison", None)
    st.session_state.pop("proposed_event", None)
    plan = st.session_state.get("plan")

    if proposed is None:
        st.session_state["awaiting_decision"] = False
        _append_live("assistant", "Recommendation declined.")
        return

    existing = set((plan or {}).get("selected", {}).keys())
    declined_now = [h for h in proposed.get("selected", {}).keys() if h not in existing]
    if not declined_now:
        st.session_state["awaiting_decision"] = False
        _append_live("assistant", "Recommendation declined.")
        return

    declined = list(dict.fromkeys(st.session_state.get("declined_hydrants", []) + declined_now))
    st.session_state["declined_hydrants"] = declined

    s = _settings()
    method, graph = s["distance_method"], None
    loc = (plan or proposed)["location"]
    if method == "network":
        method, graph = _resolve_network(loc[0], loc[1], s["max_radius"] + s["radius_extension"])
    st.session_state["live_method"] = method
    st.session_state["live_graph"] = graph

    if plan is None:
        transcript = proposed.get("transcript") or (
            f"We need {proposed.get('stated_minimum_flow_l_min', 0):g} L/min"
        )
        request = IncidentRequest(
            transcript=transcript,
            location=proposed["location"],
            planning_reserve_percent=s["reserve"],
        )
        filtered = hydrants_df[
            ~hydrants_df["Hydrant"].isin(set(declined) | set(proposed.get("unavailable", [])))
        ]
        new_proposed, _event, _comparison = analyse_incident(
            request, filtered, s["model"],
            start_radius=s["start_radius"], radius_step=s["radius_step"],
            max_radius=s["max_radius"] + s["radius_extension"],
            distance_method=method, graph=graph,
        )
    else:
        new_proposed = recompute_plan(
            plan, hydrants_df, s["model"],
            exclude=declined,
            start_radius=s["start_radius"], radius_step=s["radius_step"],
            max_radius=s["max_radius"], radius_extension=s["radius_extension"],
            distance_method=method, graph=graph,
        )

    if new_proposed is not None and new_proposed.get("result") is not None:
        st.session_state["proposed_plan"] = new_proposed
        st.session_state["proposed_comparison"] = None
        st.session_state["proposed_event"] = {
            "kind": "decline",
            "message": None,
            "flow": None,
            "hydrant": None,
            "declined": declined_now,
            "summary": _plan_summary_text(new_proposed),
        }
        st.session_state["awaiting_decision"] = True
        _append_live(
            "assistant",
            f"Recommendation declined; excluding {', '.join(declined_now)}. "
            "Recomputed with an extended search radius.",
        )
    else:
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
            placeholder="e.g. 'We need 800 L/min at 55.664178, 12.607972'",
        )
        submitted = st.form_submit_button("Send", key="live_send_btn")

    if submitted and msg:
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
                if plan and plan.get("stated_minimum_flow_l_min") is not None else 800.0
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
                _discard(hydrants_df)
                st.rerun()
        _render_plan_output(proposed, hydrants_df)
    elif plan is not None and plan.get("result") is not None:
        st.subheader("Current recommendation")
        _render_plan_output(plan, hydrants_df)
    else:
        st.info("No recommendation yet. Describe the situation in the chat "
                "(e.g. 'We need 800 L/min at 55.664178, 12.607972').")


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
