"""
POC - Analyse GTFS : Indicateurs par arrêt pour un jour donné
Calcule pour chaque arrêt : nombre de passages, premier et dernier départ
"""

import pandas as pd


def calculer_indicateurs_arrets(feed, date_str: str):
    """
    Calcule les indicateurs pour chaque arrêt :
    - Nombre de lignes desservies
    - Nombre de passages
    - Heure du premier départ
    - Heure du dernier départ
    - Amplitude horaire
    - Temps d'attente moyen, min et max

    Returns:
        Panda Dataframe avec une ligne d'indicateurs par arrêt.
    """
    print(f"\nCalcul des indicateurs aux arrêts...")

    active_trips = feed.get_trips(date=date_str)
    active_service_ids = active_trips['service_id'].unique().tolist()

    if not active_service_ids:
        print("⚠ Aucun service actif pour cette date")
    

        # Filtrer les trips actifs ce jour-là
    trips_actifs = feed.trips[feed.trips["service_id"].isin(active_service_ids)]
    print(f"✓ {len(trips_actifs)} trips actifs")


    # Joindre avec stop_times
    stop_times_actifs = feed.stop_times.merge(
        trips_actifs[["trip_id", "service_id"]], on="trip_id"
    )

        # Rattacher chaque arrêt (quai) à sa station mère (parent_station) : les
        # différents quais d'une même station sont ainsi regroupés ensemble.
        # Les arrêts sans parent_station (stations autonomes) se regroupent sur
        # eux-mêmes.
    if "parent_station" in feed.stops.columns:
        stops_avec_station = feed.stops[["stop_id", "parent_station"]].copy()
        stops_avec_station["station_id"] = stops_avec_station["parent_station"].fillna(
            stops_avec_station["stop_id"]
        )
    else:
        stops_avec_station = feed.stops[["stop_id"]].copy()
        stops_avec_station["station_id"] = stops_avec_station["stop_id"]

    stop_times_actifs = stop_times_actifs.merge(
            stops_avec_station[["stop_id", "station_id"]], on="stop_id", how="left"
    )


        # Calculer les indicateurs par station
    indicateurs = (
        stop_times_actifs.groupby("station_id")
        .agg(
            nombre_passages=("trip_id", "count"),
            premier_depart=("departure_time", "min"),
            dernier_depart=("departure_time", "max"),
        )
        .reset_index()
        .rename(columns={"station_id": "stop_id"})
    )


        # Utilisation de gtfs_kit pour calculer les stats de headway (par quai),
        # puis agrégation au niveau de la station
    indic_gk = feed.compute_stop_stats([date_str])
    indic_gk = indic_gk.merge(
        stops_avec_station[["stop_id", "station_id"]], on="stop_id", how="left"
    )
    indic_gk_station = (
            indic_gk.groupby("station_id")
            .agg(
                temps_attente_moyen=("mean_headway", "mean"),
                temps_attente_max=("max_headway", "max"),
            )
            .reset_index()
            .rename(columns={"station_id": "stop_id"})
        )


    indicateurs = indicateurs.merge(indic_gk_station, on="stop_id", how="left")



        # Nombre de lignes distinctes desservant la station (évite les doublons
        # de comptage entre quais d'une même station)
    stop_times_avec_route = stop_times_actifs.merge(
        trips_actifs[["trip_id", "route_id"]], on="trip_id"
    )
    nb_lignes = (
        stop_times_avec_route.groupby("station_id")["route_id"]
        .nunique()
        .reset_index()
        .rename(columns={"station_id": "stop_id", "route_id": "nb_lignes"})
    )

    indicateurs = indicateurs.merge(nb_lignes, on="stop_id", how="left")


        # Joindre avec les informations de la station (nom, coordonnées)
    indicateurs = indicateurs.merge(
        feed.stops[["stop_id", "stop_name", "stop_lat", "stop_lon"]],
        on="stop_id",
        how="left",
    )

        # Calculer l'amplitude horaire
    indicateurs["amplitude_horaire"] = pd.to_timedelta(
        indicateurs["dernier_depart"]
    ) - pd.to_timedelta(indicateurs["premier_depart"])
        # nettoyer pour retirer le 0 days

    indicateurs["amplitude_horaire"] = indicateurs["amplitude_horaire"].apply(
            lambda x: str(x).replace("0 days ", " ").strip()
    )

        # Réorganiser les colonnes
    indicateurs = indicateurs[
        [
            "stop_id",
            "stop_name",
            "stop_lat",
            "stop_lon",
            "nb_lignes",
            "nombre_passages",
            "premier_depart",
            "dernier_depart",
            "amplitude_horaire",
            "temps_attente_moyen",
            "temps_attente_max",
        ]
    ]

        # Trier par nombre de passages décroissant
    indicateurs = indicateurs.sort_values("nombre_passages", ascending=False)

    print(f"✓ Indicateurs calculés pour {len(indicateurs)} arrêts")



    return indicateurs


def afficher_statistiques(df):
    """
    Affiche des statistiques résumées à partir d'un dataframe
    d'indicateurs par arrêt
    
    :param df: Dataframe d'indicateurs par arrêt obtenu par
    calcul à l'aide de la fonction calculer_indicateurs_arrets
    """
    print("\n" + "=" * 70)
    print("STATISTIQUES GLOBALES")
    print("=" * 70)
    print(f"Nombre total d'arrêts desservis : {len(df)}")
    print(f"Nombre total de passages / jour : {df['nombre_passages'].sum():,}")
    print(f"Moyenne de passages par arrêt : {df['nombre_passages'].mean():.1f}")
    print(f"Médiane de passages par arrêt : {df['nombre_passages'].median():.1f}")
    print(
        f"\nArrêt le plus fréquenté : {df.iloc[0]['stop_name']} ({df.iloc[0]['nombre_passages']} passages)"
    )
    print(f"Premier départ global : {df['premier_depart'].min()}")
    print(f"Dernier départ global : {df['dernier_depart'].max()}")

    print("\n" + "=" * 70)
    print("TOP 10 DES ARRÊTS LES PLUS FRÉQUENTÉS")
    print("=" * 70)
    print(df.head(10).to_string(index=False))
