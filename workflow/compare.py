"""Model comparison: run one scenario through all three models."""

from domain import MODEL_NAMES
from solver import solve_model


def compare_models(candidates, demand, params, hydrants_df, radius=None, distance_method="gis"):
    """Run Models A/B/C over the same candidate set; return a list of ModelResult."""
    return [
        solve_model(m, candidates, demand, params, hydrants_df,
                    radius=radius, distance_method=distance_method)
        for m in MODEL_NAMES
    ]
