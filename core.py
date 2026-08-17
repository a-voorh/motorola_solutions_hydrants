"""Compatibility shim.

Re-exports the public API previously available from ``core``. The real code now
lives in ``workflow`` (use cases / status), ``extraction`` (parsing), and
``routing`` (candidate building).
"""

from domain import (
    DEFAULT_MAX_RADIUS,
    DEFAULT_PLANNING_RESERVE_PERCENT,
    DEFAULT_Q,
    DEFAULT_R,
    DEFAULT_RADIUS_STEP,
    DEFAULT_START_RADIUS,
    DEFAULT_V,
    MODEL_NAMES,
    Params,
)
from extraction import detect_update, extract_flow
from routing import build_candidates, nearby_hydrants_geodesic
from workflow import (
    _capacity_of,
    _plan_objective,
    apply_update,
    compare_models,
    flow_status_lines,
    planning_target_flow,
    process_update,
    run_initial_analysis,
    summarize_flow,
)

__all__ = [
    "DEFAULT_MAX_RADIUS",
    "DEFAULT_PLANNING_RESERVE_PERCENT",
    "DEFAULT_Q",
    "DEFAULT_R",
    "DEFAULT_RADIUS_STEP",
    "DEFAULT_START_RADIUS",
    "DEFAULT_V",
    "MODEL_NAMES",
    "Params",
    "detect_update",
    "extract_flow",
    "build_candidates",
    "nearby_hydrants_geodesic",
    "_capacity_of",
    "_plan_objective",
    "apply_update",
    "compare_models",
    "flow_status_lines",
    "planning_target_flow",
    "process_update",
    "run_initial_analysis",
    "summarize_flow",
]
