"""Scripted talk-group scenario application.

Turns a :class:`Scenario` into the same plan/event flow the manual UI produces:

  * the first message is expected to state a flow and carry a location, and
    becomes the initial analysis (``analyse_incident``);
  * later messages are applied via ``process_update`` when the parser detects a
    failure or a demand change;
  * every other message is logged as a ``"chatter"`` event with no plan change.

``apply_scenario_message`` is the resumable primitive (used by step-through UI);
``run_scenario`` applies every message in one go. Neither is timed.
"""

from domain import (
    DEFAULT_MAX_RADIUS,
    DEFAULT_PLANNING_RESERVE_PERCENT,
    DEFAULT_RADIUS_EXTENSION_M,
    DEFAULT_RADIUS_STEP,
    DEFAULT_START_RADIUS,
    IncidentRequest,
    Params,
)
from extraction import detect_update, extract_flow, extract_location
from workflow.analysis import analyse_incident, process_update


def _chatter_event(message):
    return {
        "kind": "chatter",
        "message": message.text,
        "speaker": message.speaker,
        "timestamp": message.timestamp,
        "flow": None,
        "hydrant": None,
        "summary": "No action required.",
    }


def apply_scenario_message(plan, comparison, message, hydrants_df, model="B",
                           params=None, *,
                           planning_reserve_percent=DEFAULT_PLANNING_RESERVE_PERCENT,
                           start_radius=DEFAULT_START_RADIUS,
                           radius_step=DEFAULT_RADIUS_STEP,
                           max_radius=DEFAULT_MAX_RADIUS,
                           radius_extension=DEFAULT_RADIUS_EXTENSION_M,
                           distance_method="gis", graph=None):
    """Apply one :class:`ScenarioMessage` -> (plan, event, comparison).

    ``plan``/``comparison`` are the running state (``None``/``[]`` before the
    initial request). Returns the updated state and the single event produced.
    """
    params = params or Params()
    flow, stated = extract_flow(message.text)

    if plan is None:
        if stated and message.location is not None:
            request = IncidentRequest(
                transcript=message.text,
                location=message.location,
                planning_reserve_percent=planning_reserve_percent,
            )
            plan, event, comparison = analyse_incident(
                request, hydrants_df, model, params,
                start_radius=start_radius, radius_step=radius_step,
                max_radius=max_radius, distance_method=distance_method, graph=graph,
            )
            event["speaker"] = message.speaker
            event["timestamp"] = message.timestamp
            return plan, event, comparison
        return plan, _chatter_event(message), comparison

    facts = detect_update(message.text)
    if facts.failure or (facts.stated and facts.demand_phrase):
        new_plan, event, error = process_update(
            plan, message.text, hydrants_df, model, params,
            start_radius=start_radius, radius_step=radius_step,
            max_radius=max_radius, radius_extension=radius_extension,
            distance_method=distance_method, graph=graph,
        )
        if error is None:
            event["speaker"] = message.speaker
            event["timestamp"] = message.timestamp
            return new_plan, event, comparison

    # A location-only message moves the incident and starts a fresh analysis at
    # the new point using the active incident's stated demand.
    new_location = extract_location(message.text)
    current_flow = plan.get("stated_minimum_flow_l_min")
    if new_location is not None and current_flow is not None:
        unavailable = set(plan.get("unavailable", []))
        analysis_hydrants = hydrants_df[
            ~hydrants_df["Hydrant"].isin(unavailable)
        ]
        request = IncidentRequest(
            transcript=f"We need {current_flow:g} L/min",
            location=new_location,
            planning_reserve_percent=plan.get(
                "planning_reserve_percent", planning_reserve_percent
            ),
        )
        new_plan, event, comparison = analyse_incident(
            request, analysis_hydrants, plan.get("model", model), params,
            start_radius=start_radius, radius_step=radius_step,
            max_radius=max_radius, distance_method=distance_method, graph=graph,
        )
        event["kind"] = "location"
        event["message"] = message.text
        event["speaker"] = message.speaker
        event["timestamp"] = message.timestamp
        new_plan["unavailable"] = list(unavailable)
        return new_plan, event, comparison

    return plan, _chatter_event(message), comparison


def run_scenario(scenario, hydrants_df, model="B", params=None, *,
                 planning_reserve_percent=DEFAULT_PLANNING_RESERVE_PERCENT,
                 start_radius=DEFAULT_START_RADIUS,
                 radius_step=DEFAULT_RADIUS_STEP,
                 max_radius=DEFAULT_MAX_RADIUS,
                 radius_extension=DEFAULT_RADIUS_EXTENSION_M,
                 distance_method="gis", graph=None):
    """Apply every message in ``scenario`` -> (plan, event_log, comparison).

    Raises ``ValueError`` if the scenario never establishes an incident (i.e.
    no message states a flow *and* carries a location).
    """
    params = params or Params()
    plan = None
    comparison = []
    event_log = []
    for message in scenario.messages:
        plan, event, comparison = apply_scenario_message(
            plan, comparison, message, hydrants_df, model, params,
            planning_reserve_percent=planning_reserve_percent,
            start_radius=start_radius, radius_step=radius_step,
            max_radius=max_radius, radius_extension=radius_extension,
            distance_method=distance_method, graph=graph,
        )
        event_log.append(event)

    if plan is None:
        raise ValueError(
            f"Scenario '{scenario.id}' has no located water request; "
            "cannot start an incident."
        )
    return plan, event_log, comparison
