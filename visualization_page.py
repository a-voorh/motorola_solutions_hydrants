"""Visualization page: real map of hydrants + network/geodesic/Manhattan comparison."""

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from data import get_hydrants
from graph_cache import get_graph
from routing import nearby_hydrants, nearby_hydrants_geodesic, nearby_hydrants_network
from ui.map import add_route, set_location_from_click
from workflow import _capacity_of

st.title("Hydrant visualization")

hydrants = get_hydrants()

col1, col2, col3 = st.columns(3)
with col1:
    lat = st.number_input("Fire latitude", key="fire_lat", value=None, format="%.6f")
with col2:
    lon = st.number_input("Fire longitude", key="fire_lon", value=None, format="%.6f")
with col3:
    radius = st.number_input("Radius (m)", key="fire_radius", value=500, min_value=0)

distance_method = st.radio(
    "Distance method",
    options=["network", "gis", "manhattan"],
    format_func=lambda m: {"network": "Street network", "gis": "GIS (geodesic)", "manhattan": "Manhattan"}[m],
    index=0,
    key="distance_method",
    horizontal=True,
)

center = (lat, lon) if (lat is not None and lon is not None) else (hydrants["Latitude"].mean(), hydrants["Longitude"].mean())
m = folium.Map(location=center, zoom_start=13)

# All hydrants (small gray markers).
for _, row in hydrants.iterrows():
    folium.CircleMarker(
        location=(row["Latitude"], row["Longitude"]),
        radius=1.5,
        color="gray",
        fill=True,
        fill_opacity=0.4,
    ).add_to(m)

graph = None
method = distance_method
net_all = None

if lat is not None and lon is not None:
    if method == "network":
        try:
            graph = get_graph(lat, lon, radius + 400)
            net_all = nearby_hydrants_network(lat, lon, radius, hydrants, graph)
        except Exception as e:
            st.warning(f"Street-network routing unavailable ({e}). Falling back to GIS.")
            method = "gis"
            graph = None

    if method == "network":
        near = net_all[net_all["Distance_m"] <= radius]
    elif method == "gis":
        near = nearby_hydrants_geodesic(lat, lon, radius, hydrants)
    else:
        near = nearby_hydrants(lat, lon, radius, hydrants)

    locs = hydrants.set_index("Hydrant")
    for i, h in enumerate(near.index):
        folium.CircleMarker(
            location=(locs.loc[h, "Latitude"], locs.loc[h, "Longitude"]),
            radius=4,
            color="blue",
            fill=True,
            fill_opacity=0.7,
        ).add_to(m)
        if graph is not None and i < 30:
            add_route(m, lat, lon, locs.loc[h, "Latitude"], locs.loc[h, "Longitude"],
                      graph=graph, street_routes=True, color="green", weight=2, opacity=0.7)

    folium.Marker((lat, lon), icon=folium.Icon(color="red", icon="info-sign")).add_to(m)
    folium.Circle(location=(lat, lon), radius=radius, color="red", weight=2, fill=False).add_to(m)

map_data = st_folium(m, height=600, key="vis_map")
set_location_from_click(map_data)
st.caption("Click the map to set the incident location.")

if lat is not None and lon is not None:
    man = nearby_hydrants(lat, lon, 1e9, hydrants)
    geo = nearby_hydrants_geodesic(lat, lon, 1e9, hydrants)
    comp = man[["Distance_m", "Capacity_L_min"]].rename(columns={"Distance_m": "Manhattan_m"})
    comp = comp.join(geo[["Distance_m"]].rename(columns={"Distance_m": "Geodesic_m"}))
    if net_all is not None:
        comp = comp.join(net_all[["Distance_m"]].rename(columns={"Distance_m": "Network_m"}))
    comp["Overestimate_x"] = comp["Manhattan_m"] / comp["Geodesic_m"]

    key_col = {"network": "Network_m", "gis": "Geodesic_m", "manhattan": "Manhattan_m"}[method]
    if key_col in comp.columns:
        comp = comp[comp[key_col] <= radius].sort_values(key_col)

    st.subheader(f"Candidates within {radius} m (sorted by {key_col.replace('_m', '')})")
    st.dataframe(comp.reset_index())
    st.caption("Manhattan (city-block) overestimates straight-line distance; GIS is the WGS84 "
               "great-circle distance; Street network follows roads/paths (green lines on the map).")
else:
    st.info("Enter a fire location to highlight nearby hydrants and compare distances.")

# --- current plan from the main page (if present) ---
plan = st.session_state.get("plan")
if plan and plan.get("selected"):
    st.subheader("Current plan (from the main page)")
    rows = [
        {"Hydrant": h, "Capacity (L/min)": int(info["capacity"]), "Distance (m)": round(info["distance"], 1)}
        for h, info in plan["selected"].items()
    ]
    st.dataframe(pd.DataFrame(rows).set_index("Hydrant"))
    st.write(f"Nominal capacity: **{_capacity_of(plan['selected']):g} L/min**")
    if plan.get("objective") is not None:
        st.write(f"Objective: **{plan['objective']:.2f} time units**")
