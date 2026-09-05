"""
Fusion de plusieurs GTFS distincts en un seul GTFS
cf. https://github.com/google/transitfeed/wiki/Merge

Contrairement à l'outil Google (qui déduplique les entités quasi-identiques
entre deux feeds qui se recouvrent), cette fusion est une concaténation simple :
chaque feed garde toutes ses entités, avec ses identifiants préfixés par feed
pour éviter toute collision. Adapté au cas de réseaux distincts (pas de
recouvrement géographique), pas à la fusion de deux feeds décrivant le même
réseau.
"""

import os
from pathlib import Path

import gtfs_kit as gk
import pandas as pd

# Tables GTFS gérées par gtfs_kit.Feed
TABLES_GTFS = [
    "agency",
    "stops",
    "routes",
    "trips",
    "stop_times",
    "calendar",
    "calendar_dates",
    "fare_attributes",
    "fare_rules",
    "shapes",
    "frequencies",
    "transfers",
    "feed_info",
    "attributions",
]


# Chemin vers le zip GTFS
# Basé sur l'emplacement réel de ce fichier (gtfs_analysis/src/merge_gtfs.py),
# pas sur le répertoire de travail courant : indépendant de l'endroit d'où
# le script est lancé.
BASE_DIR = Path(__file__).resolve().parent.parent


# Choisir le jeu de données GTFS en zip, site de référence 
GTFS_ZIP_PATH_NYC_gtfs_busco = os.path.join(BASE_DIR,"data", "NYC_gtfs_busco.zip")
GTFS_ZIP_PATH_NYC_gtfs_b = os.path.join(BASE_DIR,"data", "NYC_gtfs_b.zip")
GTFS_ZIP_PATH_NYC_gtfs_subway = os.path.join(BASE_DIR,"data", "NYC_gtfs_subway.zip")
GTFS_ZIP_PATH_NYC_gtfs_bx = os.path.join(BASE_DIR,"data", "NYC_gtfs_bx.zip")
GTFS_ZIP_PATH_NYC_gtfs_m = os.path.join(BASE_DIR,"data", "NYC_gtfs_m.zip")
GTFS_ZIP_PATH_NYC_gtfs_si = os.path.join(BASE_DIR,"data", "NYC_gtfs_si.zip")
OUTPUT_HTML_PATH_NYC_merge = os.path.join(BASE_DIR, "data","NYC_gtfs_merge.zip")



def fusionner_gtfs(chemins_zip, chemin_sortie, dist_units="km"):
    """
    Fusionne plusieurs GTFS (fichiers zip) en un seul GTFS.

    Chaque feed est chargé, puis ses identifiants (stop_id, route_id,
    trip_id, service_id, shape_id, etc.) sont préfixés par son rang dans
    `chemins_zip` pour garantir l'absence de collision entre feeds, avant
    concaténation table par table.

    Parameters:
    -----------
    chemins_zip : list[str]
        Chemins des fichiers GTFS (zip) à fusionner, au moins 2.
    chemin_sortie : str
        Chemin du fichier GTFS (zip) fusionné à écrire.
    dist_units : str
        Unité de distance à utiliser pour le feed fusionné (défaut: 'km').
        Les feeds dans une autre unité sont convertis avant fusion.

    Returns:
    --------
    gtfs_kit Feed object
        Le feed fusionné.
    """
    if len(chemins_zip) < 2:
        raise ValueError("Il faut au moins 2 GTFS à fusionner")

    print("=" * 70)
    print("FUSION DES GTFS")
    print("=" * 70)

    feeds_prefixes = []
    for i, chemin in enumerate(chemins_zip):
        print(f"\nChargement de {os.path.basename(chemin)}...")
        feed = gk.read_feed(chemin, dist_units=dist_units)

        prefixe = f"{i}_"
        print(f"  → préfixage des identifiants avec '{prefixe}'")
        feeds_prefixes.append(gk.prefix_feed_ids(feed, prefixe))

    tables_fusionnees = {}
    for table in TABLES_GTFS:
        dfs = [
            getattr(feed, table)
            for feed in feeds_prefixes
            if getattr(feed, table) is not None
        ]
        if dfs:
            tables_fusionnees[table] = pd.concat(dfs, ignore_index=True, sort=False)

    feed_fusionne = gk.Feed(dist_units=dist_units, **tables_fusionnees)

    feed_fusionne.to_file(chemin_sortie)
    print(f"\n✓ GTFS fusionné enregistré dans : {chemin_sortie}")

    print("\n" + "=" * 70)
    print("✓ FUSION TERMINÉE")
    print("=" * 70)

    return feed_fusionne


if __name__ == "__main__":
    fusionner_gtfs(
        [
            GTFS_ZIP_PATH_NYC_gtfs_subway,
            GTFS_ZIP_PATH_NYC_gtfs_m,           
        ],
        OUTPUT_HTML_PATH_NYC_merge,
    )
