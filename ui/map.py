"""Map rendering for the model playground.

Folium is imported here (not in ``ui/components.py``) so the UI components that
tests import stay free of the folium dependency. The map shows the candidate
hydrants, highlights the selected ones, and connects them to the fire along
street routes (when a graph is available) or straight lines.
"""

import math

import folium
import streamlit as st
from streamlit_folium import st_folium

from routing import route_geometry


def set_location_from_click(map_data, lat_key="fire_lat", lon_key="fire_lon"):
    """Write a map's ``last_clicked`` payload into the incident-location state."""
    if map_data and map_data.get("last_clicked"):
        st.session_state[lat_key] = map_data["last_clicked"]["lat"]
        st.session_state[lon_key] = map_data["last_clicked"]["lng"]
        st.rerun()


def render_location_picker(lat, lon, hydrants_df, key="location_picker", height=450):
    """Render a tappable full-hydrant map for choosing an incident location.

    Centered on ``(lat, lon)`` when given, otherwise on the hydrant centroid.
    Returns the ``st_folium`` click payload.
    """
    center = (lat, lon) if (lat is not None and lon is not None) else (
        hydrants_df["Latitude"].mean(), hydrants_df["Longitude"].mean())
    m = folium.Map(location=center, zoom_start=13)

    for _, row in hydrants_df.iterrows():
        folium.CircleMarker(
            location=(row["Latitude"], row["Longitude"]),
            radius=1.5,
            color="gray",
            fill=True,
            fill_opacity=0.4,
        ).add_to(m)

    if lat is not None and lon is not None:
        folium.Marker(
            (lat, lon),
            icon=folium.Icon(color="red", icon="info-sign"),
            tooltip="Incident location",
        ).add_to(m)

    return st_folium(m, height=height, key=key)


def add_route(m, fire_lat, fire_lon, hyd_lat, hyd_lon, graph=None,
              street_routes=False, color="green", weight=3, opacity=0.9):
    """Draw the route from the fire to a hydrant on a folium map.

    In street mode the route is three connected segments: a dashed off-road
    leg (fire -> road), a solid on-road path, and a dashed off-road leg
    (road -> hydrant). Otherwise a straight dashed line is drawn.
    """
    if street_routes and graph is not None:
        pts = route_geometry(graph, fire_lat, fire_lon, hyd_lat, hyd_lon)
        if pts:
            folium.PolyLine(
                [(fire_lat, fire_lon), pts[0]],
                color=color, weight=weight, opacity=opacity, dash_array="6 3",
            ).add_to(m)
            folium.PolyLine(pts, color=color, weight=weight, opacity=opacity).add_to(m)
            folium.PolyLine(
                [pts[-1], (hyd_lat, hyd_lon)],
                color=color, weight=weight, opacity=opacity, dash_array="6 3",
            ).add_to(m)
            return
    folium.PolyLine(
        [(fire_lat, fire_lon), (hyd_lat, hyd_lon)],
        color=color, weight=weight, opacity=opacity, dash_array="6 3",
    ).add_to(m)


def render_hydrant_map(fire_lat, fire_lon, candidates, selected, radius,
                       graph=None, street_routes=False, height=500,
                       unavailable=None, hydrants_df=None, key=None):
    """Render a folium map zoomed to the candidate radius.

    ``candidates`` is a DataFrame indexed by ``Hydrant`` with columns
    ``Latitude``, ``Longitude``, ``Distance_m`` and ``Capacity_L_min``.
    ``selected`` is a list of :class:`HydrantLine` (the model's chosen set).

    ``unavailable`` is an optional iterable of hydrant ids rendered as failed
    markers (their positions are looked up in ``hydrants_df``, since failed
    hydrants may be excluded from ``candidates``).

    Returns the ``st_folium`` click payload (use ``set_location_from_click`` to
    turn taps into incident-location updates).
    """
    m = folium.Map(location=(fire_lat, fire_lon), zoom_start=15)

    folium.Marker(
        (fire_lat, fire_lon),
        icon=folium.Icon(color="red", icon="info-sign"),
        tooltip="Fire location",
    ).add_to(m)
    folium.Circle(
        location=(fire_lat, fire_lon), radius=radius, color="red",
        weight=2, fill=False, dash_array="4 4",
    ).add_to(m)

    selected_ids = {s.hydrant for s in selected}

    for hid, row in candidates.iterrows():
        if hid in selected_ids:
            continue
        folium.CircleMarker(
            location=(row["Latitude"], row["Longitude"]),
            radius=4,
            color="blue",
            fill=True,
            fill_opacity=0.6,
            tooltip=(
                f"{hid} — {row['Distance_m']:.0f} m, "
                f"{row['Capacity_L_min']:.0f} L/min"
            ),
        ).add_to(m)

    for s in selected:
        folium.CircleMarker(
            location=(s.latitude, s.longitude),
            radius=7,
            color="green",
            fill=True,
            fill_opacity=0.9,
            tooltip=(
                f"{s.hydrant} — {s.distance_m:.0f} m, "
                f"{s.effective_capacity:.0f} L/min (selected)"
            ),
        ).add_to(m)

        add_route(m, fire_lat, fire_lon, s.latitude, s.longitude,
                  graph=graph, street_routes=street_routes)

    if unavailable and hydrants_df is not None:
        locs = hydrants_df.set_index("Hydrant")[["Latitude", "Longitude"]]
        for hid in unavailable:
            if hid not in locs.index:
                continue
            folium.CircleMarker(
                location=(locs.loc[hid, "Latitude"], locs.loc[hid, "Longitude"]),
                radius=7,
                color="black",
                fill=True,
                fill_opacity=0.9,
                tooltip=f"{hid} (unavailable)",
            ).add_to(m)

    m.fit_bounds([
        [fire_lat - _radius_deg_lat(radius), fire_lon - _radius_deg_lon(radius, fire_lat)],
        [fire_lat + _radius_deg_lat(radius), fire_lon + _radius_deg_lon(radius, fire_lat)],
    ])

    return st_folium(m, height=height, key=key)


def _radius_deg_lat(meters):
    return meters / 111320.0


def _radius_deg_lon(meters, lat):
    return meters / (111320.0 * math.cos(math.radians(lat)))
