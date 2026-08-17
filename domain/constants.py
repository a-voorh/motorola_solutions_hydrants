"""Shared constants and pure helpers for the hydrant recommender.

This module has no Streamlit, routing, or solver-library dependencies. It is
the single source of truth for default parameters, model identifiers, and the
flow-comparison tolerance.
"""

from __future__ import annotations

# Default model parameters.
DEFAULT_V = 5.0       # hose deployment rate (m per time unit)
DEFAULT_Q = 10.0      # deployment time per hydrant connection (time units)

# Hydraulic calibration parameter (experimental -- NOT physically calibrated).
#
# ``gamma`` bundles the unknown available pressure and hose friction
# characteristics behind the simplified physical relationship
# ``friction loss ~ d * Q^2``. For a single hose line it yields the usable flow
# ``Q = gamma / sqrt(d)`` (L/min, with ``d`` in metres), so ``gamma`` carries
# units of ``L/min * sqrt(m)``. The value below is a placeholder to let the
# prototype run; it has not been calibrated against real apparatus.
DEFAULT_GAMMA = 10000.0

# Default search / planning parameters.
DEFAULT_START_RADIUS = 30
DEFAULT_RADIUS_STEP = 30
DEFAULT_MAX_RADIUS = 1500
DEFAULT_PLANNING_RESERVE_PERCENT = 50  # illustrative planning reserve (%)

# Default incident location for map-based pages (Copenhagen demo area). The
# red "i" marker is placed here until the user clicks elsewhere to move it.
DEFAULT_INCIDENT_LOCATION = (55.664178, 12.607972)

# Hose-inventory constants.
HOSE_PIECE_M = 15.0      # hose piece length (m)
CARRIED_PIECES = 30      # hose pieces carried on the vehicle

# Parallel-line configuration bound considered by the prototype. This is NOT a
# claim that Danish hydrants physically support at most two lines; it simply
# defines which configurations the current prototype considers.
DEFAULT_MAX_LINES_PER_HYDRANT = 2

# Guard against zero / near-zero route distances in the friction-loss proxy.
MIN_DISTANCE_M = 1e-6

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
    "B": "Deployment-time",
    "C-soft": "Soft hose-inventory (parallel lines + friction-loss proxy)",
    "C-hard": "Hard hose-inventory (parallel lines + friction-loss proxy)",
}

# Selector labels grouping the two inventory-aware models under one heading.
MODEL_OPTION_LABELS = {
    "A": "A — Naive baseline",
    "B": "B — Deployment-time",
    "C-soft": "Inventory-aware — C-soft (soft reinforcement)",
    "C-hard": "Inventory-aware — C-hard (fixed inventory)",
}


def flow_tolerance(value: float) -> float:
    """Small absolute tolerance for comparing flows of magnitude ``value``."""
    return FLOW_TOL * max(1.0, abs(value))
