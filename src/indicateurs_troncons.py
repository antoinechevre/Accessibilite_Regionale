"""
Calcul de la fréquentation et vitesse moyenne par tronçon unique
Basé sur stop_parent_id, tous sens confondus
"""

import os

import pandas as pd
import numpy as np
import geopandas as gpd

from src.utils import charger_ou_calculer_gdf


def calculer_distance_haversine(lat1, lon1, lat2, lon2):
    """
    Calcule la distance entre deux points GPS en kilomètres
    Utilise la formule de Haversine
    """
    R = 6371  # Rayon de la Terre en km

    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))

    return R * c


def convertir_temps_en_secondes(temps_str):
    """
    Convertit un temps GTFS (HH:MM:SS) en secondes
    Gère les heures > 24 (ex: 25:30:00 pour 01:30 le lendemain)
    """
    if pd.isna(temps_str):
        return None

    parts = str(temps_str).split(":")
    heures = int(parts[0])
    minutes = int(parts[1])
    secondes = int(parts[2])

    return heures * 3600 + minutes * 60 + secondes


def preparer_mapping_parent_stops(feed):
    """
    Crée un mapping entre stop_id et parent_station
    Si parent_station n'existe pas, utilise stop_id comme parent
    """
    stops = feed.stops.copy()

    # Si parent_station n'existe pas ou est vide, utiliser stop_id
    if "parent_station" not in stops.columns:
        stops["parent_station"] = stops["stop_id"]
    else:
        stops["parent_station"] = stops["parent_station"].fillna(stops["stop_id"])
        # Remplacer les chaînes vides par stop_id
        stops.loc[stops["parent_station"] == "", "parent_station"] = stops["stop_id"]

    return (
        stops[["stop_id", "parent_station"]]
        .set_index("stop_id")["parent_station"]
        .to_dict()
    )


def calculer_frequentation_troncons(feed, df_troncons_uniques, service_ids, route_type, agency_ids=None):
    """
    Calcule la fréquentation et la vitesse moyenne pour chaque tronçon unique

    Parameters:
    -----------
    feed : gtfs_kit Feed object
        Le feed GTFS chargé
    df_troncons_uniques : DataFrame
        Table des tronçons uniques avec les colonnes :
        stop_depart_parent_id, stop_arrivee_parent_id, troncon_unique_id, etc.
    service_ids : list
        Liste des service_id actifs pour la date analysée
    route_type: int
        Le type de route (0=tram, 3=bus, etc.)
    agency_ids : list[str], optional
        Restreint en plus aux routes de ces agency_id (ex : distinguer RER,
        Transilien et TER, qui partagent tous route_type=2 dans le GTFS
        IDFM mais ont des agency_id différents — cf. gtfs_notebook_idf.ipynb).

    Returns:
    --------
    DataFrame avec fréquentation et vitesse moyenne par tronçon
    """
    print("\nCalcul de la fréquentation par tronçon unique...")

    colonnes_stats = [
        "nombre_passages",
        "duree_moyenne_secondes",
        "duree_min_secondes",
        "duree_max_secondes",
        "distance_km",
        "vitesse_moyenne_kmh",
        "vitesse_min_kmh",
        "vitesse_max_kmh",
    ]

    if df_troncons_uniques.empty:
        # Aucun tronçon de référence pour ce mode (ex : réseau sans
        # trolleybus) : .apply(axis=1) sur un DataFrame à 0 ligne ne produit
        # pas de colonne exploitable plus bas, d'où ce retour anticipé.
        print("⚠ Aucun tronçon de référence pour ce mode")
        df_resultat = df_troncons_uniques.copy()
        for colonne in colonnes_stats:
            df_resultat[colonne] = pd.Series(dtype="float64")
        df_resultat["nombre_passages"] = df_resultat["nombre_passages"].astype(int)
        return df_resultat

    # Créer le mapping stop_id -> parent_station
    mapping_parent = preparer_mapping_parent_stops(feed)

    # Filtrer les trips actifs
    trips_actifs = feed.trips[feed.trips["service_id"].isin(service_ids)].copy()

    # Restriction au bon route_type (et, si fourni, aux bonnes agences)
    routes_filtrees = feed.routes[feed.routes["route_type"] == route_type]
    if agency_ids is not None:
        routes_filtrees = routes_filtrees[routes_filtrees["agency_id"].isin(agency_ids)]
    trips_actifs = trips_actifs[
        trips_actifs["route_id"].isin(routes_filtrees["route_id"])
    ]

    print(f"✓ {len(trips_actifs)} trips actifs")

    # Enrichir stop_times avec les informations nécessaires
    stop_times = feed.stop_times.merge(
        trips_actifs[["trip_id", "route_id"]], on="trip_id"
    )

    # Ajouter les parent_station pour chaque stop
    stop_times["stop_parent_id"] = stop_times["stop_id"].map(mapping_parent)

    # Trier par trip et séquence
    stop_times = stop_times.sort_values(["trip_id", "stop_sequence"])

    print(f"✓ {len(stop_times)} stop_times à analyser")

    # Calculer les passages par paire de stops consécutifs
    passages_list = []

    for trip_id, groupe in stop_times.groupby("trip_id"):
        groupe = groupe.sort_values("stop_sequence").reset_index(drop=True)

        for i in range(len(groupe) - 1):
            depart = groupe.iloc[i]
            arrivee = groupe.iloc[i + 1]

            # Calculer le temps de parcours
            temps_depart = convertir_temps_en_secondes(depart["departure_time"])
            temps_arrivee = convertir_temps_en_secondes(arrivee["arrival_time"])

            if temps_depart is not None and temps_arrivee is not None:
                duree_secondes = temps_arrivee - temps_depart

                if duree_secondes > 0:
                    # Identifier le tronçon (tous sens confondus)
                    parent_depart = depart["stop_parent_id"]
                    parent_arrivee = arrivee["stop_parent_id"]

                    # Créer une clé normalisée (ordre alphabétique pour regrouper les deux sens)
                    stops_pair = tuple(sorted([parent_depart, parent_arrivee]))

                    passages_list.append(
                        {
                            "stop_pair": stops_pair,
                            "stop_depart_parent": parent_depart,
                            "stop_arrivee_parent": parent_arrivee,
                            "duree_secondes": duree_secondes,
                            "trip_id": trip_id,
                        }
                    )

    print(f"✓ {len(passages_list)} passages détectés")

    if not passages_list:
        # Des tronçons de référence existent pour ce mode, mais aucun passage
        # n'a été détecté à cette date (ex : pas de service ce jour-là) : on
        # renvoie les tronçons avec nombre_passages=0 plutôt que None, pour
        # rester compatible avec gpd.GeoDataFrame(..., geometry="geometry")
        # plus bas dans le pipeline.
        print("⚠ Aucun passage détecté")
        stats_par_paire = pd.DataFrame(
            columns=[
                "stop_pair",
                "nombre_passages",
                "duree_moyenne_secondes",
                "duree_min_secondes",
                "duree_max_secondes",
            ]
        )
    else:
        df_passages = pd.DataFrame(passages_list)

        # Agréger par paire de stops (tous sens confondus)
        # On compte le nombre de passages et calcule la durée moyenne
        stats_par_paire = (
            df_passages.groupby("stop_pair")
            .agg(
                nombre_passages=("trip_id", "count"),
                duree_moyenne_secondes=("duree_secondes", "mean"),
                duree_min_secondes=("duree_secondes", "min"),
                duree_max_secondes=("duree_secondes", "max"),
            )
            .reset_index()
        )

    print(f"✓ Statistiques calculées pour {len(stats_par_paire)} paires de stops")

    # Préparer le matching avec df_troncons_uniques
    # Créer la même clé normalisée dans df_troncons_uniques
    df_troncons_uniques["stop_pair"] = df_troncons_uniques.apply(
        lambda row: tuple(
            sorted([row["stop_depart_parent_id"], row["stop_arrivee_parent_id"]])
        ),
        axis=1,
    )

    # Joindre avec les statistiques
    df_resultat = df_troncons_uniques.merge(stats_par_paire, on="stop_pair", how="left")

    # Supprimer la colonne temporaire
    df_resultat = df_resultat.drop(columns=["stop_pair"])

    # Calculer la distance si pas déjà présente
    if "distance_km" not in df_resultat.columns:
        df_resultat["distance_km"] = df_resultat.apply(
            lambda row: calculer_distance_haversine(
                row["lat_depart_parent"],
                row["lon_depart_parent"],
                row["lat_arrivee_parent"],
                row["lon_arrivee_parent"],
            ),
            axis=1,
        )

    # Calculer la vitesse moyenne en km/h
    df_resultat["vitesse_moyenne_kmh"] = df_resultat["distance_km"] / (
        df_resultat["duree_moyenne_secondes"] / 3600
    )

    # Calculer aussi vitesse min et max
    df_resultat["vitesse_min_kmh"] = df_resultat["distance_km"] / (
        df_resultat["duree_max_secondes"] / 3600
    )

    df_resultat["vitesse_max_kmh"] = df_resultat["distance_km"] / (
        df_resultat["duree_min_secondes"] / 3600
    )

    # Remplacer les NaN (tronçons sans passage) par 0
    df_resultat["nombre_passages"] = (
        df_resultat["nombre_passages"].fillna(0).astype(int)
    )

    # Trier par nombre de passages décroissant
    df_resultat = df_resultat.sort_values(
        "nombre_passages", ascending=False
    ).reset_index(drop=True)

    print(f"✓ Fréquentation calculée pour {len(df_resultat)} tronçons uniques")
    print(f"✓ Tronçons avec passages : {(df_resultat['nombre_passages'] > 0).sum()}")

    return df_resultat


def compute_indicateurs_troncons(
    feed,
    active_service_ids: list[str],
    reference_troncons_uniques_bus: pd.DataFrame,
    reference_troncons_uniques_tram: pd.DataFrame,
    reference_troncons_uniques_metro: pd.DataFrame,
    reference_troncons_uniques_trolley: pd.DataFrame,
    reference_troncons_uniques_ferry: pd.DataFrame,
    reference_troncons_uniques_train: pd.DataFrame,
    nom_reseau_str: str = None,
):
    """
    Réalise le calcul des indicateurs par tronçon pour une date d'analyse donnée
    Args:
        feed: gtfs_kit Feed object
            Le feed GTFS chargé
        active_service_ids: list[str]: Liste des services actifs à la date choisie
        reference_troncons_uniques_bus (pd.DataFrame):
            Table des tronçons uniques bus
        reference_troncons_uniques_tram (pd.DataFrame):
            Table des tronçons uniques tram
        reference_troncons_uniques_metro (pd.DataFrame):
            Table des tronçons uniques metro
        reference_troncons_uniques_trolley (pd.DataFrame):
            Table des tronçons uniques trolley
        reference_troncons_uniques_ferry (pd.DataFrame):
            Table des tronçons uniques ferry
        reference_troncons_uniques_train (pd.DataFrame):
            Table des tronçons uniques train (route_type=2 : RER, Transilien, TER...)
        nom_reseau_str : str, optional
            Si fourni, active un cache disque par mode sous
            data/memory_troncons/<nom_reseau_str>/indicateurs_<mode>.csv
            (cf. charger_ou_calculer_gdf dans utils.py) — sûr d'une exécution
            à l'autre car date_JOB est déterministe pour un GTFS donné.
            Si omis, comportement inchangé : toujours recalculé.

    Returns:
        Tuple de GeoDataFrame : (indicateurs_bus, indicateurs_tram, indicateurs_metro, indicateurs_trolley, indicateurs_ferry, indicateurs_train)
    """

    def _calculer(nom_mode, reference_troncons_uniques, route_type):
        fonction_calcul = lambda: calculer_frequentation_troncons(
            feed, reference_troncons_uniques, active_service_ids, route_type=route_type
        )
        if nom_reseau_str is None:
            return fonction_calcul()
        chemin_cache = os.path.join(
            "data", "memory_troncons", nom_reseau_str, f"indicateurs_{nom_mode}.csv"
        )
        return charger_ou_calculer_gdf(chemin_cache, fonction_calcul)

    # Calculer la fréquentation (ou la charger depuis le cache si nom_reseau_str fourni)
    indicateurs_bus = _calculer("bus", reference_troncons_uniques_bus, 3)
    indicateurs_tram = _calculer("tram", reference_troncons_uniques_tram, 0)
    indicateurs_metro = _calculer("metro", reference_troncons_uniques_metro, 1)
    indicateurs_trolley = _calculer("trolley", reference_troncons_uniques_trolley, 11)
    indicateurs_ferry = _calculer("ferry", reference_troncons_uniques_ferry, 4)
    indicateurs_train = _calculer("train", reference_troncons_uniques_train, 2)

    # Convertir en GeoDataFrame
    indicateurs_bus_gdf = gpd.GeoDataFrame(
        indicateurs_bus, geometry="geometry", crs="EPSG:4326"
    )

    indicateurs_tram_gdf = gpd.GeoDataFrame(
        indicateurs_tram, geometry="geometry", crs="EPSG:4326"
    )

    indicateurs_metro_gdf = gpd.GeoDataFrame(
        indicateurs_metro, geometry="geometry", crs="EPSG:4326"
    )
    indicateurs_trolley_gdf = gpd.GeoDataFrame(
        indicateurs_trolley, geometry="geometry", crs="EPSG:4326"
    )
    indicateurs_ferry_gdf = gpd.GeoDataFrame(
        indicateurs_ferry, geometry="geometry", crs="EPSG:4326"
    )
    indicateurs_train_gdf = gpd.GeoDataFrame(
        indicateurs_train, geometry="geometry", crs="EPSG:4326"
    )

    return indicateurs_bus_gdf, indicateurs_tram_gdf, indicateurs_metro_gdf, indicateurs_trolley_gdf, indicateurs_ferry_gdf, indicateurs_train_gdf
 
 
"""
 
# =============================================================================
# EXEMPLE D'UTILISATION
# =============================================================================

if __name__ == "__main__":
    from src.create_troncons_uniques import creer_troncons_uniques
    from src.utils import exporter_geojson, exporter_df_to_csv, exporter_gdf_to_csv

    date_calcul = "20251123"

    # Charger le GTFS
    print("Chargement du GTFS...")
    feed = charger_gtfs()
    active_service_ids = obtenir_service_ids_pour_date(feed, date_calcul)

    # Charger la table des tronçons uniques
    df_troncons_uniques_bus = creer_troncons_uniques(feed, route_type=3)
    df_troncons_uniques_tram = creer_troncons_uniques(feed, route_type=0)

    # Calcul des indicateurs
    indicateurs_bus, indicateurs_tram = compute_indicateurs_troncons(
        feed, active_service_ids, df_troncons_uniques_bus, df_troncons_uniques_tram
    )

    # Export en csv
    exporter_gdf_to_csv(
        indicateurs_bus, f"output/indicateurs_troncons_bus_{date_calcul}.csv"
    )
    exporter_gdf_to_csv(
        indicateurs_tram, f"output/indicateurs_troncons_tram_{date_calcul}.csv"
    )

    # Export en geojson
    exporter_geojson(
        indicateurs_bus, f"output/indicateurs_troncons_bus_{date_calcul}.geojson"
    )
    exporter_geojson(
        indicateurs_tram, f"output/indicateurs_troncons_tram_{date_calcul}.geojson"
    )
"""
