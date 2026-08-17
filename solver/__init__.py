"""Solver layer: pure optimisation (Models A/B/C) with no UI or parsing.

Public API:
  * ``solve_model``           -- the stable dispatcher (one model -> ModelResult).
  * ``build_recommendation``  -- natural-language summary of a ModelResult.
"""

from solver.milp import build_recommendation, solve_model

__all__ = ["solve_model", "build_recommendation"]
