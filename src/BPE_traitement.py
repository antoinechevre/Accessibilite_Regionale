import pandas as pd
import os
import folium
import geopandas as gpd
import requests

from src.cartographie import script_legende_en_bas, script_reajuster_si_masque, script_synchroniser_zoom
from src.hf_cache import recuperer_depuis_hf


BASE_DIR = os.getcwd()  # Remonte d'un niveau depuis scripts/

BPE_PATH=os.path.join(BASE_DIR,'data','INSEE',"BPE25.parquet") # base de données BPE https://catalogue-donnees.insee.fr/fr/catalogue/recherche/DS_BPE 2024

BPE_URL = "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.parquet"

def import_BPE(BPE_URL): 
    # Télécharge le fichier détail BPE25 (géolocalisé, LAMBERT_X/LAMBERT_Y) le plus
    # récent depuis insee.fr si absent en local. Mis à jour mensuellement par
    # l'INSEE (cf. https://www.insee.fr/fr/statistiques/8217525) ; on ne
    # re-télécharge pas à chaque run pour éviter de retélécharger 160+ Mo à chaque
    # fois (supprimer le fichier local pour forcer une mise à jour).
   
    if not os.path.exists(BPE_PATH) and recuperer_depuis_hf("BPE25.parquet", BPE_PATH):
        print(f"BPE25 récupéré depuis le cache Hugging Face : {BPE_PATH}")
        return

    if not os.path.exists(BPE_PATH):
        print(f"Téléchargement du BPE25 depuis {BPE_URL}...")
        with requests.get(BPE_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(BPE_PATH, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        print(f"✓ BPE25 téléchargé : {BPE_PATH}")
    else:
        print(f"BPE25 déjà présent en local, pas de téléchargement : {BPE_PATH}")


# Paris/Lyon/Marseille (PLM) : ces 3 villes sont subdivisées en arrondissements
# municipaux, chacun avec son propre code INSEE, EN PLUS du code de la ville
# entière. Les géocodeurs utilisés pour construire decoupage_agglo.csv (cf.
# src/build_data_agglo.codes_communes_via_api) renvoient le code de ville
# entière (13055, 69123, 75056), mais le BPE (colonne DEPCOM) localise chaque
# équipement au niveau de l'arrondissement (13201-13216, 69381-69389,
# 75101-75120) — jamais au code de ville entière. Sans expansion, le filtre
# DEPCOM ci-dessous exclut donc silencieusement TOUS les équipements de la
# ville (observé sur Marseille : les 43 445 équipements de la ville, 100%,
# disparaissent de la carte au profit des seules communes voisines).
CODES_ARRONDISSEMENTS_PLM = {
    "75056": [f"751{i:02d}" for i in range(1, 21)],  # Paris
    "69123": [f"6938{i}" for i in range(1, 10)],  # Lyon
    "13055": [f"132{i:02d}" for i in range(1, 17)],  # Marseille
}


def filtre_BPE (DECOUPAGE_COM_PATH_CSV,population_grid_agglo):

    # BPE25.parquet (INSEE) est un parquet tabulaire classique (colonnes LONGITUDE/
    # LATITUDE, pas de géométrie WKB/métadonnées GeoParquet) : gpd.read_parquet()
    # échoue avec "Missing geo metadata". pd.read_parquet() est la bonne fonction ici.
    BPE_agglo = pd.read_parquet(BPE_PATH) #étape intermédiaire charge l'ensemble de la BDD

    # decoupage_agglo.csv est plus simple que le .geojson ici : il suffit d'une jointure
    # attributaire sur le code commune INSEE (pas besoin de jointure spatiale avec
    # reprojection). code_insee est un int (ex: 17300) ; DEPCOM dans le BPE est une
    # chaîne de 5 caractères (ex: "17300") : on caste et on zero-pad pour faire matcher.
    codes_insee_agglo = (pd.read_csv(DECOUPAGE_COM_PATH_CSV))["code_insee"].astype(str).str.zfill(5)
    codes_insee_agglo = pd.concat(
        [codes_insee_agglo]
        + [pd.Series(CODES_ARRONDISSEMENTS_PLM[code]) for code in codes_insee_agglo if code in CODES_ARRONDISSEMENTS_PLM]
    ).drop_duplicates()

    BPE_agglo = BPE_agglo[BPE_agglo["DEPCOM"].isin(codes_insee_agglo)].copy() # sélectionne BDD BPE seulement sur découpage agglo.csv 

    print(f"{len(BPE_agglo)} équipements dans l'agglo (sur {codes_insee_agglo.size} communes)")

    # Liste des types d'équipements présents (TYPEQU = code le plus fin de la
    # nomenclature BPE, ex: "C1", "D201"... ; le parquet ne contient pas les libellés
    # associés, seulement les codes). DOM/SDOM donnent des catégories plus larges
    # (domaine / sous-domaine) si TYPEQU est trop détaillé pour ton usage.
    print(BPE_agglo["TYPEQU"].value_counts())

    # Rattachement de BDE_cda au carroyage population_grid_cda : jointure spatiale.
    # LAMBERT_X/LAMBERT_Y du BPE sont déjà en EPSG:2154 (vérifié : identique à la CRS
    # de population_grid_cda), donc pas besoin de reprojection.
    BPE_agglo = gpd.GeoDataFrame(
        BPE_agglo,
        geometry=gpd.points_from_xy(BPE_agglo["LAMBERT_X"], BPE_agglo["LAMBERT_Y"]),
        crs=population_grid_agglo.crs,
    )

    BPE_agglo = gpd.sjoin(
        BPE_agglo,
        population_grid_agglo[["id", "geometry"]],
        predicate="within",
        how="left",
    ).rename(columns={"id": "id_carreau"})
    
    return BPE_agglo


#analyse BPE 1.2

# Ne garder que les carreaux "actifs" pour l'analyse BPE : ceux qui ont de la
# population ou au moins un équipement pondéré (equipements_pondere, calculé en
# cellule 4). Les carreaux vides (ni habitants ni équipement) n'apportent rien
# aux cartes/calculs suivants.

def filtre_BPE_actifs (population_grid_agglo,land_use_data):

    total_carreaux = len(population_grid_agglo)
    carreaux_actifs = land_use_data.loc[
        (land_use_data["population"] > 0) | (land_use_data["equipements_pondere"] > 0),
        "id",
    ]
    population_grid_agglo = population_grid_agglo[population_grid_agglo["id"].isin(carreaux_actifs)].copy()

    print(
        f"{len(population_grid_agglo)} carreaux actifs conservés sur {total_carreaux} "
        "(population ou équipements)"
    )
    return population_grid_agglo

# Cartes de la pondération par gamme des équipements, par domaine BPE et par
# carreau (population_grid_cda) — pas l'accessibilité en temps de trajet, juste
# la donnée d'offre brute (land_use_data_domaine).

def carte_ponderation_domaine(DOMAINES_BPE,population_grid_agglo,BPE_agglo,land_use_data,domaine,tiles="CartoDB positron", canal_sync=None, bounds=None):
    """Carte interactive de la pondération cumulée par gamme d'un domaine BPE, par carreau.

    canal_sync : si fourni, synchronise le zoom/centre de cette carte avec
    toute autre carte utilisant le même canal (cf.
    src.cartographie.script_synchroniser_zoom) — utilisé par
    views/accessibilite_urbaine_2.py pour lier cette carte à celle
    d'accessibilité 45 min du même domaine ; None (défaut) pour un usage
    autonome (ex: views/ponderation_equipements.py), pas de synchronisation.

    bounds : [[miny, minx], [maxy, maxx]] à utiliser pour le cadrage initial
    (script_reajuster_si_masque) au lieu de le calculer à partir de
    `population_grid_agglo` — utilisé par views/accessibilite_urbaine_2.py
    pour partager le MÊME cadrage que la carte 45 min du même domaine.
    population_grid_agglo (tous les carreaux de l'agglomération, y compris
    ceux hors de la zone couverte par la matrice de temps de trajet) donne
    en effet un total_bounds bien plus large que celui de la carte 45 min
    (limitée aux carreaux d'origine de cette matrice) : sans ce partage, les
    deux cartes démarrent sur des cadrages différents, indépendamment de
    canal_sync (qui ne synchronise que les déplacements/zooms ultérieurs,
    pas le cadrage initial de chaque carte). None (défaut) : calcule le
    cadrage localement, comme avant — comportement inchangé pour un usage
    autonome (ex: views/ponderation_equipements.py)."""
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)
    grille = population_grid_agglo[["id", "geometry"]].merge(land_use_data_domaine(BPE_agglo, land_use_data, domaine), on="id")

    # scheme="NaturalBreaks" plutôt qu'une échelle linéaire brute (défaut de
    # .explore()) : la pondération par domaine est très asymétrique (quelques
    # carreaux avec un score très supérieur au reste, ex: pôle universitaire
    # pour "C"), donc une échelle linéaire écrase la quasi-totalité des
    # carreaux dans une seule couleur sombre. "Quantiles" ne convient pas non
    # plus ici : trop de carreaux à 0 sur un domaine donné, mapclassify
    # réduirait k à 2 classes (0 vs tout le reste).
    # style_kwds : contours des carreaux transparents (weight=0, opacity=0),
    # comme pour overview_map_cda (cellule 2) — sinon le quadrillage noir
    # écrase le fond de carte.
    carte = grille.explore(
        column=domaine,
        cmap="inferno",
        scheme="NaturalBreaks",
        k=5,
        tiles=tiles,
        legend=True,
        legend_kwds={"caption": f"{nom_domaine} (pondéré)"},
        style_kwds={"weight": 0, "opacity": 0},
        # prefer_canvas : argument direct de .explore() (pas dans map_kwds,
        # que geopandas rejette explicitement pour ce paramètre) — cf. même
        # correctif dans accessibilite_index.py.
        prefer_canvas=True,
    )

    if bounds is None:
        minx, miny, maxx, maxy = grille.to_crs(epsg=4326).total_bounds
        bounds = [[miny, minx], [maxy, maxx]]
    carte.get_root().html.add_child(
        folium.Element(script_reajuster_si_masque(carte, bounds))
    )
    carte.get_root().html.add_child(folium.Element(script_legende_en_bas()))

    if canal_sync:
        carte.get_root().html.add_child(folium.Element(script_synchroniser_zoom(carte, canal_sync)))

    return carte


def mask_domaine_bpe(BPE_agglo,domaine):
    """Masque booléen sur BDE_cda pour un domaine donné. "O" = tous les équipements."""
    if domaine == "O":
        return pd.Series(True, index=BPE_agglo.index)
    return BPE_agglo["TYPEQU"].str.startswith(domaine)


def land_use_data_domaine(BPE_agglo, land_use_data,domaine):
    """
    land_use_data pour un domaine BPE donné (A-G, ou "O" pour tous), avec la
    pondération cumulée par gamme des équipements de ce domaine par carreau
    (poids_gamme, calculé plus haut) — pas un simple comptage.
    """
    ponderation_par_carreau = (
        BPE_agglo[mask_domaine_bpe(BPE_agglo, domaine)]
        .dropna(subset=["id_carreau", "poids_gamme"])
        .groupby("id_carreau")["poids_gamme"]
        .sum()
    )

    df = land_use_data[["id"]].copy()
    df[domaine] = df["id"].map(ponderation_par_carreau).fillna(0.0)
    return df

