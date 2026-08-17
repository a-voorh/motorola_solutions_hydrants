"""Scripts mode: scenario story at the top, shared dispatcher workspace below.

The dispatcher-oriented part (configuration sidebar, dialog, recommendation with
Accept/Decline, map) is the same shared workspace rendered in Live Dialog mode.
Scripts adds the scripted scenario story at the top of the page (with a pause
for Accept/Decline on each decision) plus technical extras in the sidebar.
"""

import streamlit as st

from data import available_scenarios, load_scenario
from ui.components import render_comparison
from ui.workspace import propose_from_message, render_workspace


def _render_scenario_bar(hydrants_df):
    scenario_names = available_scenarios()
    _default_index = scenario_names.index("default") if "default" in scenario_names else 0
    scenario_name = st.selectbox("Scenario", scenario_names, index=_default_index, key="scenario_name")
    st.caption("Scripted talk-group playback (deterministic; no timing).")

    col1, col2 = st.columns(2)
    with col1:
        load_clicked = st.button("Load scenario", key="load_scenario_btn")
    with col2:
        playback = st.session_state.get("playback")
        awaiting = st.session_state.get("awaiting_decision", False)
        done = bool(playback) and playback.get("index", 0) >= len(playback["scenario"].messages)
        next_clicked = st.button("Next message", key="next_msg_btn",
                                 disabled=done or awaiting or not playback)

    if load_clicked:
        scenario = load_scenario(scenario_name)
        st.session_state["playback"] = {"name": scenario_name, "index": 0, "scenario": scenario}
        st.session_state["plan"] = None
        st.session_state["proposed_plan"] = None
        st.session_state["proposed_comparison"] = None
        st.session_state["proposed_event"] = None
        st.session_state["event_log"] = []
        st.session_state["comparison"] = []
        st.session_state["awaiting_decision"] = False
        st.session_state["live_messages"] = []

    if next_clicked and playback and not awaiting:
        scenario = playback["scenario"]
        message = scenario.messages[playback["index"]]
        text = f"{message.speaker}: {message.text}" if message.speaker else message.text
        propose_from_message(text, hydrants_df, location=message.location)
        st.session_state["playback"]["index"] += 1

    playback_state = st.session_state.get("playback")
    if playback_state:
        scenario = playback_state["scenario"]
        idx = playback_state.get("index", 0)
        total = len(scenario.messages)
        st.caption(f"Scenario **{scenario.title}** — {idx}/{total} messages applied.")
        if idx == 0:
            st.info("Scenario loaded. Use 'Next message' to step through, and "
                    "Accept/Decline each recommendation before advancing.")
        if awaiting:
            st.info("Accept or decline the recommendation to continue the scenario.")


def _render_comparison():
    plan = st.session_state.get("plan")
    comparison = st.session_state.get("comparison")
    if comparison and plan and plan.get("stated_minimum_flow_l_min") is not None:
        render_comparison(comparison,
                          plan["stated_minimum_flow_l_min"],
                          plan.get("planning_reserve_percent", 0.0))


def _render_sidebar_advanced():
    with st.sidebar:
        with st.expander("Advanced settings", expanded=False):
            radius_step = st.number_input("Radius step (m)", key="radius_step", min_value=10, value=30, step=10)
            start_radius = st.number_input("Starting radius (m)", key="start_radius", min_value=0, value=30, step=10)
            max_radius = st.number_input("Maximum radius (m)", key="max_radius", min_value=start_radius, value=1500, step=10)

        with st.expander("Session state (debug)", expanded=False):
            p = st.session_state.get("plan")
            st.write("model:", p.get("model") if p else None)
            st.write("stated_minimum_flow_l_min:", p.get("stated_minimum_flow_l_min") if p else None)
            st.write("planning_reserve_percent:", p.get("planning_reserve_percent") if p else None)
            st.write("planning_target_flow_l_min:", p.get("planning_target_flow_l_min") if p else None)
            st.write("delivered_flow_l_min:", p.get("delivered_flow_l_min") if p else None)
            st.write("minimum_met:", p.get("minimum_met") if p else None)
            st.write("target_met:", p.get("target_met") if p else None)
            st.write("selected:", list(p["selected"].keys()) if p else None)
            st.write("unavailable:", p.get("unavailable") if p else None)
            st.write("radius:", p.get("radius") if p else None)


def render_scripts(hydrants_df):
    """Render the Scripts interface: scenario story + shared workspace + comparison."""
    _render_scenario_bar(hydrants_df)
    render_workspace(hydrants_df)
    _render_sidebar_advanced()
    _render_comparison()
