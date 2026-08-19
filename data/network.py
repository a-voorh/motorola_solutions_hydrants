"""Persisted street-network loading.

The street network (roads, footpaths, and cycleways) is downloaded once by
``download_network.py`` and stored at ``cache/street_network.pkl``.
``get_or_build_graph`` loads it, falling back to a live download around the
given location if the file is missing.

The Streamlit ``@st.cache_resource`` wrapper lives in ``graph_cache.py`` so this
module stays free of UI dependencies.
"""

import os
import pickle
from pathlib import Path

GRAPH_PATH = Path(__file__).resolve().parent.parent / "cache" / "street_network.pkl"
NETWORK_TYPE = "all"


def load_persisted_graph(path=GRAPH_PATH):
    """Load a persisted graph from disk (pure pickle read)."""
    with open(path, "rb") as f:
        return pickle.load(f)


def get_or_build_graph(lat, lon, dist, network_type=NETWORK_TYPE):
    """Return the persisted graph, or download a local one if the file is absent."""
    if os.path.exists(GRAPH_PATH):
        return load_persisted_graph(GRAPH_PATH)
    from routing.network import build_graph

    return build_graph(lat, lon, dist, network_type)
