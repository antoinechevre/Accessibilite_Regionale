import json
import math
import pathlib
import re
import shutil
import subprocess
import time
import unicodedata
import zipfile
from collections import Counter

import geopandas as gpd
import gtfs_kit as gk
import pandas as pd
import requests
import shapely
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import shapely.geometry
import os

from src.hf_cache import recuperer_depuis_hf


class HorsMetropoleError(Exception):
    """Levée quand aucun arrêt du GTFS ne tombe dans la France métropolitaine
    (GTFS étranger, ou d'un DROM-COM) : le carroyage population (Filosofi) et
    la BPE utilisés en aval ne couvrent que la métropole."""


# Boîte englobante large de la France métropolitaine (Corse comprise), avec
# marge de sécurité aux frontières — sert de garde-fou RAPIDE (aucun appel
# réseau) avant le géocodage arrêt par arrêt de codes_communes_via_api, qui
# ferait sinon des milliers d'appels HTTP inutiles (un par arrêt, avec pause
# entre chacun) sur un GTFS étranger volumineux avant de conclure qu'aucun
# arrêt n'est en France — potentiellement des heures pour rien (ex: CTA
# Chicago, ~10 000 arrêts).
LAT_MIN_METROPOLE, LAT_MAX_METROPOLE = 41.0, 51.5
LON_MIN_METROPOLE, LON_MAX_METROPOLE = -5.3, 9.7


BASE_DIR = os.getcwd()  # Remonte d'un niveau depuis scripts/
DATA_DIR = os.path.join(BASE_DIR,"data")

FILOSOFI_ZIP_URL = "https://www.insee.fr/fr/statistiques/fichier/7655475/Filosofi2019_carreaux_200m_gpkg.zip"

# Contrairement au 200m (FILOSOFI_ZIP_URL, téléchargeable directement),
# récupéré manuellement depuis insee.fr (pas d'URL directe stable identifiée) :
# cf. assurer_carreaux_1km_local, repli sur le cache Hugging Face uniquement.
CARREAUX_1KM_PATH = os.path.join(DATA_DIR, "INSEE", "Filosofi2017_carreaux_1km_met.gpkg")


def assurer_carreaux_200m_local():
    """Télécharge et extrait le carroyage population INSEE Filosofi 200m
    (France métropolitaine) depuis insee.fr si absent en local.

    Le zip source (~200 Mo) ne contient pas directement les gpkg mais une
    seule archive .7z imbriquée (vérifié par téléchargement direct : le zip
    n'a qu'un membre, qui se termine par ".7z"), qui elle contient les 3 gpkg
    (métropole/Martinique/Réunion) en un unique bloc solide. Seul
    carreaux_200m_met.gpkg (~1,1 Go décompressé) est conservé, les deux
    autres n'étant pas utilisés par ce projet. Le zip et le .7z intermédiaire
    sont supprimés après extraction pour ne pas tripler l'espace disque
    utilisé. Nécessite le binaire `7z` (paquet p7zip-full, cf. Dockerfile et
    packages.txt) : zipfile (stdlib) ne sait pas lire le format .7z.
    """
    output_dir = pathlib.Path(DATA_DIR) / "extracted"
    output_path = output_dir / "carreaux_200m_met.gpkg"
    if output_path.exists() and output_path.stat().st_size > 0:
        return

    if recuperer_depuis_hf("extracted/carreaux_200m_met.gpkg", str(output_path)):
        print(f"✓ Carroyage population récupéré depuis le cache Hugging Face : {output_path}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "Filosofi2019_carreaux_200m_gpkg.zip"

    print("Téléchargement du carroyage population INSEE Filosofi 200m (~200 Mo)...")
    with requests.get(FILOSOFI_ZIP_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    with zipfile.ZipFile(zip_path) as z:
        membre_7z = next(n for n in z.namelist() if n.endswith(".7z"))
        z.extract(membre_7z, path=output_dir)
    zip_path.unlink()

    print("Extraction de l'archive .7z imbriquée (bloc solide, peut prendre quelques minutes)...")
    archive_7z_path = output_dir / membre_7z
    subprocess.run(
        ["7z", "e", str(archive_7z_path), f"-o{output_dir}", "carreaux_200m_met.gpkg", "-y"],
        check=True,
    )
    archive_7z_path.unlink()

    print(f"✓ Carroyage population téléchargé et extrait : {output_path}")


def session_avec_retries(methods=("GET",), total=5, backoff_factor=1):
    """Session HTTP tolérante aux lenteurs/coupures ponctuelles d'une API distante."""
    session = requests.Session()
    retries = Retry(
        total=total,
        backoff_factor=backoff_factor,  # 1s, 2s, 4s, 8s, 16s... entre les tentatives
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=list(methods),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def codes_communes_via_api(stops_gdf, session, pause=0.05, timeout=30, taille_lot=500, on_step=None, checkpoint_path=None):
    """Reverse-géocode chaque arrêt (lat/lon) en code INSEE via geo.api.gouv.fr.

    Best-effort par arrêt : une erreur ponctuelle sur un arrêt (ex. 502 côté
    API, déjà tolérée jusqu'à 5x par session_avec_retries mais parfois
    persistante quelques minutes) est ignorée plutôt que de perdre tout le
    géocodage déjà fait pour les arrêts précédents — même logique que
    ville_principale ci-dessous.

    Un arrêt = un appel HTTP séquentiel (+ pause) : sur un gros réseau (IDF,
    dizaines de milliers d'arrêts uniques) ça peut prendre des heures sans
    aucun retour, et une interruption (kernel arrêté à la main, coupure
    réseau...) reperdrait tout le géocodage déjà fait. Comme pour
    calculer_ttm_par_lots : progression journalisée par lots de taille_lot
    (on_step, même contrat), et si checkpoint_path est fourni, état
    (codes trouvés + nombre d'arrêts traités) sauvegardé sur disque à la fin
    de chaque lot — une reprise relit ce fichier et continue à partir du
    dernier arrêt traité plutôt que de tout regéocoder depuis le premier.
    """
    stops_liste = list(stops_gdf[["stop_lat", "stop_lon"]].itertuples(index=False))
    nb_lots = math.ceil(len(stops_liste) / taille_lot) if stops_liste else 0

    codes = set()
    debut = 0
    if checkpoint_path is not None and os.path.exists(checkpoint_path):
        with open(checkpoint_path, encoding="utf-8") as f:
            etat = json.load(f)
        codes = set(etat["codes"])
        debut = etat["arrets_traites"]
        print(f"reprise du géocodage depuis le checkpoint : {debut}/{len(stops_liste)} arrêt(s) déjà traité(s)")

    echecs = 0
    for i in range(debut, len(stops_liste)):
        lat, lon = stops_liste[i]
        try:
            r = session.get(
                "https://geo.api.gouv.fr/communes",
                params={"lat": lat, "lon": lon, "fields": "code"},
                timeout=timeout,
            )
            r.raise_for_status()
            codes.update(c["code"] for c in r.json())
        except Exception:
            echecs += 1
        time.sleep(pause)

        fin_de_lot = (i + 1) % taille_lot == 0 or (i + 1) == len(stops_liste)
        if fin_de_lot:
            if on_step is not None:
                on_step(f"Géocodage des arrêts... lot {(i // taille_lot) + 1}/{nb_lots} ({i + 1}/{len(stops_liste)})")
            if checkpoint_path is not None:
                os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
                with open(checkpoint_path, "w", encoding="utf-8") as f:
                    json.dump({"codes": sorted(codes), "arrets_traites": i + 1}, f)

    if checkpoint_path is not None and os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)

    if echecs:
        print(f"⚠ {echecs} arrêt(s) non géocodé(s) (API geo.api.gouv.fr indisponible) — ignoré(s)")
    return codes


def ville_principale(codes_insee, session=None, pause=0.05, timeout=30):
    """Nom de la commune la plus peuplée parmi codes_insee (ex: communes
    desservies par le GTFS, decoupage_agglo["code_insee"]), via l'API
    geo.api.gouv.fr (même source que codes_communes_via_api/details_communes
    ci-dessus, champ "population" = population légale la plus récente).

    Retourne None si codes_insee est vide ou si l'API est inaccessible pour
    toutes les communes — ne doit jamais faire échouer l'appelant (cf.
    usage benchmark, src/utilitaires_matrix.calculer_index_benchmark)."""
    codes_insee = set(codes_insee)
    if not codes_insee:
        return None

    session = session or session_avec_retries()
    population_max = -1
    nom_max = None
    for code in sorted(codes_insee):
        try:
            r = session.get(
                f"https://geo.api.gouv.fr/communes/{code}",
                params={"fields": "nom,population"},
                timeout=timeout,
            )
            r.raise_for_status()
            commune = r.json()
        except Exception:
            continue
        population = commune.get("population") or 0
        if population > population_max:
            population_max = population
            nom_max = commune.get("nom")
        time.sleep(pause)
    return nom_max


def details_communes(codes, session, pause=0.05, timeout=30):
    """Récupère nom/centre/contour de chaque commune (même schéma que decoupage_cda.csv).

    Best-effort par commune : une commune qui échoue (ex. API indisponible)
    est ignorée — absente du découpage — plutôt que de faire échouer tout le
    run, même logique que codes_communes_via_api ci-dessus.
    """
    lignes = []
    for code in sorted(codes):
        try:
            r = session.get(
                f"https://geo.api.gouv.fr/communes/{code}",
                params={"fields": "nom,centre,contour"},
                timeout=timeout,
            )
            r.raise_for_status()
            commune = r.json()
            lon, lat = commune["centre"]["coordinates"]
        except Exception:
            print(f"⚠ Commune {code} non récupérée (API geo.api.gouv.fr indisponible) — ignorée")
            time.sleep(pause)
            continue
        lignes.append({
            "code_insee": commune["code"],
            "nom_commune": commune["nom"],
            "coordinates": f"{lat:.3f},{lon:.3f}",
            "geojson": json.dumps(commune["contour"], separators=(",", ":")),
        })
        time.sleep(pause)
    return lignes


DISTANCE_MAX_ISOLEMENT_KM = 40


def plus_grande_composante_connexe(decoupage_agglo):
    """Exclut, parmi les communes de decoupage_agglo, celles trop
    lointaines du cœur du réseau (DISTANCE_MAX_ISOLEMENT_KM) pour être une
    desserte réelle plutôt qu'un unique arrêt de car interurbain — observé
    sur "Cap Cotentin" avec un arrêt "RENNES-GARE ROUTIERE" qui faisait
    remonter Rennes entière (commune ET population/équipements de son
    carroyage plus loin dans le pipeline, à 159 km du cœur du réseau) dans
    l'agglomération analysée.

    Pas un simple "plus grande composante connexe" (communes strictement
    adjacentes) : un réseau intercommunal réel peut légitimement avoir des
    trous de desserte entre deux zones proches (aucun arrêt dans les
    communes intermédiaires) sans que la zone la plus petite soit pour
    autant hors du périmètre — vérifié sur ce même "Cap Cotentin",
    Valognes/Montebourg/Saint-Sauveur-le-Vicomte etc. à 7-19 km du cœur
    (Cherbourg-en-Cotentin) mais dans une composante séparée, alors que
    Rennes était à 159 km. Le seuil (40 km) sépare largement les deux cas
    observés, à ajuster si un réseau légitimement plus étendu s'avère
    coupé à tort."""
    if len(decoupage_agglo) <= 1:
        return decoupage_agglo

    gdf = gpd.GeoDataFrame(
        decoupage_agglo,
        geometry=decoupage_agglo["geojson"].apply(lambda g: shapely.geometry.shape(json.loads(g))),
        crs="EPSG:4326",
    ).to_crs("EPSG:2154")

    union = gdf.buffer(0).union_all()
    composantes = list(union.geoms) if hasattr(union, "geoms") else [union]
    tailles = [gdf.geometry.intersects(c).sum() for c in composantes]
    coeur = composantes[tailles.index(max(tailles))]

    distances_km = gdf.geometry.distance(coeur) / 1000
    dans_perimetre = distances_km <= DISTANCE_MAX_ISOLEMENT_KM

    nb_exclues = (~dans_perimetre).sum()
    if nb_exclues:
        exclues = decoupage_agglo.loc[~dans_perimetre, "nom_commune"]
        distances_exclues = distances_km.loc[~dans_perimetre]
        detail = ", ".join(f"{nom} ({dist:.0f} km)" for nom, dist in zip(exclues, distances_exclues))
        print(
            f"⚠ {nb_exclues} commune(s) à plus de {DISTANCE_MAX_ISOLEMENT_KM} km du cœur du réseau "
            f"exclue(s) du découpage (desserte ponctuelle probable, ex. terminus interurbain) : {detail}"
        )
    return decoupage_agglo.loc[dans_perimetre].reset_index(drop=True)


def build_decoupage_agglo(
    gtfs_path,
    output_path,
    decoupage_reference_path=None,
    coord_round=4,
    on_step=None,
    checkpoint_path=None,
    filtrer_composante_connexe=True,
):
    """
    Construit un CSV des communes desservies par un GTFS, au même format que
    decoupage_cda.csv (id, code_insee, nom_commune, coordinates, geojson).

    gtfs_path: chemin vers n'importe quel zip GTFS.
    decoupage_reference_path: CSV existant du même format (optionnel), utilisé
        comme cache local pour éviter de géocoder les arrêts qui tombent dans
        des communes déjà connues (ex. decoupage_cda.csv pour le réseau CDA).
    on_step, checkpoint_path: transmis à codes_communes_via_api pour suivre
        la progression et reprendre après une interruption sur un gros GTFS
        (ex. IDFM, dizaines de milliers d'arrêts) — cf. sa docstring.
    filtrer_composante_connexe: applique plus_grande_composante_connexe
        (pertinent pour un réseau agglo/interurbain, cf. sa docstring) — à
        désactiver pour un GTFS régional (ex. TER), qui dessert légitimement
        de vastes zones disjointes plutôt qu'un unique cœur de réseau
        (cf. build_decoupage_region).
    """
    feed = gk.read_feed(gtfs_path, dist_units="km")
    stops = feed.stops[["stop_lat", "stop_lon"]].dropna().round(coord_round).drop_duplicates()

    # Garde-fou rapide (aucun appel réseau) avant le géocodage arrêt par
    # arrêt ci-dessous — cf. HorsMetropoleError plus haut.
    dans_bbox_metropole = stops["stop_lat"].between(LAT_MIN_METROPOLE, LAT_MAX_METROPOLE) & stops[
        "stop_lon"
    ].between(LON_MIN_METROPOLE, LON_MAX_METROPOLE)
    if not dans_bbox_metropole.any():
        raise HorsMetropoleError(
            "Cette application ne fonctionne que pour des villes de France métropolitaine."
        )

    stops_gdf = gpd.GeoDataFrame(
        stops,
        geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]),
        crs="EPSG:4326",
    )

    codes_connus = set()
    communes_connues = pd.DataFrame(columns=["code_insee", "nom_commune", "coordinates", "geojson"])

    if decoupage_reference_path is not None:
        reference = pd.read_csv(decoupage_reference_path, dtype={"code_insee": str})
        reference_gdf = gpd.GeoDataFrame(
            reference,
            geometry=reference["geojson"].apply(lambda g: shapely.geometry.shape(json.loads(g))),
            crs="EPSG:4326",
        )
        joined = gpd.sjoin(stops_gdf, reference_gdf[["code_insee", "geometry"]], how="left", predicate="within")
        codes_connus = set(joined.loc[joined["code_insee"].notna(), "code_insee"])
        stops_gdf = stops_gdf.loc[joined["code_insee"].isna()]
        communes_connues = reference[reference["code_insee"].isin(codes_connus)][
            ["code_insee", "nom_commune", "coordinates", "geojson"]
        ]

    print(f"{len(codes_connus)} commune(s) déjà connue(s), {len(stops_gdf)} arrêt(s) à géocoder")

    with session_avec_retries() as session:
        codes_a_geocoder = (
            codes_communes_via_api(stops_gdf, session, on_step=on_step, checkpoint_path=checkpoint_path)
            if len(stops_gdf)
            else set()
        )
        nouveaux_codes = codes_a_geocoder - codes_connus
        print(f"{len(nouveaux_codes)} nouvelle(s) commune(s) identifiée(s) : {sorted(nouveaux_codes)}")
        nouvelles_lignes = details_communes(nouveaux_codes, session)

    decoupage_agglo = (
        pd.concat([communes_connues, pd.DataFrame(nouvelles_lignes)], ignore_index=True)
        .drop_duplicates(subset="code_insee")
        .sort_values("nom_commune")
        .reset_index(drop=True)
    )
    if filtrer_composante_connexe:
        decoupage_agglo = plus_grande_composante_connexe(decoupage_agglo)
    decoupage_agglo.insert(0, "id", range(1, len(decoupage_agglo) + 1))

    decoupage_agglo.to_csv(output_path, index=False)
    print(f"✓ {len(decoupage_agglo)} commune(s) écrite(s) dans {output_path}")
    return decoupage_agglo


def build_decoupage_region(gtfs_path, output_path, **kwargs):
    """
    Construit un CSV des communes desservies par un GTFS régional (ex. TER),
    au même format et via la même logique que build_decoupage_agglo
    (id, code_insee, nom_commune, coordinates, geojson), mais sans
    plus_grande_composante_connexe : un réseau régional dessert
    légitimement de vastes zones, potentiellement disjointes, plutôt qu'un
    unique cœur de réseau autour duquel exclure les communes isolées.

    gtfs_path: chemin vers n'importe quel zip GTFS régional.
    kwargs: transmis à build_decoupage_agglo (decoupage_reference_path,
        coord_round, on_step, checkpoint_path).
    """
    return build_decoupage_agglo(
        gtfs_path, output_path, filtrer_composante_connexe=False, **kwargs
    )


def nom_region_via_gtfs(gtfs_path, session=None, timeout=30, taille_echantillon=20, seed=0):
    """
    Détermine la région administrative desservie par un GTFS quelconque, à
    partir d'un échantillon de ses arrêts reverse-géocodés (geo.api.gouv.fr,
    un seul appel HTTP par arrêt échantillonné, avec le champ "region" inclus
    directement dans la réponse : pas besoin d'un second appel par commune).

    Contrairement à build_decoupage_agglo (qui géocode TOUS les arrêts pour
    lister les communes desservies), un échantillon suffit ici : un réseau
    régional dessert presque toujours une unique région, et un vote
    majoritaire sur l'échantillon absorbe les quelques arrêts isolés près
    d'une frontière régionale (ex. terminus interurbain dans la région
    voisine) sans avoir à géocoder les dizaines de milliers d'arrêts du GTFS.

    Lève HorsMetropoleError si aucun arrêt du GTFS ne tombe en France
    métropolitaine (même garde-fou que build_decoupage_agglo), ou ValueError
    si l'API n'a renvoyé aucune région exploitable sur l'échantillon.
    """
    feed = gk.read_feed(gtfs_path, dist_units="km")
    stops = feed.stops[["stop_lat", "stop_lon"]].dropna().drop_duplicates()

    dans_bbox_metropole = stops["stop_lat"].between(LAT_MIN_METROPOLE, LAT_MAX_METROPOLE) & stops[
        "stop_lon"
    ].between(LON_MIN_METROPOLE, LON_MAX_METROPOLE)
    stops = stops.loc[dans_bbox_metropole]
    if stops.empty:
        raise HorsMetropoleError(
            "Cette application ne fonctionne que pour des villes de France métropolitaine."
        )

    echantillon = stops.sample(n=min(taille_echantillon, len(stops)), random_state=seed)

    session = session or session_avec_retries()
    regions = []
    for lat, lon in echantillon.itertuples(index=False):
        try:
            r = session.get(
                "https://geo.api.gouv.fr/communes",
                params={"lat": lat, "lon": lon, "fields": "region"},
                timeout=timeout,
            )
            r.raise_for_status()
            resultats = r.json()
            if resultats and resultats[0].get("region"):
                regions.append(resultats[0]["region"]["nom"])
        except Exception:
            continue

    if not regions:
        raise ValueError(
            "Impossible de déterminer la région du GTFS : aucun arrêt de l'échantillon "
            "n'a pu être géocodé (API geo.api.gouv.fr indisponible ?)."
        )

    nom_region, occurrences = Counter(regions).most_common(1)[0]
    print(f"Région détectée via le GTFS : {nom_region} ({occurrences}/{len(regions)} arrêt(s) échantillonné(s))")
    return nom_region


def build_decoupage_region_via_api(nom_region, output_path, session=None, timeout=60):
    """
    Construit un CSV des communes d'une région administrative française,
    au même format que decoupage_agglo.csv (id, code_insee, nom_commune,
    coordinates, geojson), directement depuis le découpage officiel
    (geo.api.gouv.fr) — sans passer par un GTFS.

    Contrairement à build_decoupage_region (qui ne retient que les communes
    desservies par un GTFS donné, via un géocodage arrêt par arrêt pouvant
    prendre des heures sur un réseau régional de dizaines de milliers
    d'arrêts), récupère ici TOUTES les communes de la région en deux appels
    HTTP (résolution du nom en code région, puis liste des communes de ce
    code région) — largement plus rapide, et exhaustif même pour les
    communes non desservies par le GTFS utilisé.

    nom_region: nom de la région (ex. "Bretagne", "Île-de-France").
    """
    session = session or session_avec_retries()

    r = session.get(
        "https://geo.api.gouv.fr/regions",
        params={"nom": nom_region, "fields": "nom,code"},
        timeout=timeout,
    )
    r.raise_for_status()
    resultats = r.json()
    if not resultats:
        raise ValueError(f"Région introuvable auprès de geo.api.gouv.fr : {nom_region!r}")
    code_region = resultats[0]["code"]

    r = session.get(
        "https://geo.api.gouv.fr/communes",
        params={"codeRegion": code_region, "fields": "nom,code,centre,contour"},
        timeout=timeout,
    )
    r.raise_for_status()
    communes = r.json()

    lignes = [
        {
            "code_insee": commune["code"],
            "nom_commune": commune["nom"],
            "coordinates": f"{commune['centre']['coordinates'][1]:.3f},{commune['centre']['coordinates'][0]:.3f}",
            "geojson": json.dumps(commune["contour"], separators=(",", ":")),
        }
        for commune in communes
        if commune.get("centre") and commune.get("contour")
    ]

    decoupage_region = pd.DataFrame(lignes).sort_values("nom_commune").reset_index(drop=True)
    decoupage_region.insert(0, "id", range(1, len(decoupage_region) + 1))

    decoupage_region.to_csv(output_path, index=False)
    print(f"✓ {len(decoupage_region)} commune(s) écrite(s) dans {output_path}")
    return decoupage_region


def decoupage_agglo_geojson(csv_path="data/decoupage_agglo.csv", output_path="data/decoupage_agglo.geojson"):
    """
    Convertit decoupage_agglo.csv en GeoJSON, au même format que decoupage_cda.geojson
    (une Feature par commune, propriétés id/code_insee/nom_commune/coordinates).
    """
    decoupage_agglo = pd.read_csv(csv_path, dtype={"code_insee": str})
    gdf = gpd.GeoDataFrame(
        decoupage_agglo[["id", "code_insee", "nom_commune", "coordinates"]],
        geometry=decoupage_agglo["geojson"].apply(lambda g: shapely.geometry.shape(json.loads(g))),
        crs="EPSG:4326",
    )
    gdf.to_file(output_path, driver="GeoJSON")
    print(f"✓ {len(gdf)} commune(s) écrite(s) dans {output_path}")
    return gdf


def surface_km2_decoupage(csv_path):
    """Surface (km²) de l'agglomération décrite par un decoupage_agglo_*.csv
    (id/code_insee/nom_commune/coordinates/geojson, cf. build_decoupage_agglo)
    : union des géométries communales, reprojetée en Lambert-93 (EPSG:2154,
    mètres) pour une surface exacte — les degrés lat/lon ne donnent pas une
    surface en km² directement. buffer(0) répare les géométries invalides
    (auto-intersections, déjà observées sur des communes du Fond de plan
    IGN) avant l'union, même précaution que build_grid_agglo."""
    decoupage = pd.read_csv(csv_path, dtype={"code_insee": str}).drop_duplicates(subset="code_insee")
    gdf = gpd.GeoDataFrame(
        decoupage,
        geometry=decoupage["geojson"].apply(lambda g: shapely.geometry.shape(json.loads(g))),
        crs="EPSG:4326",
    )
    gdf.geometry = gdf.geometry.buffer(0)
    agglo_2154 = gpd.GeoSeries([gdf.union_all()], crs=gdf.crs).to_crs("EPSG:2154")
    return agglo_2154.area.iloc[0] / 1e6

def _tuiles_bbox(min_lon, min_lat, max_lon, max_lat, taille_deg):
    """Découpe une bbox en tuiles carrées d'au plus `taille_deg` degrés de côté.

    Pour les grandes agglomérations, interroger Overpass sur toute l'emprise en une
    seule requête dépasse vite les limites de taille/temps du service public. On
    découpe donc en tuiles plus petites, récupérées séparément puis fusionnées.
    """
    tuiles = []
    lat = min_lat
    while lat < max_lat:
        haut = min(lat + taille_deg, max_lat)
        lon = min_lon
        while lon < max_lon:
            droite = min(lon + taille_deg, max_lon)
            tuiles.append((lon, lat, droite, haut))
            lon = droite
        lat = haut
    return tuiles


def _telecharger_tuile_overpass(bbox, output_path, session, overpass_url, timeout):
    """Télécharge les données OSM d'une tuile (bbox) via Overpass, au format XML."""
    min_lon, min_lat, max_lon, max_lat = bbox
    query = (
        f"[out:xml][timeout:{timeout}];"
        f"(node({min_lat},{min_lon},{max_lat},{max_lon});"
        f"way({min_lat},{min_lon},{max_lat},{max_lon});"
        f"relation({min_lat},{min_lon},{max_lat},{max_lon}););"
        # (._;>;) : récupère aussi tous les nœuds référencés par les ways/relations
        # ci-dessus, même hors de la bbox interrogée. Sans ça, un way qui ne fait que
        # longer/traverser le bord de la bbox (route, rivière...) est renvoyé avec sa
        # liste de nœuds, mais seuls les nœuds tombant dans la bbox sont eux-mêmes
        # présents dans la réponse — les autres sont des références "dans le vide".
        # osmium (merge/extract) ne valide pas cette cohérence et laisse passer un
        # .osm.pbf avec des ways à la géométrie trouée, mais r5py, plus strict,
        # échoue dessus ("Writer thread failed" / "Error occurred while parsing OSM
        # file"). Vérifié sur une tuile réelle près de Périgueux : 3450 nœuds
        # référencés sur 15794 manquaient sans cette clause, 0 avec.
        "(._;>;);"
        "out body;"  # tags + géométrie seulement (pas d'historique d'édition : ~3-4x plus léger que "out meta")
    )
    headers = {"User-Agent": "Dossier_index_def/1.0 (build_data_agglo.py)"}
    response = session.post(
        overpass_url, data={"data": query}, headers=headers, timeout=timeout + 30
    )
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)


def _ways_avec_geometrie_cassee(pbf_path):
    """Vérifie l'intégrité référentielle du .osm.pbf produit par osmium
    extract : repère les ways taggés highway=* auxquels il ne reste, une
    fois les nœuds manquants exclus, plus qu'au maximum 1 nœud résolvable —
    donc plus aucune géométrie exploitable (une ligne a besoin d'au moins 2
    points).

    Un way highway=* avec quelques nœuds manquants (simple clip au bord
    d'une tuile Overpass, cf. commentaire (._;>;) plus haut) reste
    inoffensif : osmium le tolère et r5py aussi tant qu'il reste au moins 2
    nœuds pour tracer un segment — vérifié sur l'extrait de TAM (en
    production), dont le pire way affecté retombe à 4 nœuds résolvables.
    Mais quand Overpass tronque sa réponse sur une tuile, un way peut perdre
    la quasi-totalité de ses nœuds : observé sur le GTFS d'IDELIS (Pau), où
    2 ways highway (dont une highway=primary) étaient réduits à 1 seul nœud
    résolvable sur 11 à 19 déclarés. C'est ce genre de way à la géométrie
    dégénérée que le lecteur OSM de r5py rejette avec "Writer thread
    failed", pas le simple clip de bord toléré par ailleurs.

    Retourne la liste des identifiants de way (str) concernés, vide si le
    fichier est utilisable tel quel.
    """
    verif = subprocess.run(
        ["osmium", "check-refs", "-i", str(pbf_path)],
        capture_output=True, text=True,
    )
    manquants_par_way = {}
    for ligne in verif.stdout.splitlines():
        if " in w" not in ligne:
            continue
        way_id = ligne.split(" in w", 1)[1].strip()
        manquants_par_way[way_id] = manquants_par_way.get(way_id, 0) + 1

    ways_casses = []
    for way_id, nb_manquants in manquants_par_way.items():
        opl = subprocess.run(
            ["osmium", "getid", str(pbf_path), f"w{way_id}", "-f", "opl"],
            capture_output=True, text=True,
        ).stdout.strip()
        if "highway=" not in opl or " N" not in opl:
            continue
        nb_total = len(opl.split(" N", 1)[1].strip().split(","))
        if nb_total - nb_manquants < 2:
            ways_casses.append(way_id)
    return ways_casses


def osm_pbf_creator(
    decoupage_agglo_path,
    output_pbf_path=None,
    tile_size_deg=0.3,
    overpass_url="https://overpass-api.de/api/interpreter",
    timeout=180,
    pause=1.0,
    max_essais=3,
):
    """Build agglo.osm.pbf: données OSM découpées sur l'emprise de decoupage_agglo_path.

    Équivalent de r5py.sampledata.helsinki.osm_pbf, mais pour n'importe quelle agglo :
    au lieu de dépendre d'un extrait régional Geofabrik pré-découpé (qui ne couvre
    qu'une zone géographique fixe), les données OSM sont téléchargées directement sur
    l'emprise du GeoJSON fourni via l'API Overpass, puis découpées précisément sur le
    contour réel de l'agglo avec osmium. Fonctionne donc pour n'importe quelle
    géographie dans le monde.

    Pour les grandes agglomérations, l'emprise est découpée en tuiles d'au plus
    `tile_size_deg` degrés de côté (0.3° ≈ 30 km) afin de rester sous les limites de
    taille/temps de l'API Overpass publique ; les tuiles sont téléchargées une par une
    (avec retries automatiques et une pause de `pause` secondes entre chacune, pour ne
    pas surcharger le service public) puis fusionnées avant le découpage final.

    Requires osmium-tool (macOS: `brew install osmium-tool`).

    decoupage_agglo_path: chemin vers un GeoJSON de communes (ex. decoupage_agglo.geojson).
    output_pbf_path: chemin du .osm.pbf en sortie (par défaut : "agglo.osm.pbf" à côté
        de decoupage_agglo_path).
    tile_size_deg: taille max d'une tuile Overpass, en degrés. Réduire cette valeur
        (ex. 0.15) si Overpass renvoie des erreurs de timeout/taille sur une très
        grande agglomération.
    overpass_url: instance Overpass à utiliser (changer en cas de limitation de débit
        sur l'instance publique par défaut, ex. "https://overpass.kumi.systems/api/interpreter").
    timeout: timeout Overpass par tuile, en secondes.
    max_essais: nombre de tentatives si l'extrait produit s'avère corrompu
        (cf. _ways_avec_geometrie_cassee) — chaque nouvel essai retélécharge
        avec des tuiles deux fois plus petites, pour réduire le risque
        qu'Overpass tronque à nouveau sa réponse au même endroit.
    """
    output_dir = pathlib.Path(decoupage_agglo_path).parent
    if output_pbf_path is None:
        output_pbf_path = output_dir / "agglo.osm.pbf"
    BOUNDARY_GEOJSON = output_dir / "agglo_boundary.geojson"
    OUTPUT_PBF = output_pbf_path

    if shutil.which("osmium") is None:
        raise SystemExit(
            "osmium-tool is required but not found. Install it with: brew install osmium-tool"
        )

    # 1. Dissolve the agglo communes into a single boundary polygon for osmium extract
    agglo = gpd.read_file(decoupage_agglo_path)
    agglo = agglo.set_crs("EPSG:4326") if agglo.crs is None else agglo
    agglo.geometry = agglo.geometry.buffer(0)  # fix invalid geometries before dissolving

    boundary = gpd.GeoDataFrame(geometry=[agglo.union_all()], crs=agglo.crs)
    boundary.to_file(BOUNDARY_GEOJSON, driver="GeoJSON")
    print(f"wrote {BOUNDARY_GEOJSON}, bounds: {boundary.total_bounds}")

    # 2-3. Télécharger les données OSM couvrant l'emprise via Overpass, tuile par
    # tuile (ne dépend d'aucun découpage régional préexistant, marche pour
    # n'importe quelle zone), fusionner puis découper précisément sur le contour
    # réel de l'agglo (les tuiles Overpass sont rectangulaires, plus larges que le
    # contour). Recommencé jusqu'à max_essais fois si l'extrait obtenu s'avère
    # corrompu (cf. _ways_avec_geometrie_cassee) — Overpass tronque parfois sa
    # réponse sur une tuile sans erreur HTTP, laissant des ways à la géométrie
    # trouée qu'osmium tolère mais que r5py rejette plus loin dans le pipeline.
    ways_casses = []
    for essai in range(1, max_essais + 1):
        min_lon, min_lat, max_lon, max_lat = boundary.total_bounds
        tuiles = _tuiles_bbox(min_lon, min_lat, max_lon, max_lat, tile_size_deg)
        print(
            f"emprise découpée en {len(tuiles)} tuile(s) de {tile_size_deg}° "
            f"pour Overpass (essai {essai}/{max_essais})"
        )

        fichiers_tuiles = []
        with session_avec_retries(methods=("GET", "POST"), total=8, backoff_factor=2) as session:
            for i, bbox in enumerate(tuiles, start=1):
                tuile_path = output_dir / f"agglo_tuile_{i}.osm"
                print(f"téléchargement tuile {i}/{len(tuiles)} (bbox {bbox}) ...")
                _telecharger_tuile_overpass(bbox, tuile_path, session, overpass_url, timeout)
                fichiers_tuiles.append(tuile_path)
                time.sleep(pause)

        if len(fichiers_tuiles) > 1:
            fusion_path = output_dir / "agglo_fusion.osm.pbf"
            subprocess.run(
                ["osmium", "merge", *fichiers_tuiles, "-o", fusion_path, "--overwrite"],
                check=True,
            )
            print(f"wrote {fusion_path} (fusion de {len(fichiers_tuiles)} tuiles)")
        else:
            fusion_path = fichiers_tuiles[0]

        subprocess.run(
            [
                "osmium",
                "extract",
                "-p",
                BOUNDARY_GEOJSON,
                "-o",
                OUTPUT_PBF,
                "--overwrite",
                fusion_path,
            ],
            check=True,
        )
        print(f"wrote {OUTPUT_PBF}")

        # Fichiers intermédiaires de CET essai, plus utiles qu'il ait réussi ou
        # non — à ne pas laisser traîner ni réutiliser par erreur à l'essai suivant
        for f in fichiers_tuiles:
            pathlib.Path(f).unlink()
        if len(fichiers_tuiles) > 1:
            pathlib.Path(fusion_path).unlink()

        ways_casses = _ways_avec_geometrie_cassee(OUTPUT_PBF)
        if not ways_casses:
            break
        print(
            f"⚠ extrait OSM avec {len(ways_casses)} way(s) routier(s) à la géométrie "
            f"dégénérée (id : {', '.join(ways_casses[:10])}"
            f"{', ...' if len(ways_casses) > 10 else ''}) — probable troncature "
            f"Overpass sur une tuile, nouvel essai avec des tuiles plus petites"
        )
        tile_size_deg = tile_size_deg / 2
    else:
        raise RuntimeError(
            f"Extrait OSM toujours corrompu après {max_essais} essai(s) : "
            f"{len(ways_casses)} way(s) routier(s) à la géométrie dégénérée "
            f"(id : {', '.join(ways_casses)}) — probablement une troncature "
            f"systématique d'Overpass sur cette zone plutôt qu'un aléa ponctuel."
        )



def slug_geofabrik(nom_region):
    """Convertit un nom de région français en slug Geofabrik (ex. "Bretagne"
    -> "bretagne", "Provence-Alpes-Côte d'Azur" -> "provence-alpes-cote-d-azur").

    Geofabrik référence encore les 22 anciennes régions françaises
    (pré-réforme de 2016) : "Bretagne", "Pays de la Loire"... correspondent
    directement à un slug, mais les régions fusionnées en 2016
    (Nouvelle-Aquitaine, Occitanie, Grand Est, Hauts-de-France,
    Auvergne-Rhône-Alpes) n'ont pas de slug unique chez Geofabrik et ne sont
    pas gérées ici."""
    slug = unicodedata.normalize("NFKD", nom_region).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()


def telecharger_osm_pbf_geofabrik(nom_region, output_path, timeout=300):
    """
    Télécharge l'extrait OSM (.osm.pbf) pré-découpé par Geofabrik pour une
    région administrative française, en un seul fichier.

    Alternative à osm_pbf_creator (tuiles Overpass, pensé pour une emprise de
    taille agglo) à l'échelle d'une région entière : le découpage en tuiles
    de ~30km d'osm_pbf_creator représenterait pour une région (ex. Bretagne,
    ~27 000 km²) des dizaines d'appels à l'API Overpass publique (taux
    limité, potentiellement 30-60+ minutes et fragile à cette échelle), alors
    que Geofabrik fournit ce même extrait déjà découpé, en un seul
    téléchargement fiable.

    nom_region: nom de la région (ex. "Bretagne") — converti en slug
    Geofabrik via slug_geofabrik (cf. sa docstring pour la limite sur les
    régions fusionnées en 2016).

    Ne re-télécharge pas si output_path existe déjà (fichier volumineux,
    ex. ~300 Mo pour la Bretagne) — supprimer le fichier local pour forcer
    une mise à jour.

    Téléchargé d'abord vers un fichier temporaire (.part) puis renommé vers
    output_path seulement si la taille reçue correspond au Content-Length
    annoncé par le serveur : sans cette vérification, une connexion coupée en
    cours de route (ex. Wi-Fi instable sur un fichier de cette taille)
    laisserait un .osm.pbf tronqué mais présent, qui passerait silencieusement
    le test "déjà présent en local" ci-dessus à chaque run suivant tout en
    faisant planter r5py (StreetLayer.loadFromOsm avec une erreur Java peu
    explicite, ex. ArrayIndexOutOfBoundsException) sans jamais se re-télécharger.
    """
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        print(f"Extrait OSM déjà présent en local, pas de téléchargement : {output_path}")
        return output_path

    slug = slug_geofabrik(nom_region)
    url = f"https://download.geofabrik.de/europe/france/{slug}-latest.osm.pbf"

    print(f"Téléchargement de l'extrait OSM Geofabrik ({url})...")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    chemin_temporaire = f"{output_path}.part"
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        taille_attendue = int(r.headers["content-length"]) if "content-length" in r.headers else None
        with open(chemin_temporaire, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    taille_obtenue = os.path.getsize(chemin_temporaire)
    if taille_attendue is not None and taille_obtenue != taille_attendue:
        os.remove(chemin_temporaire)
        raise IOError(
            f"Téléchargement incomplet de {url} : {taille_obtenue} octet(s) reçus sur "
            f"{taille_attendue} attendus (connexion interrompue ?) — relancer la cellule."
        )

    os.replace(chemin_temporaire, output_path)
    print(f"✓ Extrait OSM téléchargé : {output_path}")
    return output_path




def build_grid_agglo(path, output_path=None):
    """Build population_grid_cda: full 200m grid clipped to the CDA La Rochelle boundary.

    Includes cells not published by INSEE Filosofi (population too low to satisfy
    statistical secrecy, generally < 11 households) with population=0, rather than
    only the sparse subset of cells that Filosofi publishes.

    output_path: chemin de sortie du gpkg. Par défaut (None), le chemin générique
    historique data/population_grid_agglo.gpkg (utilisé par le notebook, qui le
    relit ensuite depuis ce même chemin fixe) — à fournir explicitement (chemin
    par réseau) pour tout appelant tournant potentiellement en parallèle d'un
    autre run (ex. l'app, cf. src/pipeline_donnees.py), afin d'éviter que deux
    runs concurrents n'écrivent/renomment le même fichier partagé.
    """
    assurer_carreaux_200m_local()

    agglo = gpd.read_file(path)
    agglo = agglo.set_crs("EPSG:4326") if agglo.crs is None else agglo
    agglo.geometry = agglo.geometry.buffer(0)
    agglo_boundary = gpd.GeoDataFrame(
        geometry=[agglo.union_all()], crs=agglo.crs
    ).to_crs("EPSG:2154")

    # Grille Filosofi publiée par l'INSEE (uniquement les carreaux avec assez de
    # ménages pour respecter le secret statistique) : sert à récupérer les données
    # démographiques là où elles existent.
    #
    # columns=["idcar_200m", "ind", "ind_snv"] : le gpkg source contient ~40
    # colonnes (revenus, logement, tranches d'âge...) mais le pipeline n'utilise
    # que "ind" (nombre d'individus -> population) et "ind_snv" (indice de
    # niveau de vie, utilisé pour les analyses d'inégalité d'accessibilité par
    # décile, cf. notebook "analyse accessibilite / pop"). Ne charger que ces
    # colonnes (+ la géométrie, toujours incluse) réduit fortement la mémoire
    # nécessaire à la lecture d'un fichier de 1,1 Go, important sur les hôtes
    # à RAM limitée (cf. Streamlit Community Cloud).
    minx, miny, maxx, maxy = agglo_boundary.total_bounds
    grid_publiee = gpd.read_file(
        f"{DATA_DIR}/extracted/carreaux_200m_met.gpkg",
        bbox=(minx, miny, maxx, maxy),
        columns=["idcar_200m", "ind", "ind_snv"],
    )

    # La grille Filosofi 200m est définie nativement en EPSG:3035 (ETRS89-LAEA) :
    # idcar_200m encode le coin sud-ouest du carreau dans ce système, ex:
    # "CRS3035RES200mN2607600E3467800" -> N=2607600, E=3467800 (vérifié : reconstruire
    # le carreau à partir de ces coordonnées puis reprojeter en EPSG:2154 reproduit
    # exactement la géométrie fournie par l'INSEE). Pour générer TOUS les carreaux
    # théoriques de la zone (y compris ceux non publiés), on construit donc la
    # grille dans ce système natif, puis on la reprojette.
    RESOLUTION = 200
    agglo_boundary_3035 = agglo_boundary.to_crs("EPSG:3035")
    minx3035, miny3035, maxx3035, maxy3035 = agglo_boundary_3035.total_bounds

    n_start = int(miny3035 // RESOLUTION) * RESOLUTION
    n_end = int(maxy3035 // RESOLUTION + 1) * RESOLUTION
    e_start = int(minx3035 // RESOLUTION) * RESOLUTION
    e_end = int(maxx3035 // RESOLUTION + 1) * RESOLUTION

    ids = []
    cells = []
    for n in range(n_start, n_end, RESOLUTION):
        for e in range(e_start, e_end, RESOLUTION):
            ids.append(f"CRS3035RES200mN{n}E{e}")
            cells.append(shapely.geometry.box(e, n, e + RESOLUTION, n + RESOLUTION))

    grille_theorique = gpd.GeoDataFrame(
        {"idcar_200m": ids}, geometry=cells, crs="EPSG:3035"
    ).to_crs("EPSG:2154")

    # Ne garder que les carreaux théoriques dont le centroïde tombe dans la CDA
    centroids = grille_theorique.geometry.centroid
    within_mask = centroids.within(agglo_boundary.geometry.iloc[0])
    population_grid_agglo = grille_theorique.loc[within_mask].copy()
    population_grid_agglo["centroid_x"] = centroids.loc[within_mask].x
    population_grid_agglo["centroid_y"] = centroids.loc[within_mask].y

    # Rattachement des données Filosofi publiées (population, revenus, etc.) sur
    # les carreaux théoriques : les carreaux non publiés (secret statistique)
    # n'ont pas de correspondance et restent à combler.
    colonnes_filosofi = [c for c in grid_publiee.columns if c not in ("idcar_200m", "geometry")]
    population_grid_agglo = population_grid_agglo.merge(
        grid_publiee[["idcar_200m", *colonnes_filosofi]],
        on="idcar_200m",
        how="left",
        indicator="publie",
    )
    population_grid_agglo["publie"] = population_grid_agglo["publie"] == "both"

    # Les colonnes numériques (population, revenus, logements...) valent 0 là où
    # l'INSEE n'a rien publié. Les colonnes identifiantes/catégorielles (idcar_1km,
    # lcog_geo...) ne sont pas dérivables sans le référentiel INSEE et restent vides.
    colonnes_numeriques = population_grid_agglo[colonnes_filosofi].select_dtypes("number").columns
    population_grid_agglo[colonnes_numeriques] = population_grid_agglo[colonnes_numeriques].fillna(0)

    population_grid_agglo["population"] = population_grid_agglo["ind"]
    population_grid_agglo["id"] = population_grid_agglo["idcar_200m"]  # required by r5py.TravelTimeMatrix

    output_path = output_path or f"{DATA_DIR}/population_grid_agglo.gpkg"
    population_grid_agglo.to_file(output_path, driver="GPKG")

    print(f"carreaux dans l'agglo (grille complète): {len(population_grid_agglo)}")
    print(f"dont publiés par l'INSEE: {population_grid_agglo['publie'].sum()}")
    print(f"dont non publiés (secret statistique, population mise à 0): {(~population_grid_agglo['publie']).sum()}")
    print(f"population totale (ind): {population_grid_agglo['ind'].sum():.0f}")
    print(f"ecrit dans: {output_path}")

    return population_grid_agglo


def assurer_carreaux_1km_local():
    """Récupère le carroyage population INSEE Filosofi 1km (France
    métropolitaine, CARREAUX_1KM_PATH) depuis le cache Hugging Face si absent
    en local.

    Contrairement au 200m (assurer_carreaux_200m_local), pas de téléchargement
    automatique depuis insee.fr ici : aucune URL directe stable identifiée
    pour ce produit (contrairement à FILOSOFI_ZIP_URL) — récupéré une
    première fois manuellement depuis insee.fr, puis mis en cache sur HF pour
    les déploiements suivants. Lève une erreur explicite si absent des deux
    côtés, plutôt que de planter plus loin avec une erreur gpkg peu claire.
    """
    if os.path.exists(CARREAUX_1KM_PATH):
        return
    if recuperer_depuis_hf("Filosofi2017_carreaux_1km_met.gpkg", CARREAUX_1KM_PATH):
        print(f"✓ Carroyage population 1km récupéré depuis le cache Hugging Face : {CARREAUX_1KM_PATH}")
        return
    raise FileNotFoundError(
        f"{CARREAUX_1KM_PATH} introuvable en local et absent du cache Hugging Face. "
        "À télécharger manuellement depuis insee.fr (carroyage Filosofi 1km, "
        "France métropolitaine) et placer à ce chemin."
    )


def build_grid_agglo_1km(decoupage_geojson_path, output_path=None):
    """Alternative à build_grid_agglo() (200m) : construit population_grid_agglo
    directement depuis le carroyage Filosofi 1km de l'INSEE (CARREAUX_1KM_PATH,
    cf. assurer_carreaux_1km_local).

    Pensé pour les très grandes agglomérations (ex: IDFM/Île-de-France) où
    même le 200m fusionné à 800m/1600m via fusionner_grille_resolution reste
    trop volumineux (carreaux, donc ttm en O(n²), en plus grand nombre qu'à
    1km directement) — "Memory limit exceeded" observé sur le Space avec
    l'approche 200m->fusion pour IDFM.

    Contrairement au 200m (grille théorique reconstruite + carreaux non
    publiés comblés à population=0, cf. build_grid_agglo), le fichier 1km de
    l'INSEE ne contient déjà QUE les carreaux publiés : pas de reconstruction
    théorique ni de colonne "publie" à combler ici, on filtre juste sur
    l'emprise de l'agglo.

    Retourne les mêmes colonnes que build_grid_agglo (id/geometry/population/
    ind/ind_snv/publie/centroid_x/centroid_y), pour rester utilisable sans
    changement par le reste du pipeline (filtre_BPE, cumulative_cutoff...).
    """
    assurer_carreaux_1km_local()

    agglo = gpd.read_file(decoupage_geojson_path)
    agglo = agglo.set_crs("EPSG:4326") if agglo.crs is None else agglo
    agglo.geometry = agglo.geometry.buffer(0)
    agglo_boundary = gpd.GeoDataFrame(geometry=[agglo.union_all()], crs=agglo.crs).to_crs("EPSG:2154")

    minx, miny, maxx, maxy = agglo_boundary.total_bounds
    grille = gpd.read_file(
        CARREAUX_1KM_PATH,
        bbox=(minx, miny, maxx, maxy),
        columns=["Idcar_1km", "Ind", "Ind_snv"],
    )

    within_mask = grille.geometry.centroid.within(agglo_boundary.geometry.iloc[0])
    population_grid_agglo = grille.loc[within_mask].copy()
    population_grid_agglo = population_grid_agglo.rename(
        columns={"Idcar_1km": "id", "Ind": "ind", "Ind_snv": "ind_snv"}
    )
    population_grid_agglo["population"] = population_grid_agglo["ind"]
    population_grid_agglo["publie"] = True
    centroids = population_grid_agglo.geometry.centroid
    population_grid_agglo["centroid_x"] = centroids.x
    population_grid_agglo["centroid_y"] = centroids.y

    output_path = output_path or f"{DATA_DIR}/population_grid_agglo_1km.gpkg"
    population_grid_agglo.to_file(output_path, driver="GPKG")

    print(f"carreaux 1km dans l'agglo: {len(population_grid_agglo)}")
    print(f"population totale (ind): {population_grid_agglo['ind'].sum():.0f}")
    print(f"ecrit dans: {output_path}")

    return population_grid_agglo


def fusionner_grille_resolution(population_grid_agglo, resolution=400):
    """Fusionne les carreaux 200m de population_grid_agglo (sortie de
    build_grid_agglo : id/geometry/population/ind/ind_snv/publie/centroid_x/
    centroid_y) en carreaux de `resolution` mètres (multiple de 200), pour
    réduire le nombre de carreaux — et donc la taille de la matrice des
    temps de trajet (ttm), qui grandit en O(n²) avec le nombre de carreaux.

    Nécessaire sur les très grosses agglomérations (ex. Lyon/TCL : 92 741
    carreaux à 200m -> ttm de 1,22 milliard de lignes, qui dépasse la RAM
    disponible même à 32 Go une fois chargé en mémoire, y compris avec les
    dtypes compacts de charger_ttm). Passer à 400m réduit le nombre de
    carreaux d'un facteur ~4 (2x2 en x et y), donc le ttm d'un facteur ~16.

    Regroupement par bloc de `resolution` mètres à partir des coordonnées N/E
    natives EPSG:3035 encodées dans "id" (ex: "CRS3035RES200mN2607600E3467800"
    -> N=2607600, E=3467800), pas des centroid_x/centroid_y (EPSG:2154,
    calculées par build_grid_agglo après reprojection). Utiliser les
    centroïdes reprojetés a été essayé puis abandonné : la reprojection
    EPSG:3035 -> EPSG:2154 déforme légèrement les coordonnées (pas une simple
    translation), donc quelques carreaux 200m proches d'une frontière de bloc
    basculaient dans le bloc voisin selon la distorsion locale — observé sur
    Lyon/TCL, carte "Tout équipements pondérés" avec des blocs 400m aux
    contours en dents de scie / valeurs incohérentes d'un bloc à l'autre.
    Les N/E natifs de "id", eux, sont des entiers exacts alignés sur la
    grille par construction (cf. build_grid_agglo) : aucune dérive possible
    quel que soit l'endroit du monde. ind
    (population) et ind_snv (Filosofi : déjà une SOMME des niveaux de vie
    winsorisés des individus du carreau, pas une moyenne) se somment
    naturellement sur les sous-carreaux d'un même bloc.

    Ne récupère PAS les données masquées par le secret statistique (les
    carreaux 200m non publiés par l'INSEE restent à 0 dans la somme) :
    réduit seulement le volume de calcul, ce n'est pas un redressement
    statistique. `publie` du carreau fusionné est True si au moins un des
    sous-carreaux 200m était publié.

    geometry : union des sous-carreaux effectivement présents (pas une
    reconstruction théorique du carré `resolution`x`resolution`) — gère
    proprement les blocs incomplets en bord d'agglo (1 à 3 sous-carreaux
    sur 4 au lieu de 4, la grille 200m étant déjà découpée à la frontière de
    l'agglo par build_grid_agglo).
    """
    grille = population_grid_agglo.copy()
    coords_natives = grille["id"].str.extract(r"N(\d+)E(\d+)").astype(int)
    bloc_n = coords_natives[0] // resolution * resolution
    bloc_e = coords_natives[1] // resolution * resolution
    grille["id"] = "CRS3035RES" + str(resolution) + "mN" + bloc_n.astype(str) + "E" + bloc_e.astype(str)

    fusionnee = grille.dissolve(by="id", aggfunc={"ind": "sum", "ind_snv": "sum", "publie": "any"}).reset_index()
    fusionnee["population"] = fusionnee["ind"]

    print(
        f"grille fusionnée en carreaux de {resolution}m : {len(population_grid_agglo)} -> "
        f"{len(fusionnee)} carreaux (facteur {len(population_grid_agglo) / len(fusionnee):.1f}x)"
    )

    return fusionnee[["id", "geometry", "population", "ind", "ind_snv", "publie"]]



