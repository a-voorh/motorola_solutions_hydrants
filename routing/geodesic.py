"""Geodesic (WGS84 great-circle) distance candidate search."""

from geopy.distance import geodesic


def nearby_hydrants_geodesic(fire_lat, fire_lon, radius_m, hydrants_df, max_results=None):
    """Return hydrants within ``radius_m`` (geodesic/WGS84), sorted by distance.

    Same output shape as ``routing.nearby_hydrants`` (Manhattan): a DataFrame
    indexed by Hydrant with columns Distance_m and Capacity_L_min.
    """
    avail = hydrants_df[hydrants_df["Available"] == True].copy()
    avail["Distance_m"] = [
        geodesic((fire_lat, fire_lon), (la, lo)).meters
        for la, lo in zip(avail["Latitude"], avail["Longitude"])
    ]
    within = avail[avail["Distance_m"] <= radius_m].sort_values("Distance_m")
    if max_results is not None:
        within = within.head(max_results)
    return within.set_index("Hydrant")[["Distance_m", "Capacity_L_min"]]
