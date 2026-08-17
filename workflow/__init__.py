"""Workflow layer: application use cases.

Public API:
  * ``analyse_incident``       -- one IncidentRequest -> (plan, event, comparison).
  * ``run_initial_analysis``   -- compatibility wrapper (transcript + lat/lon).
  * ``apply_update``           -- apply a failure/demand update to a plan.
  * ``process_update``         -- apply an update and build the logging event.
  * ``compare_models``         -- run A/B/C over one candidate set.
  * ``planning_target_flow`` / ``summarize_flow`` / ``flow_status_lines``
                               -- planning-reserve semantics and status.
"""

from workflow.analysis import analyse_incident, apply_update, process_update, run_initial_analysis
from workflow.compare import compare_models
from workflow.plan import _capacity_of, _plan_objective
from workflow.planning import flow_status_lines, planning_target_flow, summarize_flow
from workflow.scenario import apply_scenario_message, run_scenario

__all__ = [
    "analyse_incident",
    "run_initial_analysis",
    "apply_update",
    "process_update",
    "compare_models",
    "planning_target_flow",
    "summarize_flow",
    "flow_status_lines",
    "apply_scenario_message",
    "run_scenario",
    "_capacity_of",
    "_plan_objective",
]
