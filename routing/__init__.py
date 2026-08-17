"""Routing layer: distance maths and candidate-set building.

Public API:
  * ``build_candidates``          -- shared candidate set (radius sweep).
  * ``nearby_hydrants``           -- Manhattan.
  * ``nearby_hydrants_geodesic``  -- WGS84 great-circle.
  * ``nearby_hydrants_network``   -- street network (OSMnx).
  * ``route_geometry``            -- street-route polyline.
  * ``build_graph``               -- download/build the OSM graph.
  * ``_nearby``, ``_ensure_committed`` -- internal workflow helpers.
"""

from routing.candidates import _ensure_committed, _nearby, build_candidates
from routing.geodesic import nearby_hydrants_geodesic
from routing.manhattan import nearby_hydrants
from routing.network import build_graph, nearby_hydrants_network, route_geometry

__all__ = [
    "build_candidates",
    "_nearby",
    "_ensure_committed",
    "nearby_hydrants",
    "nearby_hydrants_geodesic",
    "nearby_hydrants_network",
    "route_geometry",
    "build_graph",
]
