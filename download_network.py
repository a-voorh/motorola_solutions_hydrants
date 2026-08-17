"""Download the street network once and save it to disk.

Fetches the street network (roads, footpaths, and cycleways) covering the
hydrant bounding box (with a small buffer) and saves it as a gpickle so the app
can load it from disk instead of re-downloading on every incident location.

Run with::

    python download_network.py
"""

import os
import pickle
import time

import osmnx as ox
import pandas as pd

# OSMnx pins the Overpass hostname to a single IP; if that IP is temporarily
# unreachable every download fails. Disable the pin so requests falls back to a
# live server (see also routing.py).
from osmnx import _http as _ox_http  # noqa: E402

_ox_http._config_dns = lambda url: None

GRAPH_PATH = "cache/street_network.pkl"
HYDRANTS_CSV = "hydrant_database.csv"
NETWORK_TYPE = "all"
BUFFER_DEG = 0.01  # ~1.1 km buffer around the hydrant extent

# Overpass mirrors to try (the default is sometimes down or rate-limited).
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
]


def _download(bbox):
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        for attempt in range(3):
            ox.settings.overpass_url = url
            try:
                print(f"  trying {url} (attempt {attempt + 1}) ...")
                return ox.graph_from_bbox(bbox, network_type=NETWORK_TYPE)
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"  failed: {e}")
                time.sleep(5)
    raise last_err


def main():
    df = pd.read_csv(HYDRANTS_CSV)
    north = df["Latitude"].max() + BUFFER_DEG
    south = df["Latitude"].min() - BUFFER_DEG
    east = df["Longitude"].max() + BUFFER_DEG
    west = df["Longitude"].min() - BUFFER_DEG
    print(f"Bounding box: lat {south:.4f}..{north:.4f}, lon {west:.4f}..{east:.4f}")

    print(f"Downloading {NETWORK_TYPE} street network (this may take a while)...")
    # graph_from_bbox expects (left, bottom, right, top) = (west, south, east, north).
    graph = _download((west, south, east, north))

    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    with open(GRAPH_PATH, "wb") as f:
        pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Saved {len(graph):,} nodes, {len(graph.edges):,} edges -> {GRAPH_PATH}")


if __name__ == "__main__":
    main()
