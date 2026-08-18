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
from ui.map import render_hydrant_map, render_selection_map
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


def _commit_plan(proposed, hydrants_df, event=None):
    """Commit ``proposed`` as the active plan and clear transient state."""
    if proposed is None:
        return
    st.session_state["plan"] = proposed
    st.session_state["comparison"] = st.session_state.pop("proposed_comparison", [])
    if event:
        st.session_state["event_log"] = st.session_state.get("event_log", []) + [event]
    if proposed.get("stated_minimum_flow_l_min") is not None:
        _seed_config(demand=proposed["stated_minimum_flow_l_min"])
    loc = proposed.get("location")
    if loc:
        _seed_config(lat=loc[0], lon=loc[1])
    st.session_state["awaiting_decision"] = False
    st.session_state["curating"] = False
    st.session_state.pop("declined_proposal", None)
    st.session_state.pop("exclude_selection", None)
    st.session_state.pop("require_selection", None)
    _append_live("assistant", "Recommendation accepted.")


def _commit(hydrants_df):
    event = st.session_state.pop("proposed_event", None)
    proposed = st.session_state.pop("proposed_plan", None)
    if proposed is None:
        return
    _commit_plan(proposed, hydrants_df, event=event)


def _start_curation():
    """Decline: stash the proposal and enter the interactive curation state."""
    proposed = st.session_state.pop("proposed_plan", None)
    st.session_state.pop("proposed_comparison", None)
    st.session_state.pop("proposed_event", None)
    if proposed is None:
        st.session_state["awaiting_decision"] = False
        _append_live("assistant", "Recommendation declined.")
        return
    st.session_state["declined_proposal"] = proposed
    st.session_state["curating"] = True
    st.session_state["awaiting_decision"] = False
    st.session_state.pop("exclude_selection", None)
    st.session_state.pop("require_selection", None)
    _append_live("assistant", "Recommendation declined. Adjust preferences and recompute.")


def _recompute_from_decline(hydrants_df):
    """Recompute using the dispatcher's exclude/require preferences."""
    declined = st.session_state.get("declined_proposal")
    if declined is None:
        return
    exclude = list(st.session_state.get("exclude_selection") or [])
    require = list(st.session_state.get("require_selection") or [])

    s = _settings()
    method, graph = s["distance_method"], None
    loc = declined["location"]
    if method == "network":
        method, graph = _resolve_network(loc[0], loc[1], s["max_radius"] + s["radius_extension"])
    st.session_state["live_method"] = method
    st.session_state["live_graph"] = graph

    plan = st.session_state.get("plan")
    if plan is None:
        # No committed plan yet: recompute from a base carrying only the demand
        # and location (nothing is locked, so exclude/require fully drive it).
        plan = {
            "location": declined["location"],
            "effective_demand": declined.get("effective_demand"),
            "stated_minimum_flow_l_min": declined.get("stated_minimum_flow_l_min"),
            "planning_reserve_percent": declined.get("planning_reserve_percent", s["reserve"]),
            "model": s["model"],
            "params": declined.get("params"),
            "result": None,
            "selected": {},
            "unavailable": list(declined.get("unavailable", [])),
            "committed_pieces": 0,
        }

    new_proposed = recompute_plan(
        plan, hydrants_df, s["model"],
        exclude=exclude, require=require,
        start_radius=s["start_radius"], radius_step=s["radius_step"],
        max_radius=s["max_radius"], radius_extension=s["radius_extension"],
        distance_method=method, graph=graph,
    )

    st.session_state["proposed_plan"] = new_proposed
    st.session_state["proposed_event"] = {
        "kind": "decline",
        "message": None,
        "flow": None,
        "hydrant": None,
        "declined": exclude,
        "required": require,
        "summary": _plan_summary_text(new_proposed),
    }
    _append_live(
        "assistant",
        "Recomputed with the dispatcher's preferences "
        f"(excluded {', '.join(exclude) or 'none'}, required {', '.join(require) or 'none'}).",
    )


def _render_curation(hydrants_df):
    """Curation panel: exclude / force-include multiselects + selection map."""
    declined = st.session_state.get("declined_proposal")
    if declined is None:
        st.session_state["curating"] = False
        return

    st.subheader("Declined recommendation — adjust")
    plan = st.session_state.get("plan")
    committed = set((plan or {}).get("selected", {}).keys())
    drop_options = [h for h in declined.get("selected", {}).keys() if h not in committed]

    candidates = declined.get("candidates")
    cand_ids = list(candidates.index) if candidates is not None and not candidates.empty else []
    selected = set(declined.get("selected", {}).keys())
    include_options = [h for h in cand_ids if h not in selected]

    col_ctrl, col_map = st.columns([1, 1])
    with col_ctrl:
        st.multiselect("Exclude hydrants", options=drop_options, key="exclude_selection")
        st.multiselect("Force-include hydrants", options=include_options, key="require_selection")
        if st.button("Recompute", key="curate_recompute_btn"):
            _recompute_from_decline(hydrants_df)
            st.rerun()

    with col_map:
        _render_selection_map(hydrants_df, declined, candidates, selected)

    _render_plan_output(declined, hydrants_df, map_key="curation_declined")


def _render_selection_map(hydrants_df, declined, candidates, selected_ids):
    """Color-coded candidate map with always-visible hydrant IDs."""
    fire_lat, fire_lon = declined["location"]
    locs = hydrants_df.set_index("Hydrant")[["Latitude", "Longitude"]]
    if candidates is None or candidates.empty:
        cand_map = None
    else:
        cand_map = candidates.join(locs)

    render_selection_map(
        fire_lat, fire_lon, cand_map,
        selected_ids=selected_ids,
        excluded_ids=set(st.session_state.get("exclude_selection") or []),
        required_ids=set(st.session_state.get("require_selection") or []),
        radius=declined.get("radius") or 500,
        unavailable=declined.get("unavailable"),
        hydrants_df=hydrants_df,
        key="curation_select_map",
    )


def _render_comparison(hydrants_df, declined, proposed):
    """Side-by-side declined vs new recommendation with the four actions."""
    st.subheader("Compare: declined vs new")
    col_acc, col_dec, col_both, col_cancel = st.columns(4)
    with col_acc:
        if st.button("Accept new", key="accept_new_btn", type="primary"):
            event = st.session_state.pop("proposed_event", None)
            _commit_plan(st.session_state.pop("proposed_plan", None), hydrants_df, event=event)
            st.rerun()
    with col_dec:
        if st.button("Accept declined", key="accept_declined_btn"):
            st.session_state.pop("proposed_plan", None)
            st.session_state.pop("proposed_event", None)
            _commit_plan(declined, hydrants_df)
            st.rerun()
    with col_both:
        if st.button("Decline both", key="decline_both_btn"):
            st.session_state["declined_proposal"] = st.session_state.pop("proposed_plan", None)
            st.session_state.pop("proposed_event", None)
            st.session_state.pop("proposed_comparison", None)
            st.session_state.pop("exclude_selection", None)
            st.session_state.pop("require_selection", None)
            _append_live("assistant", "Declined both — kept the updated solution as the new base.")
            st.rerun()
    with col_cancel:
        if st.button("Cancel", key="cancel_btn"):
            st.session_state["curating"] = False
            st.session_state.pop("declined_proposal", None)
            st.session_state.pop("proposed_plan", None)
            st.session_state.pop("proposed_event", None)
            st.session_state.pop("proposed_comparison", None)
            st.session_state.pop("exclude_selection", None)
            st.session_state.pop("require_selection", None)
            st.session_state["awaiting_decision"] = False
            _append_live("assistant", "Recommendation declined.")
            st.rerun()

    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown("**Declined solution**")
        _render_plan_output(declined, hydrants_df, map_key="cmp_declined")
    with col_right:
        st.markdown("**New recommendation**")
        _render_plan_output(proposed, hydrants_df, map_key="cmp_new")


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


def _render_plan_output(plan, hydrants_df, map_key=None):
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
        key=map_key,
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
                _start_curation()
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

    curating = st.session_state.get("curating")
    comparing = curating and st.session_state.get("proposed_plan") is not None

    col_chat, col_output = st.columns([3, 2])
    with col_chat:
        st.subheader("Dialog")
        _render_dialog(hydrants_df)
    with col_output:
        st.subheader("Recommendation")
        if comparing:
            st.info("Comparing declined vs new recommendation below.")
        elif curating:
            st.info("Adjust preferences below.")
        else:
            _render_recommendation(hydrants_df)

    if comparing:
        _render_comparison(
            hydrants_df,
            st.session_state["declined_proposal"],
            st.session_state["proposed_plan"],
        )
    elif curating:
        _render_curation(hydrants_df)
