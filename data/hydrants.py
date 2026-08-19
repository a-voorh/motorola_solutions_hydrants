"""Hydrant database loading."""

from pathlib import Path

import pandas as pd

DATA_PATH = Path(__file__).resolve().parent.parent / "hydrant_database.csv"


def get_hydrants():
    """Return the full hydrant database as a DataFrame."""
    return pd.read_csv(DATA_PATH)
