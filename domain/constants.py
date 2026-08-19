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
DEFAULT_RADIUS_EXTENSION_M = 200  # extra search radius (m) allowed on recompute
DEFAULT_CANDIDATE_MARGIN_M = 100  # extra radius (m) kept in the initial candidate pool
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

MODEL_DESCRIPTIONS = {
    "A": r"""### Model A — Naive baseline

**What it does:** Selects hydrants using nominal capacity and route distance. It does not model connection setup time, friction losses, or hose inventory.

**Mathematical formulation:** For each candidate hydrant $i$, choose $x_i \in \{0,1\}$ and let $u \geq 0$ represent unmet demand:

$$\sum_i C_i x_i + u \geq D$$

The lexicographic objective first minimizes $u$, then minimizes total laying time:

$$\sum_i \frac{d_i}{v}x_i$$

**Parameters:** Uses $v$ (hose deployment rate). `hose_piece_m` is reported, but `q`, `gamma`, `carried_pieces`, and `max_lines_per_hydrant` are not used in the optimization.

**Optimizer:** SciPy `scipy.optimize.milp`, using the HiGHS mixed-integer programming solver.""",
    "B": r"""### Model B — Deployment-time

**What it does:** Extends Model A by including a fixed connection/setup time, so both route distance and the number of hydrant connections affect the recommendation.

**Mathematical formulation:** The demand constraint is the same as Model A:

$$\sum_i C_i x_i + u \geq D$$

After minimizing unmet demand $u$, it minimizes:

$$\sum_i \left(\frac{d_i}{v} + q\right)x_i$$

**Parameters:** Uses $v$ (hose deployment rate) and $q$ (setup time per hydrant connection). `hose_piece_m` is reported, but `gamma`, `carried_pieces`, and `max_lines_per_hydrant` are not used in the optimization.

**Optimizer:** SciPy `scipy.optimize.milp`, using the HiGHS mixed-integer programming solver.""",
    "C-soft": r"""### Model C-soft — Inventory-aware with reinforcement

**What it does:** Models distance-related friction loss, multiple parallel hose lines, and carried hose inventory. It may request reinforcement hose when the carried inventory is insufficient, but penalizes that reinforcement.

**Mathematical formulation:** For hydrant $i$ and line configuration $n$, choose $y_{i,n} \in \{0,1\}$, with at most one configuration per hydrant. Let $u \geq 0$ be unmet demand and $o \geq 0$ be extra hose pieces. Usable capacity is:

$$a_{i,n} = \min\left(C_i, \frac{n\gamma}{\sqrt{d_i}}\right)$$

The hose requirement for one line is $h_i = \max(1, \lceil d_i / hose\_piece\_m \rceil)$. The main constraints are:

$$\sum_{i,n} a_{i,n}y_{i,n} + u \geq D$$

$$\sum_{i,n} n h_i y_{i,n} \leq budget + o$$

The lexicographic objective minimizes unmet demand, then reinforcement pieces $o$, then deployment effort:

$$\sum_{i,n} n\left(\frac{h_i \cdot hose\_piece\_m}{v} + q\right)y_{i,n}$$

**Parameters:** Uses $v$, $q$, $\gamma$, `hose_piece_m`, `carried_pieces`, and `max_lines_per_hydrant`.

**Optimizer:** SciPy `scipy.optimize.milp`, using the HiGHS mixed-integer programming solver.""",
    "C-hard": r"""### Model C-hard — Inventory-aware with fixed inventory

**What it does:** Uses the same friction-loss and parallel-line model as C-soft, but does not allow reinforcement. All hose must fit within the available carried inventory.

**Mathematical formulation:** It uses the same $y_{i,n}$, $u$, $a_{i,n}$, and $h_i$ definitions as C-soft, but applies a hard inventory constraint:

$$\sum_{i,n} n h_i y_{i,n} \leq budget$$

The lexicographic objective minimizes unmet demand first, then deployment effort. There is no reinforcement variable $o$.

**Parameters:** Uses $v$, $q$, $\gamma$, `hose_piece_m`, `carried_pieces`, and `max_lines_per_hydrant`.

**Optimizer:** SciPy `scipy.optimize.milp`, using the HiGHS mixed-integer programming solver.""",
}


def flow_tolerance(value: float) -> float:
    """Small absolute tolerance for comparing flows of magnitude ``value``."""
    return FLOW_TOL * max(1.0, abs(value))
