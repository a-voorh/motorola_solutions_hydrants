"""Manhattan (city-block) distance candidate search.

Reimplemented from ``picking candidates.ipynb`` as pure code (identical math)
so the routing layer has no notebook-execution dependency.
"""

import numpy as np


def nearby_hydrants(fire_lat, fire_lon, radius_m, hydrants_df, max_results=None):
    """Return hydrants within ``radius_m`` (Manhattan) of the fire, sorted by distance.

    Returns a DataFrame indexed by Hydrant with columns Distance_m and Capacity_L_min.
    """
    avail = hydrants_df[hydrants_df["Available"] == True].copy()

    lat_m_per_deg = 111320.0
    lon_m_per_deg = 111320.0 * np.cos(np.radians((fire_lat + avail["Latitude"]) / 2.0))

    dx = (avail["Longitude"] - fire_lon) * lon_m_per_deg
    dy = (avail["Latitude"] - fire_lat) * lat_m_per_deg
    avail["Distance_m"] = np.abs(dx) + np.abs(dy)

    within = avail[avail["Distance_m"] <= radius_m].sort_values("Distance_m")
    if max_results is not None:
        within = within.head(max_results)

    if "Hydrant" in within.columns:
        within = within.set_index("Hydrant")
    return within[["Distance_m", "Capacity_L_min"]]
