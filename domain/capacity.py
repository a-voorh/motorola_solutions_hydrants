"""Shared candidate-capacity helpers.

The single source of truth for how much flow a hydrant candidate contributes,
used by BOTH the solver and the radius sweep (candidate feasibility) so they
cannot diverge.

Each selected hydrant uses one connection and contributes its full modelled
capacity, subject only to the distance-decay assumption where applicable:

  * Model A:      ``C_i`` (no decay, no setup time).
  * Models B / C-soft / C-hard: ``C_i * exp(-r * D_i / 100)``.

Hose needs are computed as whole 30 m pieces: ``max(1, ceil(D_i / 30))``.
"""

import math


def hydrant_flow(capacity, distance, params, model):
    """Effective capacity (L/min) of one hydrant connection at ``distance``."""
    decay = model in ("B", "C-soft", "C-hard")
    return capacity * (math.exp(-params.r * distance / 100.0) if decay else 1.0)


def hose_pieces(distance, params):
    """Whole 30 m hose pieces needed to connect a hydrant at ``distance``."""
    return max(1, math.ceil(distance / params.hose_piece_m))
