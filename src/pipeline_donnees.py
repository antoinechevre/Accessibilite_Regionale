"""
Construction des données partagées par les pages Streamlit d'accessibilité :
découpage communal, carroyage population INSEE et BPE filtrée/pondérée.
Reprend les cellules "analyse BPE 1.1/1.2" du notebook
index_accessibility_notebook_def.ipynb, avec mise en cache disque par réseau
(nom_reseau_str) pour ne pas tout relancer à chaque rerun Streamlit.

Ne construit PAS le réseau de transport ni la matrice des temps de trajet
(r5py/osmium) : cf. views/accessibilite_index.py pour la suite du pipeline.
"""

import os

import geopandas as gpd
import pandas as pd
import requests

from src.build_data_agglo import (
    build_decoupage_agglo,
    decoupage_agglo_geojson,
    build_grid_agglo,
    build_grid_agglo_1km,
    fusionner_grille_resolution,
    HorsMetropoleError,
)
from src.BPE_traitement import filtre_BPE, filtre_BPE_actifs, land_use_data_domaine
from src.hf_cache import HF_DATA_REPO_ID, envoyer_vers_hf, recuperer_depuis_hf
from src.ponderation_bpe import GAMMES_POIDS_PAR_DOMAINE, SEUILS_DOMAINE
from src.utils import exporter_df_to_csv

# Préfixes de code INSEE (3 premiers caractères) hors France métropolitaine :
# 971 Guadeloupe, 972 Martinique, 973 Guyane, 974 La Réunion,
# 975 Saint-Pierre-et-Miquelon, 976 Mayotte, 977 Saint-Barthélemy,
# 978 Saint-Martin, 986 Wallis-et-Futuna, 987 Polynésie française,
# 988 Nouvelle-Calédonie.
PREFIXES_DEPARTEMENTS_HORS_METROPOLE = ("971", "972", "973", "974", "975", "976", "977", "978", "986", "987", "988")

BASE_DIR = os.getcwd()
DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_CSV_AGGLO_DIR = os.path.join(DATA_DIR, "memory_csv_agglo")
# Mêmes sous-dossiers que le dataset HF distant (memory_gpkg/, memory_pbf/,
# memory_ttm/, cf. recuperer_depuis_hf/envoyer_vers_hf dans ce module et dans
# views/accessibilite_index.py) : la structure locale correspond ainsi
# exactement à la structure distante, plus de fichiers par réseau éparpillés
# à la racine de data/.
MEMORY_GPKG_DIR = os.path.join(DATA_DIR, "memory_gpkg")
MEMORY_PBF_DIR = os.path.join(DATA_DIR, "memory_pbf")
MEMORY_TTM_DIR = os.path.join(DATA_DIR, "memory_ttm")
BPE_PATH = os.path.join(DATA_DIR, "BPE25.parquet")
BPE_XLS_PATH = os.path.join(DATA_DIR, "BPE_gammes_equipements_2025.xlsx")

# Fichier détail BPE25 (géolocalisé, LAMBERT_X/LAMBERT_Y) : cf. cellule
# "#import BPE" du notebook pour comment cette URL a été trouvée.
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

# Réseaux dont la grille 200m est trop grosse pour tenir en mémoire une fois
# la matrice des temps de trajet chargée (cf. src.utilitaires_matrix.charger_ttm) :
# carreaux fusionnés en blocs de resolution mètres avant tout calcul (cf.
# fusionner_grille_resolution), au prix d'une résolution spatiale plus
# grossière sur ces réseaux.
# - Aix_Marseille (800m) : GTFS agrégé "mamp" couvrant bien plus que la seule
#   métropole Aix-Marseille-Provence (283 communes identifiées, de Nice à
#   Nîmes à Briançon — vraisemblablement des lignes TER régionales incluses
#   dans le GTFS), échelle comparable à IDFM. Également une exception au
#   garde-fou "max 4 agences" — cf. GTFS_NOM_RESEAU_FORCE dans app.py.
# - TCL/Lyon (400m) : 92 741 carreaux à 200m -> ttm de 1,22 milliard de
#   lignes, "Memory limit exceeded (32.0G)" sur le Space dès le chargement du
#   ttm en mémoire (charger_ttm) — indépendamment des deux bugs mémoire déjà
#   corrigés par ailleurs (enhanced_2sfca_par_lots, purge de cache r5py qui
#   épargne le .jar). Passage à 400m (fusionner_grille_resolution(...,
#   resolution=400), cf. index_accessibility_notebook_TCL2.ipynb) : ~23 000
#   carreaux, ttm nettement plus petit.
RESOLUTIONS_GRILLE_SPECIALES = {"Aix_Marseille": 800, "TCL": 400}

# Réseaux dont même la grille 200m fusionnée à 800m/1600m/400m (cf.
# RESOLUTIONS_GRILLE_SPECIALES) reste trop volumineuse (ttm en O(n²)) :
# carroyage Filosofi 1km de l'INSEE utilisé directement à la place (cf.
# build_grid_agglo_1km) — déjà le grillage publié, pas de reconstruction
# théorique ni de fusion à faire. Résolution spatiale fixe à 1km (pas de
# palier intermédiaire), et millésime Filosofi 2017 (le 200m est peut-être
# plus récent) : décalage temporel mineur avec le BPE 2025.
# - IDFM : testé à 400m (74 074 carreaux, ttm de 905M lignes) — plante
#   encore avec "Memory limit exceeded (32.0G)" au chargement du ttm, comme
#   à 200m. La marge par rapport à Lyon (26% de lignes en moins) ne suffit
#   pas ; retour au grillage 1km INSEE (nettement moins de carreaux qu'une
#   fusion 400m) pour rester dans la limite mémoire du Space. Également une
#   exception au garde-fou "max 4 agences" — cf. GTFS_NOM_RESEAU_FORCE dans
#   app.py.
RESEAUX_GRILLE_1KM = {"IDFM"}

# Réseaux exclus du fichier CSV de benchmark inter-réseaux
# (index_benchmark_reseaux.csv, cf. calculer_index_benchmark) : IDFM
# (~11 millions d'habitants) est sur une échelle de population sans commune
# mesure avec les autres réseaux du fichier (Lyon ~1M, Toulouse ~1M...) et
# écraserait les nuages de points comparatifs de l'onglet "Benchmark réseaux".
RESEAUX_EXCLUS_BENCHMARK = {"IDFM"}

def chemins_reseau(nom_reseau_str):
    """Chemins de cache disque (par réseau) utilisés par le pipeline — sous
    les mêmes sous-dossiers que le dataset HF distant (memory_gpkg/,
    memory_pbf/, memory_ttm/, cf. recuperer_depuis_hf/envoyer_vers_hf plus
    bas et dans views/accessibilite_index.py), pour que la structure locale
    corresponde à la structure distante plutôt que d'éparpiller des fichiers
    par réseau à la racine de data/.

    decoupage_csv/decoupage_geojson restent à part, dans data/decoupage_agglo/
    (pas memory_csv_agglo/, qui n'a pourtant l'air redondant qu'en apparence) :
    ce sont des fichiers de TRAVAIL reconstruits à chaque run depuis le GTFS
    (cf. plus bas), distincts de decoupage_reference_path/chemin_memoire_decoupage
    (le cache persistant réellement synchronisé avec HF, dans
    MEMORY_CSV_AGGLO_DIR) — mêmes réseau et "decoupage_agglo" dans le nom,
    mais pas le même rôle ni le même contenu (chemin_memoire_decoupage est un
    export reformaté de ce fichier de travail, pas une simple copie). Leur
    donner le même chemin ferait que la référence téléchargée depuis HF au
    tout début de construire_donnees_bpe() serait aussitôt (mé)prise pour le
    résultat déjà construit pour CE run, sautant la (re)construction depuis
    le GTFS.
    """
    for dossier in (MEMORY_GPKG_DIR, MEMORY_PBF_DIR, MEMORY_TTM_DIR):
        os.makedirs(dossier, exist_ok=True)
    dossier_decoupage_travail = os.path.join(DATA_DIR, "decoupage_agglo")
    os.makedirs(dossier_decoupage_travail, exist_ok=True)
    return {
        "decoupage_csv": os.path.join(dossier_decoupage_travail, f"decoupage_agglo_{nom_reseau_str}.csv"),
        "decoupage_geojson": os.path.join(dossier_decoupage_travail, f"decoupage_agglo_{nom_reseau_str}.geojson"),
        "osm_pbf": os.path.join(MEMORY_PBF_DIR, f"agglo_osm_pbf_{nom_reseau_str}.osm.pbf"),
        "gpkg": os.path.join(MEMORY_GPKG_DIR, f"population_grid_agglo_{nom_reseau_str}.gpkg"),
        "ttm": os.path.join(MEMORY_TTM_DIR, f"ttm_{nom_reseau_str}.parquet"),
    }


def assurer_bpe_local():
    """Récupère le fichier détail BPE25 (~160 Mo) si absent en local : d'abord
    depuis le cache HF (plus rapide, déjà téléversé), sinon depuis insee.fr."""
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
    BPE_gammes_equipements_2025.xlsx si absent en local : fichier propre à ce
    projet (pas de source publique équivalente à télécharger)."""
    if os.path.exists(BPE_XLS_PATH):
        return
    if not recuperer_depuis_hf("BPE_gammes_equipements_2025.xlsx", BPE_XLS_PATH):
        raise FileNotFoundError(
            f"{BPE_XLS_PATH} introuvable en local et absent du dataset HF {HF_DATA_REPO_ID}."
        )


def ponderer_bpe(BPE_agglo):
    """Ajoute la colonne poids_gamme à BPE_agglo (cf. notebook "#analyse BPE 1.1")."""
    assurer_bpe_xls_local()
    gamme_typequ = pd.read_excel(
        BPE_XLS_PATH,
        sheet_name="Gammes 2025 1 ligne 1 Typequ",
        header=4,
    )[["TYPEQU", "GAMME"]]

    BPE_agglo = BPE_agglo.merge(gamme_typequ, on="TYPEQU", how="left")

    table_poids_domaine_gamme = pd.DataFrame(
        [
            {"domaine": domaine, "GAMME": gamme, "poids_gamme": poids}
            for domaine, poids_par_gamme in GAMMES_POIDS_PAR_DOMAINE.items()
            for gamme, poids in poids_par_gamme.items()
        ]
    )
    BPE_agglo["domaine"] = BPE_agglo["TYPEQU"].str[0]
    BPE_agglo = BPE_agglo.merge(table_poids_domaine_gamme, on=["domaine", "GAMME"], how="left")
    return BPE_agglo


def construire_donnees_bpe(zip_path, nom_reseau_str, on_step=None):
    """Construit (ou recharge depuis le cache disque) le découpage communal,
    le carroyage population INSEE et la BPE filtrée/pondérée pour ce réseau.

    on_step: callback optionnel appelé avec un message avant chaque étape
        potentiellement longue (ex. st.spinner côté appelant Streamlit).

    Ne construit pas le réseau de transport ni la matrice des temps de trajet
    (osmium/r5py) : cf. views/accessibilite_index.py pour la suite.
    """
    def _step(message):
        if on_step is not None:
            on_step(message)

    chemins = chemins_reseau(nom_reseau_str)

    decoupage_reference_path = os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv")
    recuperer_depuis_hf(f"memory_csv_agglo/decoupage_agglo_{nom_reseau_str}.csv", decoupage_reference_path)
    if not os.path.exists(decoupage_reference_path):
        decoupage_reference_path = None

    if not os.path.exists(chemins["decoupage_csv"]):
        _step("Identification des communes desservies par le GTFS...")
        build_decoupage_agglo(
            gtfs_path=zip_path,
            output_path=chemins["decoupage_csv"],
            decoupage_reference_path=decoupage_reference_path,
        )

        # Vérifié avant toute mise en cache (local + HF) : un GTFS étranger ou
        # d'un DROM-COM ne doit pas polluer le cache partagé avec un
        # découpage vide/hors-périmètre — carroyage Filosofi et BPE ne
        # couvrent que la France métropolitaine.
        codes_insee_agglo = pd.read_csv(chemins["decoupage_csv"], dtype={"code_insee": str})["code_insee"]
        codes_metropole = codes_insee_agglo[~codes_insee_agglo.str[:3].isin(PREFIXES_DEPARTEMENTS_HORS_METROPOLE)]
        if codes_metropole.empty:
            os.remove(chemins["decoupage_csv"])
            raise HorsMetropoleError(
                "Cette application ne fonctionne que pour des villes de France métropolitaine."
            )

        os.makedirs(MEMORY_CSV_AGGLO_DIR, exist_ok=True)
        chemin_memoire_decoupage = os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv")
        exporter_df_to_csv(
            pd.read_csv(chemins["decoupage_csv"], dtype={"code_insee": str}),
            chemin_memoire_decoupage,
        )
        envoyer_vers_hf(chemin_memoire_decoupage, f"memory_csv_agglo/decoupage_agglo_{nom_reseau_str}.csv")
        _step("✓ Découpage communal prêt")

    if not os.path.exists(chemins["decoupage_geojson"]):
        decoupage_agglo_geojson(csv_path=chemins["decoupage_csv"], output_path=chemins["decoupage_geojson"])

    nom_gpkg_hf = f"memory_gpkg/population_grid_agglo_{nom_reseau_str}.gpkg"
    if not os.path.exists(chemins["gpkg"]) and recuperer_depuis_hf(nom_gpkg_hf, chemins["gpkg"]):
        _step("✓ Carroyage population récupéré depuis le cache Hugging Face")

    if not os.path.exists(chemins["gpkg"]):
        if nom_reseau_str in RESEAUX_GRILLE_1KM:
            _step("Construction du carroyage population 1x1km (INSEE Filosofi)...")
            # Chemin de sortie par réseau explicite, comme pour build_grid_agglo
            # ci-dessous : un run concurrent pour un autre réseau ne doit jamais
            # pouvoir écrire/renommer le même fichier partagé.
            build_grid_agglo_1km(chemins["decoupage_geojson"], output_path=chemins["gpkg"])
            _step("✓ Carroyage population prêt")
        else:
            _step("Construction du carroyage population 200x200 (INSEE)...")
            # Chemin de sortie par réseau explicite (pas le chemin générique par
            # défaut de build_grid_agglo) : un run concurrent pour un autre réseau
            # (app ou notebook tournant en parallèle sur la même machine) ne doit
            # jamais pouvoir écrire/renommer le même fichier partagé.
            grille = build_grid_agglo(chemins["decoupage_geojson"], output_path=chemins["gpkg"])
            _step("✓ Carroyage population prêt")

            resolution_speciale = RESOLUTIONS_GRILLE_SPECIALES.get(nom_reseau_str)
            if resolution_speciale is not None:
                # chemins["gpkg"] est déjà scopé par réseau (pas le chemin
                # générique) : l'écraser ici par la version fusionnée ne risque
                # aucune collision avec un autre run, et les prochains lancements
                # pour ce réseau retrouveront directement la version fusionnée en
                # cache (le test os.path.exists ci-dessus ne distingue pas la
                # résolution, juste la présence du fichier).
                grille = fusionner_grille_resolution(grille, resolution=resolution_speciale)
                grille.to_file(chemins["gpkg"], driver="GPKG")

        # Envoi vers le cache HF (mêmes garanties que envoyer_vers_hf ailleurs
        # dans le pipeline : no-op silencieux si HF_TOKEN absent/sans droit
        # d'écriture, pas bloquant si l'envoi échoue).
        envoyer_vers_hf(chemins["gpkg"], nom_gpkg_hf)

    resolution_speciale = RESOLUTIONS_GRILLE_SPECIALES.get(nom_reseau_str)
    if resolution_speciale is not None:
        _step(
            f"⚠ {nom_reseau_str} : réseau trop grand pour une analyse à 200m (matrice des "
            "temps de trajet trop volumineuse pour tenir en mémoire) — carreaux fusionnés "
            f"en blocs de {resolution_speciale}m, résolution spatiale plus grossière sur les "
            "cartes et indicateurs de ce réseau."
        )
    elif nom_reseau_str in RESEAUX_GRILLE_1KM:
        _step(
            f"⚠ {nom_reseau_str} : réseau trop grand pour une fusion de carreaux 200m "
            "(matrice des temps de trajet trop volumineuse même à 800m/1600m) — passage en "
            "carreaux de 1km sur la base INSEE correspondante (carroyage Filosofi 1km, "
            "millésime 2017), résolution spatiale nettement plus grossière sur les cartes et "
            "indicateurs de ce réseau."
        )

    population_grid_agglo = gpd.read_file(chemins["gpkg"])
    land_use_data = population_grid_agglo[["id", "population"]].copy()

    _step("Vérification de la base BPE (téléchargement si absente)...")
    assurer_bpe_local()
    _step("✓ Base BPE disponible")

    _step("Filtrage et pondération de la base BPE...")
    BPE_agglo = filtre_BPE(chemins["decoupage_csv"], population_grid_agglo)
    BPE_agglo = ponderer_bpe(BPE_agglo)

    equipements_pondere_par_carreau = (
        BPE_agglo.dropna(subset=["id_carreau", "poids_gamme"])
        .groupby("id_carreau")["poids_gamme"]
        .sum()
    )
    land_use_data["equipements_pondere"] = (
        land_use_data["id"].map(equipements_pondere_par_carreau).fillna(0.0)
    )

    population_grid_agglo = filtre_BPE_actifs(population_grid_agglo, land_use_data)
    _step(f"✓ BPE pondérée — {len(population_grid_agglo)} carreaux actifs")

    # Restreint aux mêmes carreaux actifs que population_grid_agglo : sans ça,
    # les seuils "pôles" ci-dessous seraient tirés vers le bas par les carreaux
    # vides (cf. notebook "analyse BPE 1.1").
    land_use_data = land_use_data[land_use_data["id"].isin(population_grid_agglo["id"])].reset_index(drop=True)

    # Colonnes pole_equipements_{domaine} : carreau au-dessus de
    # SEUILS_DOMAINE[domaine] fois la moyenne du domaine (cf. notebook
    # "analyse BPE 1.1" et sections 9.1/9.2).
    for domaine, seuil_pct in SEUILS_DOMAINE.items():
        valeurs_domaine = land_use_data_domaine(BPE_agglo, land_use_data, domaine)
        seuil = seuil_pct * valeurs_domaine[domaine].mean()
        land_use_data[f"pole_equipements_{domaine}"] = (valeurs_domaine[domaine] > seuil).astype(int)

    return population_grid_agglo, land_use_data, BPE_agglo
