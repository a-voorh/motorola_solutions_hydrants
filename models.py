"""Compatibility shim.

Re-exports the public API previously available from ``models``. The real code
now lives in ``domain`` (types/constants) and ``solver`` (optimisation).
"""

from domain import (
    CARRIED_PIECES,
    DEFAULT_Q,
    DEFAULT_R,
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
    flow_tolerance,
)
from solver import build_recommendation, solve_model

__all__ = [
    "CARRIED_PIECES",
    "DEFAULT_Q",
    "DEFAULT_R",
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
    "flow_tolerance",
    "build_recommendation",
    "solve_model",
]
