"""Shared constants and pure helpers for the hydrant recommender.

This module has no Streamlit, routing, or solver-library dependencies. It is
the single source of truth for default parameters, model identifiers, and the
flow-comparison tolerance.
"""

from __future__ import annotations

# Default model parameters.
DEFAULT_V = 5.0       # hose deployment rate (m per time unit)
DEFAULT_Q = 10.0      # deployment time per hydrant connection (time units)
DEFAULT_R = 0.058     # illustrative flow decay per 100 m of hose

# Default search / planning parameters.
DEFAULT_START_RADIUS = 30
DEFAULT_RADIUS_STEP = 30
DEFAULT_MAX_RADIUS = 1500
DEFAULT_PLANNING_RESERVE_PERCENT = 50  # illustrative planning reserve (%)

# Hose-inventory constants.
HOSE_PIECE_M = 30.0      # hose piece length (m)
CARRIED_PIECES = 12      # hose pieces carried on the vehicle

# Policy: hose at a failed hydrant is NOT immediately recoverable. While False,
# a failed hydrant's hose pieces stay counted in the committed-piece total and
# are not released back to the carried allowance. Flipping this to True would
# release them for reuse on later reinforcement.
RECOVER_FAILED_HYDRANT_HOSE = False

# Shared tolerance for flow comparisons. Used to judge whether a delivered flow
# "meets" a target so solver rounding (e.g. a 1e-9 L/min residual) never shows
# up as a failure state.
FLOW_TOL = 1e-6

MODEL_NAMES = ("A", "B", "C-soft", "C-hard")
MODEL_LABELS = {
    "A": "Naive baseline",
    "B": "Decayed setup-time",
    "C-soft": "Soft hose-inventory",
    "C-hard": "Hard hose-inventory",
}

# Selector labels grouping the two inventory-aware models under one heading.
MODEL_OPTION_LABELS = {
    "A": "A — Naive baseline",
    "B": "B — Decayed setup-time",
    "C-soft": "Inventory-aware — C-soft (soft reinforcement)",
    "C-hard": "Inventory-aware — C-hard (fixed inventory)",
}


def flow_tolerance(value: float) -> float:
    """Small absolute tolerance for comparing flows of magnitude ``value``."""
    return FLOW_TOL * max(1.0, abs(value))
