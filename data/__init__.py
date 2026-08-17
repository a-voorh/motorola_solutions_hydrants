"""Data layer: hydrant database and persisted street network.

Public API:
  * ``get_hydrants``           -- load the hydrant database (DataFrame).
  * ``DATA_PATH``              -- path to ``hydrant_database.csv``.
  * ``load_persisted_graph``   -- read the pre-downloaded street network.
  * ``get_or_build_graph``     -- load persisted graph or fall back to download.
  * ``GRAPH_PATH``, ``NETWORK_TYPE``
"""

from data.hydrants import DATA_PATH, get_hydrants
from data.network import GRAPH_PATH, NETWORK_TYPE, get_or_build_graph, load_persisted_graph
from data.scenarios import available_scenarios, default_scenario, load_scenario

__all__ = [
    "get_hydrants",
    "DATA_PATH",
    "load_persisted_graph",
    "get_or_build_graph",
    "GRAPH_PATH",
    "NETWORK_TYPE",
    "load_scenario",
    "available_scenarios",
    "default_scenario",
]
