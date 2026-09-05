"""
Construction des données partagées par les pages Streamlit d'accessibilité
RÉGIONALE : découpage administratif régional, carroyage population INSEE 1km
et BPE filtrée/pondérée, réseau de transport r5py et matrice des temps de
trajet — reprend le notebook notebook_region_accessibilite.ipynb, avec mise
en cache disque par région (nom_region_str) pour ne pas tout relancer à
chaque rerun Streamlit.

Distinct de src/pipeline_donnees.py (échelle agglo, par réseau de transport
nom_reseau_str) : à l'échelle région, ni le filtre "plus grande composante
connexe" (build_decoupage_region_via_api récupère TOUTES les communes de la
région, GTFS ou pas), ni le garde-fou "max 4 agences" (un GTFS régional
agrège légitimement des dizaines d'agences), ni le carroyage 200m (1km
directement, une région étant systématiquement plus grosse qu'une agglo).
"""

import os

from src.build_data_agglo import (
    build_grid_agglo_1km,
    build_decoupage_region_via_api,
    decoupage_agglo_geojson,
    nom_region_via_gtfs,
    telecharger_osm_pbf_geofabrik,
)
from src.BPE_traitement import filtre_BPE, filtre_BPE_actifs, land_use_data_domaine
from src.hf_cache import HF_DATA_REPO_ID_REGION, recuperer_depuis_hf
from src.ponderation_bpe import GAMMES_POIDS_PAR_DOMAINE, SEUILS_DOMAINE
from src.utils import charger_gtfs, exporter_df_to_csv, preparer_gtfs_pour_r5py
from src.utilitaires_matrix import (
    calculer_index_benchmark_par_lots,
    calculer_ttm_par_lots,
    deciles_niveau_vie,
)

BASE_DIR = os.getcwd()
DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_CSV_REGION_DIR = os.path.join(DATA_DIR, "memory_csv_region")
MEMORY_GPKG_DIR = os.path.join(DATA_DIR, "memory_gpkg")
MEMORY_PBF_DIR = os.path.join(DATA_DIR, "memory_pbf")
MEMORY_TTM_DIR = os.path.join(DATA_DIR, "memory_ttm")
BPE_PATH = os.path.join(DATA_DIR, "INSEE", "BPE25.parquet")
BPE_XLS_PATH = os.path.join(DATA_DIR, "INSEE", "BPE_gammes_equipements_2025.xlsx")
BPE_URL = "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.parquet"

DOMAINES_BPE = {
    "O": "Tout équipements pondérés",
    "A": "Services pour les particuliers",
    "B": "Commerces",
    "C": "Enseignement",
    "D": "Santé et action sociale",
    "E": "Transports et déplacements",
    "F": "Sports, loisirs et culture",
    "G": "Tourisme",
}

# Cutoffs par défaut de l'analyse d'accessibilité régionale (accès à 60 et
# 90 min) — distincts des cutoffs (30, 45, 60) par défaut de
# calculer_index_benchmark, pensés pour une échelle agglo où les trajets
# sont plus courts.
CUTOFFS_MINUTES_DEFAUT = (60, 90)
MAX_TIME_MINUTES_DEFAUT = max(CUTOFFS_MINUTES_DEFAUT)


def chemins_region(nom_region_str):
    """Chemins de cache disque (par région) utilisés par le pipeline."""
    for dossier in (MEMORY_CSV_REGION_DIR, MEMORY_GPKG_DIR, MEMORY_PBF_DIR, MEMORY_TTM_DIR):
        os.makedirs(dossier, exist_ok=True)
    return {
        "decoupage_csv": os.path.join(MEMORY_CSV_REGION_DIR, f"decoupage_region_{nom_region_str}.csv"),
        "decoupage_geojson": os.path.join(MEMORY_CSV_REGION_DIR, f"decoupage_region_{nom_region_str}.geojson"),
        "osm_pbf": os.path.join(MEMORY_PBF_DIR, f"region_osm_pbf_{nom_region_str}.osm.pbf"),
        "gpkg": os.path.join(MEMORY_GPKG_DIR, f"population_grid_region_{nom_region_str}.gpkg"),
        "ttm": os.path.join(MEMORY_TTM_DIR, f"ttm_region_{nom_region_str}.parquet"),
    }


def assurer_bpe_local():
    """Récupère le fichier détail BPE25 (~160 Mo) si absent en local : d'abord
    depuis le cache HF (plus rapide, déjà téléversé), sinon depuis insee.fr."""
    import requests

    if os.path.exists(BPE_PATH):
        return
    if recuperer_depuis_hf("BPE25.parquet", BPE_PATH):
        return
    os.makedirs(os.path.dirname(BPE_PATH), exist_ok=True)
    with requests.get(BPE_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(BPE_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def assurer_bpe_xls_local():
    """Récupère depuis le cache HF le fichier des gammes d'équipements
    BPE_gammes_equipements_2025.xlsx si absent en local."""
    if os.path.exists(BPE_XLS_PATH):
        return
    if not recuperer_depuis_hf("BPE_gammes_equipements_2025.xlsx", BPE_XLS_PATH):
        raise FileNotFoundError(f"{BPE_XLS_PATH} introuvable en local et absent du cache Hugging Face.")


def ponderer_bpe(BPE_region):
    """Ajoute la colonne poids_gamme à BPE_region (cf. notebook "analyse BPE 1.1")."""
    import pandas as pd

    assurer_bpe_xls_local()
    gamme_typequ = pd.read_excel(BPE_XLS_PATH, sheet_name="Gammes 2025 1 ligne 1 Typequ", header=4)[
        ["TYPEQU", "GAMME"]
    ]
    BPE_region = BPE_region.merge(gamme_typequ, on="TYPEQU", how="left")

    table_poids_domaine_gamme = pd.DataFrame(
        [
            {"domaine": domaine, "GAMME": gamme, "poids_gamme": poids}
            for domaine, poids_par_gamme in GAMMES_POIDS_PAR_DOMAINE.items()
            for gamme, poids in poids_par_gamme.items()
        ]
    )
    BPE_region["domaine"] = BPE_region["TYPEQU"].str[0]
    BPE_region = BPE_region.merge(table_poids_domaine_gamme, on=["domaine", "GAMME"], how="left")
    return BPE_region


def construire_donnees_region(gtfs_path, on_step=None):
    """Construit (ou recharge depuis le cache disque) le nom de région, le
    découpage communal officiel, le carroyage population INSEE 1km et la BPE
    filtrée/pondérée pour la région desservie par ce GTFS.

    Ne construit PAS le réseau de transport ni la matrice des temps de
    trajet (r5py/osmium) : cf. construire_reseau_region /
    calculer_ttm_region ci-dessous.

    on_step: callback optionnel appelé avec un message avant chaque étape
        potentiellement longue (ex. st.spinner côté Streamlit).

    Retourne un dict : nom_region_str, decoupage_csv, decoupage_geojson,
    population_grid_region, land_use_data, BPE_region.
    """

    def _step(message):
        if on_step is not None:
            on_step(message)

    _step("Identification de la région desservie par le GTFS...")
    nom_region_str = nom_region_via_gtfs(gtfs_path)
    chemins = chemins_region(nom_region_str)

    if not os.path.exists(chemins["decoupage_csv"]):
        _step(f"Découpage communal de {nom_region_str}...")
        build_decoupage_region_via_api(nom_region=nom_region_str, output_path=chemins["decoupage_csv"])
    if not os.path.exists(chemins["decoupage_geojson"]):
        decoupage_agglo_geojson(csv_path=chemins["decoupage_csv"], output_path=chemins["decoupage_geojson"])

    if not os.path.exists(chemins["gpkg"]):
        _step(f"Carroyage population 1km de {nom_region_str}...")
        build_grid_agglo_1km(chemins["decoupage_geojson"], output_path=chemins["gpkg"])
    import geopandas as gpd

    population_grid_region = gpd.read_file(chemins["gpkg"])
    land_use_data = population_grid_region[["id", "population"]].copy()

    _step("Vérification de la base BPE (téléchargement si absente)...")
    assurer_bpe_local()

    _step("Filtrage et pondération de la base BPE...")
    BPE_region = filtre_BPE(chemins["decoupage_csv"], population_grid_region)
    BPE_region = ponderer_bpe(BPE_region)

    equipements_pondere_par_carreau = (
        BPE_region.dropna(subset=["id_carreau", "poids_gamme"]).groupby("id_carreau")["poids_gamme"].sum()
    )
    land_use_data["equipements_pondere"] = land_use_data["id"].map(equipements_pondere_par_carreau).fillna(0.0)

    population_grid_region = filtre_BPE_actifs(population_grid_region, land_use_data)
    land_use_data = land_use_data[land_use_data["id"].isin(population_grid_region["id"])].reset_index(drop=True)

    for domaine, seuil_pct in SEUILS_DOMAINE.items():
        valeurs_domaine = land_use_data_domaine(BPE_region, land_use_data, domaine)
        seuil = seuil_pct * valeurs_domaine[domaine].mean()
        land_use_data[f"pole_equipements_{domaine}"] = (valeurs_domaine[domaine] > seuil).astype(int)

    _step(f"✓ Données de {nom_region_str} prêtes — {len(population_grid_region)} carreaux actifs")

    return {
        "nom_region_str": nom_region_str,
        "decoupage_csv": chemins["decoupage_csv"],
        "decoupage_geojson": chemins["decoupage_geojson"],
        "population_grid_region": population_grid_region,
        "land_use_data": land_use_data,
        "BPE_region": BPE_region,
    }


def construire_reseau_region(gtfs_path, nom_region_str, allow_errors=True, on_step=None):
    """Construit l'objet r5py.TransportNetwork (réseau multimodal) pour la
    région : extrait OSM Geofabrik (mis en cache disque, réutilisable pour
    n'importe quel GTFS de la même région) + GTFS nettoyé pour r5py.

    Pas de mise en cache disque possible pour transport_network lui-même
    (objet Java vivant, pas sérialisable) : à l'appelant (page Streamlit) de
    le garder en mémoire via st.cache_resource plutôt que de rappeler cette
    fonction à chaque rerun.
    """
    import r5py

    def _step(message):
        if on_step is not None:
            on_step(message)

    chemins = chemins_region(nom_region_str)

    _step(f"Extrait OSM de {nom_region_str} (Geofabrik)...")
    telecharger_osm_pbf_geofabrik(nom_region_str, chemins["osm_pbf"])

    _step("Préparation du GTFS pour r5py...")
    gtfs_r5py_path = preparer_gtfs_pour_r5py(gtfs_path)

    _step("Construction du réseau de transport multimodal (peut prendre plusieurs minutes)...")
    transport_network = r5py.TransportNetwork(
        chemins["osm_pbf"], gtfs=[str(gtfs_r5py_path)], allow_errors=allow_errors
    )
    _step(f"✓ Réseau de {nom_region_str} prêt")
    return transport_network


def calculer_ttm_region(
    transport_network,
    population_grid_region,
    nom_region_str,
    date_job,
    max_time_minutes=MAX_TIME_MINUTES_DEFAUT,
    cache_max_age_jours=10,
    on_step=None,
):
    """Calcule (ou recharge depuis le cache disque, s'il a moins de
    cache_max_age_jours) la TravelTimeMatrix de la région, par lots
    d'origines (cf. calculer_ttm_par_lots) — écrite sur disque au fur et à
    mesure, jamais gardée en RAM en une seule fois pendant le calcul.

    Retourne le CHEMIN du parquet, pas un DataFrame chargé : charger_ttm()
    reste sûr en mémoire pour un ttm, mais les calculs en aval (notamment
    calculer_index_benchmark, cf. calculer_index_benchmark_par_lots dans
    src/utilitaires_matrix.py) ont déjà fait exploser la RAM sur un ttm
    complet à l'échelle région/IDFM — autant ne jamais le charger entièrement
    ici, et laisser l'appelant lire ttm_path par lots s'il en a besoin.

    date_job: date de service (format GTFS "%Y%m%d", cf. dates_service) à
        utiliser comme jour de référence pour le calcul, départ 14h00.
    """
    import datetime
    import time

    def _step(message):
        if on_step is not None:
            on_step(message)

    chemins = chemins_region(nom_region_str)
    ttm_path = chemins["ttm"]

    recuperer_depuis_hf(
        f"memory_ttm/ttm_region_{nom_region_str}.parquet", ttm_path, repo_id=HF_DATA_REPO_ID_REGION
    )

    ttm_cache_recent = (
        os.path.exists(ttm_path) and (time.time() - os.path.getmtime(ttm_path)) < cache_max_age_jours * 24 * 3600
    )
    if ttm_cache_recent:
        _step(f"ttm rechargé depuis le cache (< {cache_max_age_jours} jours)")
        return ttm_path

    import r5py

    points = population_grid_region[["id", "geometry"]].copy()
    points["geometry"] = points.geometry.centroid

    departure_datetime = datetime.datetime.strptime(date_job, "%Y%m%d").replace(hour=14, minute=0, second=0)

    calculer_ttm_par_lots(
        r5py,
        transport_network,
        points,
        departure=departure_datetime,
        transport_modes=[r5py.TransportMode.WALK, r5py.TransportMode.TRANSIT],
        max_time_walking=datetime.timedelta(minutes=30),
        max_time=datetime.timedelta(minutes=max_time_minutes),
        ttm_path=ttm_path,
        on_step=_step,
    )
    from src.hf_cache import envoyer_vers_hf

    envoyer_vers_hf(
        ttm_path, f"memory_ttm/ttm_region_{nom_region_str}.parquet", repo_id=HF_DATA_REPO_ID_REGION
    )
    return ttm_path


def calculer_index_accessibilite_region(
    BPE_region, land_use_data, ttm_path, population_grid_region, cutoffs=CUTOFFS_MINUTES_DEFAUT, on_step=None
):
    """Indicateurs d'accessibilité régionale : % moyen (pondéré population)
    d'équipements pondérés accessibles en <= cutoff minutes, par domaine BPE
    et par décile de niveau de vie.

    ttm_path (pas un ttm déjà chargé) : calculer_index_benchmark_par_lots lit
    le ttm par row group pyarrow plutôt qu'un DataFrame complet en mémoire —
    calculer_index_benchmark (la version "tout en mémoire") a déjà fait
    exploser la RAM sur un ttm à l'échelle région/IDFM, cf. sa docstring et
    celle de calculer_index_benchmark_par_lots (src/utilitaires_matrix.py)."""
    niveau_vie = deciles_niveau_vie(population_grid_region)
    return calculer_index_benchmark_par_lots(
        ttm_path, BPE_region, land_use_data, DOMAINES_BPE, niveau_vie, cutoffs=cutoffs, on_step=on_step
    )
