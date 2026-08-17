"""Compatibility shim: Streamlit-cached street-network loader.

Loads the pre-downloaded street network from disk (see ``download_network.py``
and ``data.network``) via ``st.cache_resource`` so it is read once per process.
"""

import streamlit as st

from data.network import NETWORK_TYPE, get_or_build_graph


@st.cache_resource(show_spinner="Loading road network…")
def get_graph(lat, lon, dist, network_type=NETWORK_TYPE):
    """Return the shared street network (persisted, or a local fallback)."""
    return get_or_build_graph(lat, lon, dist, network_type)


__all__ = ["get_graph", "NETWORK_TYPE"]
