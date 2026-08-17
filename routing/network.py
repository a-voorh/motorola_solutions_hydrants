"""Street-network routing via OSMnx.

Distances follow roads/paths (the OSM graph), not straight lines. Kept separate
so the (heavy) osmnx dependency is only imported when network routing is
actually used.

The fire and each hydrant are snapped to the nearest road *edge* (perpendicular
projection onto the road centreline), not the nearest node, so the off-road
connector is short and perpendicular to the street. A network distance is then
the two perpendicular connectors plus the shortest on-road path between the two
projected points; ``route_geometry`` draws exactly that same path.
"""

import networkx as nx
import numpy as np
import osmnx as ox
from geopy.distance import geodesic
from shapely import ops as _shapely_ops
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

# OSMnx pins the Overpass hostname to a single IP (via socket.gethostbyname)
# and reuses it for every request. If that IP is temporarily unreachable, every
# download fails with "Connection refused" even though other hosts are up.
# Disable the pin so requests uses normal multi-IP resolution and falls back to
# a live server.
from osmnx import _http as _ox_http  # noqa: E402

_ox_http._config_dns = lambda url: None


def build_graph(lat, lon, dist, network_type="all"):
    """Download/build the OSM graph around (lat, lon) within ``dist`` meters."""
    return ox.graph.graph_from_point((lat, lon), dist=dist, network_type=network_type)


def _edge_index(graph):
    """Build (and memoize) a shapely STRtree of every edge's geometry.

    Returns ``(tree, geoms, meta)`` where ``meta`` is a parallel list of
    ``(u, v, length_m)`` edge metadata.
    """
    cached = graph.graph.get("_hydrant_edge_index")
    if cached is not None:
        return cached
    geoms = []
    meta = []
    for u, v, k, data in graph.edges(keys=True, data=True):
        geom = data.get("geometry")
        if geom is None:
            geom = LineString([
                (graph.nodes[u]["x"], graph.nodes[u]["y"]),
                (graph.nodes[v]["x"], graph.nodes[v]["y"]),
            ])
        geoms.append(geom)
        meta.append((u, v, data.get("length", 0.0)))
    index = (STRtree(geoms), geoms, meta)
    graph.graph["_hydrant_edge_index"] = index
    return index


def _snap_to_edge(graph, lat, lon, index):
    """Project ``(lat, lon)`` onto its nearest road edge.

    Returns a dict with the edge's endpoint nodes ``u``/``v``, the perpendicular
    ``off`` distance (m), the projection distance ``s`` along the edge (degrees),
    the along-edge distances ``d_u``/``d_v`` to each endpoint (m), the ``proj``
    Point, the edge ``geom`` LineString and a direction-insensitive ``edge_key``.
    """
    tree, geoms, meta = index
    point = Point(lon, lat)
    i = tree.nearest(point)
    geom = geoms[i]
    s = geom.project(point)
    proj = geom.interpolate(s)
    off = geodesic((lat, lon), (proj.y, proj.x)).meters
    u, v, length_m = meta[i]
    total = geom.length
    frac = (s / total) if total > 0 else 0.0
    return {
        "u": u, "v": v, "off": off, "proj": proj, "geom": geom,
        "length_m": length_m, "s": s,
        "d_u": frac * length_m, "d_v": (1.0 - frac) * length_m,
        "edge_key": frozenset((u, v)),
    }


def _subsegment(geom, s_from, s_to):
    """Return ``[(lat, lon), ...]`` along ``geom`` from distance ``s_from`` to ``s_to``."""
    if abs(s_from - s_to) < 1e-9:
        return []
    sub = _shapely_ops.substring(geom, min(s_from, s_to), max(s_from, s_to))
    pts = [(lat, lon) for lon, lat in sub.coords]
    if s_from > s_to:
        pts.reverse()
    return pts


def _path_geometry(graph, path):
    """Reconstruct a node path following real edge geometry (road curvature)."""
    coords = []
    for u, v in zip(path[:-1], path[1:]):
        edge = min(
            graph[u][v].items(),
            key=lambda kv: kv[1].get("length", float("inf")),
        )[1]
        geom = edge.get("geometry")
        if geom is not None:
            coords.extend((y, x) for x, y in geom.coords)  # (lon, lat) -> (lat, lon)
        else:
            coords.append((graph.nodes[u]["y"], graph.nodes[u]["x"]))
            coords.append((graph.nodes[v]["y"], graph.nodes[v]["x"]))
    return coords


def _snap_distances(graph, fire_lat, fire_lon, lats, lons, cutoff=None):
    """Street distances (m) from the fire to each (lat, lon) target.

    Snap each point to its nearest edge and route between the projected points.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    index = _edge_index(graph)

    f = _snap_to_edge(graph, fire_lat, fire_lon, index)
    fire_entries = [(f["u"], f["d_u"]), (f["v"], f["d_v"])]

    dijk = {}
    for node, _ in fire_entries:
        if node in dijk:
            continue
        if cutoff is not None and np.isfinite(cutoff):
            dijk[node], _ = nx.single_source_dijkstra(graph, node, weight="length", cutoff=cutoff)
        else:
            dijk[node], _ = nx.single_source_dijkstra(graph, node, weight="length")

    out = np.full(len(lats), np.inf)
    for i in range(len(lats)):
        h = _snap_to_edge(graph, float(lats[i]), float(lons[i]), index)

        if h["edge_key"] == f["edge_key"]:
            road = abs(h["s"] - f["s"]) / f["geom"].length * f["length_m"] if f["geom"].length > 0 else 0.0
            out[i] = f["off"] + road + h["off"]
            continue

        best = np.inf
        for en, d_entry in fire_entries:
            dd = dijk[en]
            for ex, d_exit in ((h["u"], h["d_u"]), (h["v"], h["d_v"])):
                road = dd.get(ex)
                if road is None:
                    continue
                total = f["off"] + d_entry + road + d_exit + h["off"]
                if total < best:
                    best = total
        out[i] = best
    return out


def nearby_hydrants_network(fire_lat, fire_lon, radius_m, hydrants_df, graph, max_results=None):
    """Hydrants within ``radius_m`` by street distance, sorted by distance.

    Returns a DataFrame indexed by Hydrant with columns Distance_m and
    Capacity_L_min (same shape as the geodesic/Manhattan variants).
    """
    import pandas as pd

    avail = hydrants_df[hydrants_df["Available"] == True].copy()
    avail["Distance_m"] = _snap_distances(
        graph, fire_lat, fire_lon,
        avail["Latitude"].to_numpy(), avail["Longitude"].to_numpy(),
        cutoff=radius_m,
    )
    within = avail[avail["Distance_m"] <= radius_m].sort_values("Distance_m")
    if max_results is not None:
        within = within.head(max_results)
    return within.set_index("Hydrant")[["Distance_m", "Capacity_L_min"]]


def route_geometry(graph, lat1, lon1, lat2, lon2):
    """Return ``[(lat, lon), ...]`` polyline of the street route between two points.

    The polyline runs from the first point's projection on the road to the
    second point's projection on the road (so callers can draw the short
    off-road connectors separately).
    """
    index = _edge_index(graph)
    a = _snap_to_edge(graph, lat1, lon1, index)
    b = _snap_to_edge(graph, lat2, lon2, index)

    if a["edge_key"] == b["edge_key"]:
        return _subsegment(a["geom"], a["s"], b["s"])

    entries = [(a["u"], a["d_u"], a["s"]), (a["v"], a["d_v"], a["s"])]
    exits = [(b["u"], b["d_u"], b["s"]), (b["v"], b["d_v"], b["s"])]

    best = None  # (total, en, ex, path, s_entry, s_exit)
    for en, d_entry, s_entry in entries:
        for ex, d_exit, s_exit in exits:
            try:
                length, path = nx.bidirectional_dijkstra(graph, en, ex, weight="length")
            except nx.NetworkXNoPath:
                continue
            total = a["off"] + d_entry + length + d_exit + b["off"]
            if best is None or total < best[0]:
                best = (total, en, ex, path, s_entry, s_exit)
    if best is None:
        return []

    _, en, ex, path, s_entry, s_exit = best

    coords = []
    entry_s = 0.0 if en == a["u"] else a["geom"].length
    coords.extend(_subsegment(a["geom"], s_entry, entry_s))
    coords.extend(_path_geometry(graph, path))
    exit_s = 0.0 if ex == b["u"] else b["geom"].length
    coords.extend(_subsegment(b["geom"], exit_s, s_exit))
    return coords
