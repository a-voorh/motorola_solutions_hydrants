"""Hydrant database loading."""

import pandas as pd

DATA_PATH = "hydrant_database.csv"


def get_hydrants():
    """Return the full hydrant database as a DataFrame."""
    return pd.read_csv(DATA_PATH)
