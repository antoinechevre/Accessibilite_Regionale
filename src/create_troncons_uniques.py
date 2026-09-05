"""
Création optimisée des tronçons uniques GTFS
Tronçons par mode de transport, deux sens confondus, à la station parent
"""

import pandas as pd
import geopandas as gpd
import gtfs_kit as gk
from shapely.geometry import LineString, Point
from shapely.ops import substring


def _geometries_reelles_par_troncon(feed, paires):
    """
    Calcule, pour chaque tronçon (paire d'arrêts parents) présent dans
    `paires`, le tracé réel (LineString) découpé depuis shapes.txt entre
    les deux arrêts, plutôt que la ligne droite par défaut — notamment
    utile pour le bus, dont les arrêts consécutifs peuvent être très
    espacés (lignes interurbaines), rendant les lignes droites trompeuses.

    Un même tronçon peut être emprunté par plusieurs shape_id (routes ou
    variantes différentes) : on retient le plus fréquent comme
    représentatif, on prend les stop_id réels (pas les parents, pour une
    projection précise) d'un passage l'utilisant, puis on découpe le tracé
    du shape entre les deux arrêts (projection des points sur la ligne +
    shapely.ops.substring). Une découpe par tronçon plutôt que par trajet
    individuel : un même (tronçon, shape_id) donne quasi toujours la même
    découpe.

    Parameters
    ----------
    feed : gtfs_kit Feed object
    paires : DataFrame
        Une ligne par passage arrêt→arrêt suivant d'un trip, colonnes
        stop_pair, shape_id, stop_id, stop_id_suivant.

    Returns
    -------
    dict[tuple, shapely.geometry.LineString]
        stop_pair -> géométrie réelle. Un tronçon absent des clés n'a pas
        pu être résolu (shapes.txt absent, projection dégénérée...) —
        l'appelant se rabat alors sur une ligne droite entre les arrêts.
    """
    if feed.shapes is None or feed.shapes.empty:
        return {}

    # Shape dominant (le plus fréquent) par tronçon
    compte = paires.groupby(["stop_pair", "shape_id"]).size().reset_index(name="n")
    shape_dominant = (
        compte.sort_values("n", ascending=False)
        .drop_duplicates(subset=["stop_pair"])[["stop_pair", "shape_id"]]
        .rename(columns={"shape_id": "shape_dominant"})
    )

    # Un exemple de passage (stop_id réels) utilisant ce shape dominant, par tronçon
    exemples = paires.merge(shape_dominant, on="stop_pair")
    exemples = exemples[exemples["shape_id"] == exemples["shape_dominant"]]
    exemples = exemples.drop_duplicates(subset=["stop_pair"])

    shape_ids_utiles = exemples["shape_id"].dropna().unique()
    if len(shape_ids_utiles) == 0:
        return {}

    shapes_utiles = feed.shapes[feed.shapes["shape_id"].isin(shape_ids_utiles)]
    if shapes_utiles.empty:
        return {}

    lignes_par_shape = gk.geometrize_shapes(shapes_utiles).set_index("shape_id")["geometry"]
    coords_arrets = feed.stops.set_index("stop_id")[["stop_lat", "stop_lon"]]

    geometries = {}
    for row in exemples.itertuples(index=False):
        ligne = lignes_par_shape.get(row.shape_id)
        if ligne is None:
            continue
        try:
            lat1 = coords_arrets.at[row.stop_id, "stop_lat"]
            lon1 = coords_arrets.at[row.stop_id, "stop_lon"]
            lat2 = coords_arrets.at[row.stop_id_suivant, "stop_lat"]
            lon2 = coords_arrets.at[row.stop_id_suivant, "stop_lon"]
        except KeyError:
            continue
        if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2):
            continue
        d1 = ligne.project(Point(lon1, lat1))
        d2 = ligne.project(Point(lon2, lat2))
        if d1 == d2:
            continue
        segment = substring(ligne, min(d1, d2), max(d1, d2))
        if segment.is_empty or segment.length == 0:
            continue
        geometries[row.stop_pair] = segment

    return geometries


def creer_troncons_uniques(feed, route_type, agency_ids=None, prefixe=None):
    """
    Crée un GeoDataFrame des tronçons uniques pour un type de route donné.

    Tronçons uniques = paires d'arrêts parents, tous sens confondus, sans distinction de route.

    Parameters:
    -----------
    feed : gtfs_kit Feed object
        Feed GTFS chargé
    route_type : int
        Type de route GTFS (0=tram, 3=bus, etc.)
    agency_ids : list[str], optional
        Restreint en plus aux routes de ces agency_id (ex : distinguer RER,
        Transilien et TER, qui partagent tous route_type=2 dans le GTFS
        IDFM mais ont des agency_id différents — cf. gtfs_notebook_idf.ipynb).
        Par défaut, aucune restriction : toutes les routes du route_type.
    prefixe : str, optional
        Préfixe de troncon_unique_id à utiliser (ex: "RER") à la place de
        celui dérivé de route_type — utile quand plusieurs sous-catégories
        partagent le même route_type (cf. agency_ids ci-dessus), pour éviter
        des identifiants ambigus entre elles.

    Returns:
    --------
    GeoDataFrame avec les tronçons uniques
    """
    print(f"\nCréation des tronçons uniques pour route_type={route_type}" + (f", agency_ids={agency_ids}" if agency_ids else "") + "...")

    # 1. Préparer le mapping vers les parent_stations
    stops = feed.stops.copy()
    if "parent_station" not in stops.columns:
        stops["parent_station"] = stops["stop_id"]
    else:
        stops["parent_station"] = stops["parent_station"].fillna(stops["stop_id"])
        stops.loc[stops["parent_station"] == "", "parent_station"] = stops["stop_id"]

    # Mapping stop_id -> parent_station
    stop_to_parent = stops.set_index("stop_id")["parent_station"].to_dict()

    # Infos des parents (coords, noms)
    parent_info = (
        stops[stops["stop_id"] == stops["parent_station"]]
        .set_index("stop_id")[["stop_name", "stop_lat", "stop_lon"]]
        .to_dict("index")
    )

    # 2. Filtrer les trips du bon type de route (et, si fourni, des bonnes agences)
    routes_filtrees = feed.routes[feed.routes["route_type"] == route_type]
    if agency_ids is not None:
        routes_filtrees = routes_filtrees[routes_filtrees["agency_id"].isin(agency_ids)]
    routes_filtrees = routes_filtrees["route_id"]
    trips_filtres = feed.trips[feed.trips["route_id"].isin(routes_filtrees)]

    # 3. Joindre stop_times avec les trips filtrés (+ shape_id, pour la
    # géométrie réelle du tracé — cf. étape 6bis ci-dessous). shape_id est
    # une colonne optionnelle de trips.txt (absente, pas juste vide, quand
    # le GTFS n'a pas de shapes.txt) : ajoutée à None si manquante plutôt
    # que de planter au merge.
    trips_pour_merge = trips_filtres[["trip_id"]].copy()
    trips_pour_merge["shape_id"] = (
        trips_filtres["shape_id"] if "shape_id" in trips_filtres.columns else None
    )
    stop_times = feed.stop_times.merge(trips_pour_merge, on="trip_id", how="inner")

    print(f"  → {len(trips_filtres)} trips, {len(stop_times)} stop_times")

    # 4. Mapper vers parent_stations
    stop_times["stop_parent"] = stop_times["stop_id"].map(stop_to_parent)

    # 5. Trier par trip et séquence
    stop_times = stop_times.sort_values(["trip_id", "stop_sequence"])

    # 6. Créer les paires d'arrêts consécutifs
    print("  → Création des paires d'arrêts consécutifs...")

    # Décalage pour obtenir l'arrêt suivant (stop_id réel en plus du
    # parent : nécessaire pour projeter précisément sur le tracé du shape,
    # cf. étape 6bis)
    stop_times["stop_parent_suivant"] = stop_times.groupby("trip_id")[
        "stop_parent"
    ].shift(-1)
    stop_times["stop_id_suivant"] = stop_times.groupby("trip_id")["stop_id"].shift(-1)

    # Supprimer les derniers arrêts de chaque trip (pas de suivant)
    paires = stop_times.dropna(subset=["stop_parent_suivant"]).copy()

    # 7. Créer une clé unique pour chaque paire (tous sens confondus)
    print("  → Normalisation des paires (tous sens confondus)...")

    paires["stop_pair"] = paires.apply(
        lambda row: tuple(sorted([row["stop_parent"], row["stop_parent_suivant"]])),
        axis=1,
    )

    # 8. Dédupliquer pour obtenir les tronçons uniques
    troncons_uniques = paires[["stop_pair"]].drop_duplicates().reset_index(drop=True)

    print(f"  → {len(troncons_uniques)} tronçons uniques identifiés")

    # 8bis. Géométrie réelle (tracé shapes.txt) plutôt que ligne droite entre
    # arrêts : un même tronçon (paire d'arrêts parents) peut être emprunté
    # par plusieurs shape_id (routes/variantes différentes) — on retient le
    # shape le plus fréquent pour ce tronçon comme représentatif, on prend
    # les stop_id réels (pas les parents, pour une projection précise) d'un
    # passage utilisant ce shape, puis on découpe le tracé du shape entre
    # les deux arrêts (shapely.ops.substring, après projection des points
    # sur la ligne). Coûteux à faire par trajet individuel ; comme un même
    # (tronçon, shape_id) donne quasi toujours la même découpe, on ne le
    # fait qu'une fois par tronçon plutôt que par trajet.
    geometrie_par_troncon = _geometries_reelles_par_troncon(feed, paires)

    colonnes_finales = [
        "troncon_unique_id",
        "stop_depart_parent_id",
        "stop_arrivee_parent_id",
        "stop_depart_name",
        "stop_arrivee_name",
        "lat_depart_parent",
        "lon_depart_parent",
        "lat_arrivee_parent",
        "lon_arrivee_parent",
        "geometry",
    ]

    if troncons_uniques.empty:
        # Aucun trip pour ce route_type (ex : réseau sans trolleybus) : sur un
        # DataFrame à 0 ligne, .apply(axis=1) ne produit pas de colonne
        # exploitable, d'où le GeoDataFrame vide construit directement ici.
        gdf = gpd.GeoDataFrame(columns=colonnes_finales, geometry="geometry", crs="EPSG:4326")
        print(f"✓ {len(gdf)} tronçons uniques créés")
        return gdf

    # 9. Enrichir avec les informations des arrêts
    print("  → Enrichissement avec coordonnées et noms...")

    def enrichir_troncon(stop_pair):
        """Enrichit un tronçon avec les infos des deux arrêts"""
        stop1, stop2 = stop_pair

        # Infos du parent 1
        info1 = parent_info.get(stop1, {})
        # Infos du parent 2
        info2 = parent_info.get(stop2, {})

        return {
            "stop_depart_parent_id": stop1,
            "stop_arrivee_parent_id": stop2,
            "stop_depart_name": info1.get("stop_name", ""),
            "stop_arrivee_name": info2.get("stop_name", ""),
            "lat_depart_parent": info1.get("stop_lat", None),
            "lon_depart_parent": info1.get("stop_lon", None),
            "lat_arrivee_parent": info2.get("stop_lat", None),
            "lon_arrivee_parent": info2.get("stop_lon", None),
        }

    # Appliquer l'enrichissement
    infos_enrichies = troncons_uniques["stop_pair"].apply(enrichir_troncon)
    df_enrichi = pd.DataFrame(infos_enrichies.tolist())

    # Combiner avec l'index original
    troncons_uniques = pd.concat([troncons_uniques, df_enrichi], axis=1)

    # 10. Générer les identifiants et géométries
    print("  → Génération des identifiants et géométries...")

    # Identifiants uniques
    route_type_prefix = prefixe or (
        "METRP" if route_type == 1
        else "TRAM" if route_type == 0
        else "BUS" if route_type == 3
        else "TRAIN" if route_type == 2
        else f"RT{route_type}"
    )
    troncons_uniques["troncon_unique_id"] = [
        f"TU_{route_type_prefix}_{i:06d}" for i in range(len(troncons_uniques))
    ]

    # Géométries : tracé réel (shapes.txt, cf. geometrie_par_troncon
    # ci-dessus) quand disponible, sinon ligne droite entre les deux
    # arrêts en repli (shapes.txt absent, projection dégénérée...).
    def _geometrie_troncon(row):
        reelle = geometrie_par_troncon.get(row["stop_pair"])
        if reelle is not None:
            return reelle
        if all(
            pd.notna(
                [
                    row["lon_depart_parent"],
                    row["lat_depart_parent"],
                    row["lon_arrivee_parent"],
                    row["lat_arrivee_parent"],
                ]
            )
        ):
            return LineString(
                [
                    (row["lon_depart_parent"], row["lat_depart_parent"]),
                    (row["lon_arrivee_parent"], row["lat_arrivee_parent"]),
                ]
            )
        return None

    troncons_uniques["geometry"] = troncons_uniques.apply(_geometrie_troncon, axis=1)
    n_reelles = sum(1 for v in geometrie_par_troncon.values() if v is not None)
    print(f"  → {n_reelles}/{len(troncons_uniques)} tronçons avec tracé réel (shapes.txt), le reste en ligne droite")

    # 11. Créer le GeoDataFrame
    gdf = gpd.GeoDataFrame(
        troncons_uniques[colonnes_finales], geometry="geometry", crs="EPSG:4326"
    )

    # Supprimer la colonne temporaire stop_pair
    if "stop_pair" in gdf.columns:
        gdf = gdf.drop(columns=["stop_pair"])

    print(f"✓ {len(gdf)} tronçons uniques créés")

    return gdf


# =============================================================================
# EXEMPLE D'UTILISATION
# =============================================================================

if __name__ == "__main__":
    from utils import charger_gtfs, exporter_gdf_to_csv

    # Charger le feed GTFS
    feed = charger_gtfs()

    # Créer les tronçons uniques pour bus et tram
    print("=" * 70)
    print("CRÉATION DES TRONÇONS UNIQUES")
    print("=" * 70)

    # Bus (route_type = 3)
    troncons_bus = creer_troncons_uniques(feed, route_type=3)
    exporter_gdf_to_csv(troncons_bus, "output/troncons_uniques_bus.csv")
    # exporter_geojson(troncons_bus, 'output/troncons_uniques_bus.geojson')

    # Tram (route_type = 0)
    troncons_tram = creer_troncons_uniques(feed, route_type=0)
    exporter_gdf_to_csv(troncons_tram, "output/troncons_uniques_tram.csv")
    # exporter_geojson(troncons_tram, 'output/troncons_uniques_tram2.geojson')

    # Metro (route_type = 1)
    troncons_metro = creer_troncons_uniques(feed, route_type=1)
    exporter_gdf_to_csv(troncons_metro, "output/troncons_uniques_metro.csv")
    # exporter_geojson(troncons_metro, 'output/troncons_uniques_metro2.geojson')

    # Trolley (route_type = 11)
    troncons_trolley = creer_troncons_uniques(feed, route_type=11)
    exporter_gdf_to_csv(troncons_trolley, "output/troncons_uniques_trolley.csv")
    # exporter_geojson(troncons_trolley, 'output/troncons_uniques_trolley2.geojson')

    # Ferry (route_type = 4)
    troncons_ferry = creer_troncons_uniques(feed, route_type=4)
    exporter_gdf_to_csv(troncons_ferry, "output/troncons_uniques_ferry.csv")
    # exporter_geojson(troncons_trolley, 'output/troncons_uniques_ferry2.geojson')

    # Train (route_type = 2 : RER, Transilien, TER...)
    troncons_train = creer_troncons_uniques(feed, route_type=2)
    exporter_gdf_to_csv(troncons_train, "output/troncons_uniques_train.csv")
    # exporter_geojson(troncons_train, 'output/troncons_uniques_train2.geojson')


    print("\n" + "=" * 70)
    print("✓ TRAITEMENT TERMINÉ")
    print("=" * 70)
