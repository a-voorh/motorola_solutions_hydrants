"""Shared candidate-capacity helpers.

The single source of truth for how much flow a hydrant candidate contributes,
used by BOTH the solver and the radius sweep (candidate feasibility) so they
cannot diverge.

Model A and Model B use the hydrant's nominal capacity directly (no distance
decay and no friction-loss model). Model C (C-soft / C-hard) precomputes the
usable capacity of each discrete hydrant/line configuration using an
experimental friction-loss proxy:

    a[i, n] = min(C_i, n * gamma / sqrt(d_i))

where ``C_i`` is nominal capacity, ``d_i`` the route distance, ``n`` the number
of parallel hose lines, and ``gamma`` an experimental hydraulic calibration
parameter (NOT physically calibrated). Longer routes reduce usable flow;
parallel lines increase it; usable flow never exceeds nominal capacity.

Hose needs are computed as whole pieces of ``params.hose_piece_m`` metres:
``max(1, ceil(d_i / hose_piece_m))``.
"""

import math

from domain.constants import MIN_DISTANCE_M


def hose_pieces(distance, params):
    """Whole hose pieces needed for ONE line to a hydrant at ``distance``."""
    return max(1, math.ceil(distance / params.hose_piece_m))


def usable_capacity(capacity, distance, params, n_lines):
    """Usable capacity (L/min) of ``n_lines`` parallel lines at ``distance``.

    Applies the friction-loss proxy ``n * gamma / sqrt(d)`` capped at nominal
    capacity. ``n_lines`` must be >= 1. Handles zero / near-zero distances.
    """
    d = max(distance, MIN_DISTANCE_M)
    return min(capacity, n_lines * params.gamma / math.sqrt(d))


def max_usable_capacity(capacity, distance, params):
    """Maximum usable capacity over all allowed line configurations (n up to
    ``params.max_lines_per_hydrant``). Used by the radius sweep for Model C so
    friction-limited models do not prematurely exclude farther hydrants."""
    return usable_capacity(capacity, distance, params, params.max_lines_per_hydrant)


def deployment_time(distance, params, n_lines=1):
    """Deployment effort for ``n_lines`` parallel lines to a hydrant.

    Each line is ``h_i`` whole pieces of ``hose_piece_m`` metres laid at speed
    ``v`` plus a per-line connection/setup time ``q``:

        n_lines * (h_i * hose_piece_m / v + q)
    """
    h_i = hose_pieces(distance, params)
    return n_lines * (h_i * params.hose_piece_m / params.v + params.q)
