"""Compatibility shim.

Re-exports the public API previously available from ``models``. The real code
now lives in ``domain`` (types/constants) and ``solver`` (optimisation).
"""

from domain import (
    CARRIED_PIECES,
    DEFAULT_GAMMA,
    DEFAULT_MAX_LINES_PER_HYDRANT,
    DEFAULT_Q,
    DEFAULT_V,
    FLOW_TOL,
    HOSE_PIECE_M,
    MODEL_LABELS,
    MODEL_NAMES,
    RECOVER_FAILED_HYDRANT_HOSE,
    DispatcherPreference,
    HydrantLine,
    IncidentRequest,
    ModelResult,
    Params,
    UpdateFacts,
    deployment_time,
    flow_tolerance,
    hose_pieces,
    max_usable_capacity,
    usable_capacity,
)
from solver import build_recommendation, solve_model

__all__ = [
    "CARRIED_PIECES",
    "DEFAULT_GAMMA",
    "DEFAULT_MAX_LINES_PER_HYDRANT",
    "DEFAULT_Q",
    "DEFAULT_V",
    "FLOW_TOL",
    "HOSE_PIECE_M",
    "MODEL_LABELS",
    "MODEL_NAMES",
    "RECOVER_FAILED_HYDRANT_HOSE",
    "DispatcherPreference",
    "HydrantLine",
    "IncidentRequest",
    "ModelResult",
    "Params",
    "UpdateFacts",
    "deployment_time",
    "flow_tolerance",
    "hose_pieces",
    "max_usable_capacity",
    "usable_capacity",
    "build_recommendation",
    "solve_model",
]
