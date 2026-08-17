"""Planning-reserve semantics and status summaries.

This module owns the separation between the stated minimum flow and the
conservative planning target, and produces the two-level status lines
("minimum request" vs "planning target").
"""

from domain import flow_tolerance


def planning_target_flow(stated_minimum, reserve_percent):
    """Planning target = stated minimum plus the reserve percentage."""
    return stated_minimum * (1.0 + reserve_percent / 100.0)


def summarize_flow(stated_minimum, reserve_percent, delivered):
    """Compute the separated flow fields and met/shortfall status.

    The plan is judged against the stated minimum first; the planning reserve
    is judged separately. ``delivered`` is the achieved flow (L/min).
    """
    reserve = stated_minimum * reserve_percent / 100.0
    target = stated_minimum + reserve
    minimum_met = delivered >= stated_minimum - flow_tolerance(stated_minimum)
    target_met = delivered >= target - flow_tolerance(target)
    return {
        "stated_minimum_flow_l_min": stated_minimum,
        "planning_reserve_percent": reserve_percent,
        "planning_reserve_l_min": reserve,
        "planning_target_flow_l_min": target,
        "delivered_flow_l_min": delivered,
        "minimum_met": minimum_met,
        "target_met": target_met,
        "planning_target_shortfall_l_min": max(0.0, target - delivered),
        "operational_shortfall_l_min": max(0.0, stated_minimum - delivered),
    }


def flow_status_lines(flow):
    """Human-readable status lines for a flow summary dict."""
    lines = []
    if flow["minimum_met"]:
        lines.append("Minimum request met")
    else:
        lines.append(
            "Minimum request not met (operational shortfall "
            f"{flow['operational_shortfall_l_min']:g} L/min)"
        )
    if flow["target_met"]:
        lines.append("Planning reserve fully achieved")
    else:
        lines.append(
            f"Planning-target shortfall: {flow['planning_target_shortfall_l_min']:g} L/min"
        )
    return lines
