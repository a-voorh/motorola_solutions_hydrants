"""Download Copenhagen hydrants from Brøndby's public ArcGIS FeatureServer.

Regenerates ``hydrant_database.csv`` (the file consumed by ``adapters.py``) with
the schema used across the app::

    Hydrant, Latitude, Longitude, Capacity_L_min, Available,
    Capacity_source, Public_hydrant_object_id, Model_type, Location_source

Scope is restricted to the city of Copenhagen (including the northern part of
Amager) plus the Frederiksberg enclave. The western suburbs (Vestegnen:
Brøndby, Glostrup, Hvidovre, Rødovre, Ishøj, Vallensbæk, ...) and the northern
suburbs (Nordvand: Gentofte, Lyngby, ...) are dropped via their ``Source``
attribute.

The layer is paginated with ``resultOffset`` / ``resultRecordCount=1000``
because a single query is capped at ``maxRecordCount`` (1000). Only hydrants
with ``funktionsstatus = 'Aktiv'`` are kept, features are deduplicated by their
``OBJECTID``, and point geometries (requested in WGS84 via ``outSR=4326``) are
written out as Latitude / Longitude. Hydrants are renumbered 1..N so the file
is one contiguous sequence (``Hydrant = H0001...``).

Capacities: the source layer carries no verified hydraulic capacity for these
municipalities (``ydelse`` is empty), so a nominal flow is modelled from the
pipe diameter embedded in ``modeltype`` and flagged
``Capacity_source = "simulated_from_model_type"``. If a verified ``ydelse``
value ever appears it is used verbatim and marked ``verified_ydelse``.

Run with::

    python download_hydrants.py
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

import pandas as pd

SERVER = "https://gisservices.brondby.dk/arcgis/rest/services/ekst_mapservice"
LAYER_URL = f"{SERVER}/Beredeskab_Hovedstaden/FeatureServer/0/query"

WHERE = "funktionsstatus='Aktiv'"
PAGE_SIZE = 1000
OUT_CSV = "hydrant_database.csv"

# Municipalities kept: Copenhagen city (incl. northern Amager) and the
# Frederiksberg enclave. West/north suburbs are excluded.
KEEP_SOURCES = {
    "6100_Brandhaner__Koebenhavn",
    "6100_Brandhaner__Frederiksberg",
}

# Nominal flow (L/min) assumed per nominal pipe diameter. These are synthetic
# engineering placeholders, not measured values.
NOMINAL_CAPACITY_BY_DIAMETER_MM = {
    32: 400,
    40: 500,
    50: 600,
    63: 900,
    80: 1200,
    90: 1500,
    100: 1600,
    110: 2000,
    125: 2600,
    150: 3600,
    160: 4200,
}
DEFAULT_CAPACITY_L_MIN = 800  # used when no diameter/capacity is available

_SCHEMA = [
    "Hydrant",
    "Latitude",
    "Longitude",
    "Capacity_L_min",
    "Available",
    "Capacity_source",
    "Public_hydrant_object_id",
    "Model_type",
    "Location_source",
]


def fetch_page(offset: int) -> dict:
    """Return one raw ArcGIS JSON page of active hydrant features."""
    params = urllib.parse.urlencode(
        {
            "where": WHERE,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
    )
    with urllib.request.urlopen(f"{LAYER_URL}?{params}", timeout=60) as resp:
        return json.load(resp)


def download_active_hydrants() -> list[dict]:
    """Fetch every active hydrant, paginating until a short or empty page."""
    features: list[dict] = []
    offset = 0
    while True:
        batch = fetch_page(offset).get("features", [])
        features.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return features


def extract_diameter_mm(modeltype) -> int | None:
    """Parse a nominal diameter (mm) out of free-text ``modeltype``.

    Handles the two encodings found in the source, e.g. ``"KV 1916, 100 mm"``
    and ``"Dim: 90"`` / ``"Dim 80"``. A zero diameter (``"Dim: 0"``) is treated
    as unknown.
    """
    if modeltype is None:
        return None
    text = str(modeltype)
    m = re.search(r"Dim:?\s*(\d+)", text, re.IGNORECASE)
    if m:
        value = int(m.group(1))
        return value if value > 0 else None
    m = re.search(r"(\d+)\s*mm", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def modelled_capacity(modeltype) -> int:
    """Synthetic nominal capacity derived from the hydrant's pipe diameter."""
    diameter = extract_diameter_mm(modeltype)
    if diameter is None:
        return DEFAULT_CAPACITY_L_MIN
    return NOMINAL_CAPACITY_BY_DIAMETER_MM.get(diameter, DEFAULT_CAPACITY_L_MIN)


def to_record(feature: dict) -> dict:
    """Map one ArcGIS feature to a row of the target CSV schema."""
    attrs = feature["attributes"]
    oid = attrs["OBJECTID"]
    geometry = feature.get("geometry") or {}

    capacity = modelled_capacity(attrs.get("modeltype"))
    capacity_source = "simulated_from_model_type"

    ydelese = attrs.get("ydelse")
    if ydelese is not None and str(ydelse).strip() != "":
        try:
            capacity = int(float(ydelse))
            capacity_source = "verified_ydelse"
        except (TypeError, ValueError):
            pass

    return {
        "Latitude": round(float(geometry["y"]), 6),
        "Longitude": round(float(geometry["x"]), 6),
        "Capacity_L_min": capacity,
        "Available": True,
        "Capacity_source": capacity_source,
        "Model_type": attrs.get("modeltype"),
        "Location_source": attrs.get("Source"),
    }


def main() -> None:
    print(f"Downloading active hydrants ({WHERE}) ...")
    features = download_active_hydrants()

    records: list[dict] = []
    seen: set[int] = set()
    skipped_source = 0
    for f in features:
        attrs = f["attributes"]
        oid = attrs["OBJECTID"]
        if oid in seen:
            continue
        seen.add(oid)
        if attrs.get("Source") not in KEEP_SOURCES:
            skipped_source += 1
            continue
        records.append(to_record(f))

    print(f"  fetched {len(features)} features, kept {len(records)} "
          f"(dropped {skipped_source} outside Copenhagen/Frederiksberg)")

    df = pd.DataFrame(records)
    df = df.reset_index(drop=True)
    df["Public_hydrant_object_id"] = range(1, len(df) + 1)
    df["Hydrant"] = [f"H{i:04d}" for i in range(1, len(df) + 1)]
    df = df[_SCHEMA]

    print(f"Active hydrants: {len(df)}")
    print(
        f"Latitude bounds : {df['Latitude'].min():.6f} .. {df['Latitude'].max():.6f}"
    )
    print(
        f"Longitude bounds: {df['Longitude'].min():.6f} .. {df['Longitude'].max():.6f}"
    )
    print(f"Capacity_source counts: {df['Capacity_source'].value_counts().to_dict()}")
    print(f"Location_source counts: {df['Location_source'].value_counts().to_dict()}")

    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote {OUT_CSV} ({len(df)} rows)")


if __name__ == "__main__":
    main()
