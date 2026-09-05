import gtfs_kit as gk
import pandas as pd
import folium


def trace_lignes_gtfs(zip_path, date_analyse):

    """
    Trace les lignes GTFS pour une date donnée.
    """
    feed = gk.read_feed(zip_path, dist_units="km")

    date_analyse = date_analyse   # format YYYYMMDD
    active_trips = feed.get_trips(date=date_analyse)

    # Filtrer le DataFrame shapes sur les shape_id actifs
    shapes_actifs = feed.shapes[feed.shapes['shape_id'].isin(active_trips['shape_id'].unique())]

    # Convertir en GeoDataFrame
    geo_shapes = gk.geometrize_shapes(shapes_actifs)

    # Fusionner avec les routes pour avoir noms/couleurs
    trips_routes = active_trips.merge(feed.routes, on='route_id')
    geo_shapes = geo_shapes.merge(
        trips_routes[['shape_id', 'route_short_name', 'route_color']].drop_duplicates(),
        on='shape_id'
    )

    m = folium.Map(location=[46.1603, -1.1511], zoom_start=13, tiles="CartoDB positron")

    for _, row in geo_shapes.iterrows():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        color = f"#{row['route_color']}" if pd.notna(row.get('route_color')) else "blue"
        folium.PolyLine(coords, color=color, weight=4, tooltip=row['route_short_name']).add_to(m)

    m.save("carte_lignes_gtfs.html")