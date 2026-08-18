"""Streamlit-only rendering components.

The UI calls workflow functions and renders their outputs; it never contains
optimisation or parsing logic. These components are shared by the main app and
the model playground page.
"""

import pandas as pd
import streamlit as st

from domain import CARRIED_PIECES, MODEL_LABELS
from workflow import flow_status_lines, summarize_flow


def describe_event(e):
    """Concise 'detected event' line for the chat log."""
    kind = e.get("kind")
    if kind == "initial":
        if e.get("flow") is None:
            return "No minimum flow stated — recommending nearest hydrant"
        return f"Initial request: {e['flow']:g} L/min"
    if kind == "demand":
        if e.get("covered"):
            return f"Request update to {e['flow']:g} L/min — already covered, no change"
        return f"Request update to {e['flow']:g} L/min"
    if kind == "failure":
        return f"Hydrant {e['hydrant']} marked unavailable"
    if kind == "failure+demand":
        return f"Hydrant {e['hydrant']} unavailable; request update to {e['flow']:g} L/min"
    if kind == "decline":
        parts = []
        if e.get("declined"):
            parts.append(f"excluded {', '.join(e['declined'])}")
        if e.get("required"):
            parts.append(f"required {', '.join(e['required'])}")
        return "Recommendation declined" + (f" ({'; '.join(parts)})" if parts else "")
    if kind == "chatter":
        return "No action required"
    return str(kind)


def selected_rows(result):
    """Build the per-hydrant DataFrame for a ModelResult."""
    rows = []
    for s in result.selected:
        rows.append({
            "Hydrant": s.hydrant,
            "Latitude": s.latitude,
            "Longitude": s.longitude,
            "Distance (m)": round(s.distance_m, 1),
            "Nominal cap (L/min)": int(s.nominal_capacity),
            "Lines": s.lines,
            "Effective cap (L/min)": round(s.effective_capacity, 0),
            "Hose pieces/line": s.hose_pieces if s.hose_pieces is not None else "n/a",
            "Total pieces": s.hose_pieces_total if s.hose_pieces_total is not None else "n/a",
        })
    return pd.DataFrame(rows)


def render_flow_status(flow):
    """Render the two-level status lines for a flow summary."""
    for line in flow_status_lines(flow):
        if "not met" in line:
            st.error(line)
        elif "shortfall" in line:
            st.warning(line)
        else:
            st.success(line)


def render_result_header(plan):
    """Render the recommendation subheader + flow-status lines; returns the flow dict."""
    result = plan["result"]
    st.subheader(f"Recommendation — {result.model}: {MODEL_LABELS[result.model]}")

    stated = plan.get("stated_minimum_flow_l_min")
    if stated is None:
        return None

    reserve_pct = plan.get("planning_reserve_percent", 0.0)
    flow = summarize_flow(stated, reserve_pct, result.demand_served)
    render_flow_status(flow)
    return flow


def render_result_body(plan, flow=None):
    """Render the metrics table, selected-hydrant table, hose info, and summary."""
    result = plan["result"]
    stated = plan.get("stated_minimum_flow_l_min")
    if stated is None:
        st.info(result.recommendation)
        return

    if flow is None:
        reserve_pct = plan.get("planning_reserve_percent", 0.0)
        flow = summarize_flow(stated, reserve_pct, result.demand_served)

    st.markdown(
        "| Metric | Value |\n"
        "|---|---|\n"
        f"| Stated minimum request | {flow['stated_minimum_flow_l_min']:g} L/min |\n"
        f"| Planning reserve | {flow['planning_reserve_l_min']:g} L/min "
        f"({flow['planning_reserve_percent']:g}%) |\n"
        f"| Planning target | {flow['planning_target_flow_l_min']:g} L/min |\n"
        f"| Demand served | {result.demand_served:g} L/min |\n"
        f"| Unmet demand | {result.unmet_demand:g} L/min |\n"
        f"| Total nominal capacity | {result.total_nominal_capacity:g} L/min |\n"
        f"| Total effective capacity | {result.total_effective_capacity:g} L/min |\n"
        f"| Deployment time | {result.deployment_time:.2f} |"
    )

    if result.selected:
        st.dataframe(selected_rows(result))
    else:
        st.write("No hydrants selected.")

    if result.hose_pieces_used is not None:
        if result.model in ("A", "B", "C-soft"):
            st.write(f"Hose: **{result.carried_pieces_used} carried pieces used**, "
                     f"**{result.extra_hose_pieces} extra** (reinforcement), "
                     f"**{result.hose_pieces_used} total pieces**")
        elif result.model == "C-hard":
            st.write(f"Hose: **{result.hose_pieces_used} of {CARRIED_PIECES} pieces used**")
    else:
        st.write("Hose inventory: not applicable")

    st.info(result.recommendation)


def render_result(plan):
    """Render a plan's selected-model recommendation (header + body)."""
    flow = render_result_header(plan)
    render_result_body(plan, flow)


def comparison_rows(results, stated_minimum, reserve_percent):
    """Build the compact four-model comparison DataFrame."""
    rows = []
    for r in results:
        flow = summarize_flow(stated_minimum, reserve_percent, r.demand_served)
        rows.append({
            "Model": f"{r.model} · {MODEL_LABELS[r.model]}",
            "Demand served (L/min)": round(r.demand_served, 0),
            "Min. request met": "Yes" if flow["minimum_met"] else "No",
            "Shortage (L/min)": f"{r.unmet_demand:g}",
            "Hydrants": len(r.selected),
            "Objective (time)": round(r.deployment_time, 2),
            "Hose pieces": "n/a" if r.hose_pieces_used is None else str(r.hose_pieces_used),
            "Extra pieces": "n/a" if r.extra_hose_pieces is None else str(r.extra_hose_pieces),
        })
    return pd.DataFrame(rows)


def render_comparison(results, stated_minimum, reserve_percent):
    if not results:
        return
    st.subheader("Model comparison")
    st.dataframe(comparison_rows(results, stated_minimum, reserve_percent))
    st.caption("Same candidates, location, and requested flow across all four models. "
               "Capacities and parameters are illustrative prototype assumptions "
               "(one connection per hydrant).")


def render_chat_log(log):
    if not log:
        st.write("No messages yet.")
        return
    for e in log:
        if e.get("message"):
            with st.chat_message("user"):
                if e.get("timestamp"):
                    st.caption(e["timestamp"])
                speaker = e.get("speaker")
                if speaker:
                    st.write(f"**{speaker}**: {e['message']}")
                else:
                    st.write(e["message"])
        with st.chat_message("assistant"):
            st.markdown(f"Detected: {describe_event(e)}")
            if e.get("summary"):
                st.markdown(f"Plan: {e['summary']}")
            if e.get("new_reinforcement_pieces"):
                st.markdown(f"New reinforcement pieces required: **{e['new_reinforcement_pieces']}**")
