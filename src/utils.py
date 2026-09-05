import csv
import io
import os
import pathlib
import zipfile

import geopandas as gpd
import gtfs_kit as gk
import pandas as pd
import numpy as np
from shapely import wkt


########################################################################
# HELPERS GTFS
########################################################################

# Correspondance des codes route_type du GTFS vers un libellé lisible
# https://gtfs.org/schedule/reference/#routestxt
LIBELLES_MODE = {
    0: "Tram",
    1: "Métro",
    2: "Train",
    3: "Bus",
    4: "Ferry",
    5: "Tram (câble)",
    6: "Téléphérique",
    7: "Funiculaire",
    11: "Trolleybus",
    12: "Monorail",
}


def _aplatir_zip_gtfs(zip_path):
    """Remet à la racine du zip les tables GTFS placées dans un sous-dossier,
    et retire les résidus non-GTFS (dossier __MACOSX/, fichiers AppleDouble
    "._*") avant chargement (réécrit le zip en place si besoin).

    Observé sur le GTFS de Périgueux (CA Grand Périgueux) : toutes les
    tables sont zippées sous "ca_grand_perigueux-aggregated-gtfs/" plutôt
    qu'à la racine, avec en plus un dossier __MACOSX/ ajouté par macOS lors
    de la compression. gtfs_kit tolère ce genre de zip (retrouve les
    fichiers par nom quel que soit leur chemin), mais r5py (lecteur GTFS
    Conveyal/OneBusAway, cf. preparer_gtfs_pour_r5py ci-dessous) le rejette
    avec TableInSubdirectoryError — plus sûr de corriger une fois ici, en
    amont de tous les lecteurs, que côté r5py seulement.
    """
    zip_path = pathlib.Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        entrees = []
        noms_vus = set()
        modifie = False
        for item in z.infolist():
            if item.is_dir():
                modifie = True
                continue
            nom = item.filename
            base = nom.rsplit("/", 1)[-1]
            if "__MACOSX" in nom.split("/") or base.startswith("._") or not base:
                modifie = True
                continue
            if base != nom:
                modifie = True
            if base in noms_vus:
                # collision improbable (même nom dans deux sous-dossiers) :
                # on garde la première entrée rencontrée
                continue
            noms_vus.add(base)
            entrees.append((base, z.read(item.filename)))

        if not modifie:
            return zip_path

    print(f"GTFS aplati (sous-dossier / résidus __MACOSX retirés) dans {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for nom, data in entrees:
            zout.writestr(nom, data)
    return zip_path


def _retirer_tabulations_finales_du_zip(zip_path):
    """Retire les tabulations parasites en fin de ligne (en-tête compris) de
    chaque table texte du zip GTFS zip_path (réécrit en place si besoin).

    Observé sur le GTFS d'IDELIS (Pau) : routes.txt et trips.txt ont chacun
    une colonne en trop côté export (probablement une colonne vide dans le
    tableur source), qui se traduit par des tabulations en fin de chaque
    ligne — y compris l'en-tête, où la dernière colonne s'appelle
    littéralement "bikes_allowed\t\t\t\t\t\t\t\t\t" au lieu de
    "bikes_allowed". Un champ CSV n'étant délimité que par des virgules, ces
    tabulations restent collées à la dernière valeur : gtfs_kit/pandas
    tolèrent ce bruit (ignoré par un strip() en aval), mais le lecteur GTFS
    de r5py (Conveyal/OneBusAway) échoue à parser bikes_allowed comme entier
    et lève NumberParseError sur chaque ligne de trips.txt.
    """
    zip_path = pathlib.Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        noms_a_nettoyer = []
        contenu = {}
        for nom in z.namelist():
            with z.open(nom) as f:
                texte = io.TextIOWrapper(f, "utf-8", errors="ignore").read() if nom.endswith(".txt") else None
            if texte is None:
                contenu[nom] = z.read(nom)
                continue
            lignes = texte.splitlines()
            nettoyees = [ligne.rstrip("\t") for ligne in lignes]
            if nettoyees != lignes:
                noms_a_nettoyer.append(nom)
                contenu[nom] = ("\n".join(nettoyees) + "\n").encode("utf-8")
            else:
                contenu[nom] = z.read(nom)

    if not noms_a_nettoyer:
        return

    print(f"Tabulations en fin de ligne retirées de {', '.join(noms_a_nettoyer)} dans {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for nom, data in contenu.items():
            zout.writestr(nom, data)


def _retirer_table_vide_du_zip(zip_path, nom_fichier):
    """Retire nom_fichier du zip GTFS zip_path (réécrit en place) s'il est
    présent mais vide (en-tête seul, aucune ligne de données).

    Un fichier GTFS présent-mais-vide est valide selon la spec (ex:
    calendar_dates.txt vide quand calendar.txt porte tout le calendrier),
    mais gtfs_kit (gk.read_feed) le rejette avec une EmptyTableError plutôt
    que de le traiter comme absent — même situation que calendar.txt côté
    r5py, cf. preparer_gtfs_pour_r5py ci-dessous, mais avec le lecteur
    gtfs_kit cette fois (utilisé lui par tout le reste du pipeline).
    """
    zip_path = pathlib.Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        if nom_fichier not in z.namelist():
            return
        with z.open(nom_fichier) as f:
            nb_lignes = sum(1 for _ in csv.reader(io.TextIOWrapper(f, "utf-8"))) - 1
        if nb_lignes > 0:
            return
        contenu = {n: z.read(n) for n in z.namelist() if n != nom_fichier}

    print(f"{nom_fichier} vide dans {zip_path.name} : retrait avant chargement gtfs_kit")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for nom, data in contenu.items():
            zout.writestr(nom, data)


def _nettoyer_espaces_dates_du_zip(zip_path, nom_fichier, colonnes_date):
    """Retire les espaces en début/fin de valeur dans colonnes_date de
    nom_fichier, dans le zip GTFS zip_path (réécrit en place si besoin).

    Observé sur le GTFS du Mans : quelques lignes de calendar.txt ont un
    end_date avec un espace en trop ("20260830 " au lieu de "20260830") —
    valide en apparence, mais gtfs_kit (datetime.strptime via
    gtfs_kit.helpers.datestr_to_date) échoue dessus avec "ValueError:
    unconverted data remains: " avant même de charger le feed, plutôt que
    d'ignorer l'espace.
    """
    zip_path = pathlib.Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        if nom_fichier not in z.namelist():
            return
        with z.open(nom_fichier) as f:
            texte = io.TextIOWrapper(f, "utf-8").read()

    lignes = texte.splitlines()
    if not lignes:
        return
    reader = csv.reader(lignes)
    entete = next(reader)
    indices = [entete.index(c) for c in colonnes_date if c in entete]
    if not indices:
        return

    modifie = False
    lignes_nettoyees = [entete]
    for ligne in reader:
        for idx in indices:
            if idx < len(ligne) and ligne[idx] != ligne[idx].strip():
                ligne[idx] = ligne[idx].strip()
                modifie = True
        lignes_nettoyees.append(ligne)

    if not modifie:
        return

    print(f"{nom_fichier} : espaces en trop retirés des colonnes date dans {zip_path.name}")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(lignes_nettoyees)

    with zipfile.ZipFile(zip_path) as z:
        contenu = {n: z.read(n) for n in z.namelist() if n != nom_fichier}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for nom, data in contenu.items():
            zout.writestr(nom, data)
        zout.writestr(nom_fichier, buffer.getvalue())


def charger_gtfs(zip_path):
    """
    Charge le fichier GTFS à l'aide de gtfs_kit.
    Returns:
        feed: gtfs_kit Feed object
    """
    _aplatir_zip_gtfs(zip_path)
    _retirer_tabulations_finales_du_zip(zip_path)
    _retirer_table_vide_du_zip(zip_path, "calendar_dates.txt")
    _nettoyer_espaces_dates_du_zip(zip_path, "calendar.txt", ["start_date", "end_date"])
    _nettoyer_espaces_dates_du_zip(zip_path, "calendar_dates.txt", ["date"])
    print(f"Chargement du fichier GTFS : {zip_path}")
    feed = gk.read_feed(zip_path, dist_units='km')
    print(f"✓ GTFS chargé avec succès")
    return feed


# Tables optionnelles de la spec GTFS observées présentes-mais-vides (en-tête
# seul, aucune ligne de données) dans des exports réels — valide selon la
# spec, mais le lecteur GTFS utilisé par r5py (Conveyal/OneBusAway) distingue
# "fichier absent" de "fichier présent mais vide" et rejette le second cas
# avec une EmptyTableError au lieu de l'ignorer. calendar.txt : vérifié sur
# le GTFS Tisseo (Toulouse) ; transfers.txt : vérifié sur le GTFS Valence ;
# frequencies.txt et fare_rules.txt : vérifiés sur le GTFS du réseau de
# Grenoble ; shapes.txt : vérifié sur le GTFS de Thionville.
TABLES_OPTIONNELLES_VIDABLES_R5PY = [
    "calendar.txt", "transfers.txt", "frequencies.txt", "fare_rules.txt", "shapes.txt",
]


def preparer_gtfs_pour_r5py(zip_path, output_path=None):
    """
    Retire du GTFS les tables de TABLES_OPTIONNELLES_VIDABLES_R5PY présentes
    mais vides (cf. commentaire ci-dessus).

    Si aucune de ces tables n'est présente-mais-vide, le zip d'origine est
    renvoyé tel quel (aucune copie créée).

    zip_path: chemin vers le GTFS à préparer.
    output_path: chemin du GTFS nettoyé (par défaut : "<zip_path stem>_r5py.zip").
    Returns: chemin du GTFS à utiliser avec r5py.TransportNetwork(gtfs=...).
    """
    zip_path = pathlib.Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        noms_presents = set(z.namelist())
        a_retirer = []
        for nom in TABLES_OPTIONNELLES_VIDABLES_R5PY:
            if nom not in noms_presents:
                continue
            with z.open(nom) as f:
                nb_lignes = sum(1 for _ in csv.reader(io.TextIOWrapper(f, "utf-8"))) - 1
            if nb_lignes <= 0:
                a_retirer.append(nom)

        if not a_retirer:
            return zip_path

        for nom in a_retirer:
            print(f"{nom} vide dans {zip_path.name} : retrait avant chargement r5py")
        if output_path is None:
            output_path = zip_path.with_name(f"{zip_path.stem}_r5py.zip")
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in z.infolist():
                if item.filename in a_retirer:
                    continue
                zout.writestr(item, z.read(item.filename))
    print(f"✓ GTFS nettoyé écrit dans {output_path}")
    return output_path





def longueur_lignes(feed):
    """
    Calcule la longueur (km) de chaque ligne (route_id).

    shapes.txt est un fichier optionnel de la spec GTFS (absent par
    exemple du jeu de données TCL) : quand il n'est pas fourni, la
    longueur est estimée à partir des arrêts desservis par chaque trip
    (distance à vol d'oiseau cumulée entre arrêts consécutifs), plutôt
    que depuis les tracés géométriques.
    """
    if feed.shapes is None or feed.shapes.empty:
        print("⚠ shapes.txt absent du GTFS : longueur des lignes estimée à partir des arrêts (distance à vol d'oiseau)")
        return _longueur_lignes_depuis_arrets(feed)

    geo_shapes = gk.geometrize_shapes(feed.shapes, use_utm=True)
    geo_shapes['longueur_km'] = geo_shapes.geometry.length / 1000
    # Associer chaque shape à sa ligne
    trips_shapes = feed.trips[['route_id', 'shape_id']].drop_duplicates()
    geo_shapes = geo_shapes.merge(trips_shapes, on='shape_id')
    longueur_par_ligne = geo_shapes.groupby('route_id')['longueur_km'].max().reset_index()
    return longueur_par_ligne


def _longueur_lignes_depuis_arrets(feed):
    """
    Longueur (km) de chaque ligne à partir des coordonnées des arrêts
    (fallback utilisé par longueur_lignes quand shapes.txt est absent).
    """
    stops = feed.stops.set_index('stop_id')[['stop_lat', 'stop_lon']]

    stop_times = feed.stop_times.merge(feed.trips[['trip_id', 'route_id']], on='trip_id')
    stop_times = stop_times.sort_values(['trip_id', 'stop_sequence'])
    stop_times = stop_times.merge(stops, on='stop_id')

    stop_times['lat_suivant'] = stop_times.groupby('trip_id')['stop_lat'].shift(-1)
    stop_times['lon_suivant'] = stop_times.groupby('trip_id')['stop_lon'].shift(-1)

    segments = stop_times.dropna(subset=['lat_suivant', 'lon_suivant'])

    R = 6371  # rayon de la Terre en km
    lat1, lon1, lat2, lon2 = map(
        np.radians,
        [segments['stop_lat'], segments['stop_lon'], segments['lat_suivant'], segments['lon_suivant']],
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    segments = segments.assign(longueur_km=R * 2 * np.arcsin(np.sqrt(a)))

    longueur_par_trip = segments.groupby(['trip_id', 'route_id'])['longueur_km'].sum().reset_index()
    longueur_par_ligne = longueur_par_trip.groupby('route_id')['longueur_km'].max().reset_index()
    return longueur_par_ligne

def km_par_ligne_jour(feed, longueur_par_ligne,date):
    """
    Calcule le total des kilomètres parcourus par ligne pour une journée donnée.

    Parameters
    ----------
    feed : gtfs_kit.Feed
        Le feed GTFS chargé.
    date : str
        Date au format YYYYMMDD.

    Returns
    -------
    DataFrame
        DataFrame avec route_id, date et le total des kilomètres parcourus.
    """
    
    active_trips = feed.get_trips(date=date)

    if active_trips.empty:
        print(f"⚠️ Aucune course pour la date {date}. Vérifiez la date ou la période de service du GTFS.")
        return pd.DataFrame(columns=['route_id', 'total_km', 'date'])

    nb_manquants = longueur_par_ligne['longueur_km'].isna().sum()
    if nb_manquants > 0:
        print(f"⚠️ {date} : {nb_manquants} routes sans longueur de shape associée")

    # Associer chaque trip actif à la longueur de son tracé
    trips_avec_longueur = active_trips.merge(longueur_par_ligne, on='route_id', how='left')

    # Sommer les km parcourus par ligne (chaque trip = un aller ou retour)
    km_par_ligne_jour = (
        trips_avec_longueur.groupby('route_id')['longueur_km']
        .sum()
        .reset_index()
        .rename(columns={'longueur_km': 'total_km'})
    )

    km_par_ligne_jour['date'] = date

    return km_par_ligne_jour

def km_par_ligne_plage(dates_service,feed):
    # Calcul jour par jour sur toute la plage
    longueur_par_ligne=longueur_lignes(feed)
    resultats_journaliers = []
    for date in dates_service:
        resultats_journaliers.append(km_par_ligne_jour(feed, longueur_par_ligne, date))

    total_vkm_par_jour = pd.concat(resultats_journaliers, ignore_index=True)

    # Agrégation finale : somme des km par ligne sur l'année entière
    total_vkm_per_plage = (
        total_vkm_par_jour.groupby('route_id')['total_km']
        .sum()
        .reset_index()
        .rename(columns={'total_km': 'total_km_plage'})
    )

    # Ajout des noms de lignes et du mode de transport pour la lisibilité
    total_vkm_per_plage = total_vkm_per_plage.merge(
        feed.routes[['route_id', 'route_short_name', 'route_long_name', 'route_type']],
        on='route_id',
        how='left'
    )
    total_vkm_per_plage['mode'] = (
        total_vkm_per_plage['route_type'].map(LIBELLES_MODE).fillna(total_vkm_per_plage['route_type'].astype(str))
    )
    return total_vkm_per_plage

def obtenir_service_ids_pour_date(feed, date_str):
    """
    Identifie les service_id actifs pour une date donnée
    en tenant compte de calendar et calendar_dates
    Args:
        feed: gtfs_kit Feed object
        date_str (str): Date au format 'YYYYMMDD'
    Returns:
        list[str]: Liste des service_id actifs
    """
    date_obj = pd.to_datetime(date_str, format='%Y%m%d')
    jour_semaine = date_obj.strftime('%A').lower()  # lundi, mardi, etc.
    
    # Mapping jour de la semaine -> colonne calendar
    jour_mapping = {
        'monday': 'monday',
        'tuesday': 'tuesday', 
        'wednesday': 'wednesday',
        'thursday': 'thursday',
        'friday': 'friday',
        'saturday': 'saturday',
        'sunday': 'sunday'
    }
    
    service_ids = set()
    
    # 1. Vérifier calendar.txt
    if hasattr(feed, 'calendar') and feed.calendar is not None:
        calendar = feed.calendar.copy()
        # Convertir les dates
        calendar['start_date'] = pd.to_datetime(calendar['start_date'], format='%Y%m%d')
        calendar['end_date'] = pd.to_datetime(calendar['end_date'], format='%Y%m%d')
        
        # Filtrer les services actifs ce jour
        jour_col = jour_mapping[jour_semaine]
        services_calendar = calendar[
            (calendar['start_date'] <= date_obj) &
            (calendar['end_date'] >= date_obj) &
            (calendar[jour_col] == 1)
        ]['service_id'].tolist()
        
        service_ids.update(services_calendar)
    
    # 2. Vérifier calendar_dates.txt (exceptions)
    if hasattr(feed, 'calendar_dates') and feed.calendar_dates is not None:
        calendar_dates = feed.calendar_dates.copy()
        calendar_dates['date'] = pd.to_datetime(calendar_dates['date'], format='%Y%m%d')
        
        exceptions = calendar_dates[calendar_dates['date'] == date_obj]
        
        for _, row in exceptions.iterrows():
            if row['exception_type'] == 1:  # Service ajouté
                service_ids.add(row['service_id'])
            elif row['exception_type'] == 2:  # Service retiré
                service_ids.discard(row['service_id'])
    
    service_ids = list(service_ids)
    print(f"✓ Services actifs le {date_str} : {len(service_ids)} service(s)")
    return service_ids


########################################################################
# UTILITAIRES D'EXPORT ET DE LECTURE
########################################################################


def exporter_df_to_csv(df, chemin_fichier):
    """
    Exporte un DataFrame en CSV
    
    Parameters:
    -----------
    df : DataFrame
        DataFrame à exporter
    chemin_fichier : str
        Chemin du fichier de sortie
    """
    df.to_csv(chemin_fichier, index=False, encoding='utf-8-sig')
    print(f"✓ CSV exporté : {chemin_fichier}")
    
def exporter_gdf_to_csv(gdf, chemin_fichier):
    """
    Exporte un GeoDataFrame en CSV sans la geometry
    
    Parameters:
    -----------
    gdf : GeoDataFrame
        GeoDataFrame à exporter
    chemin_fichier : str
        Chemin du fichier de sortie
    """
    df = gdf.drop(columns=['geometry'], errors='ignore')
    df.to_csv(chemin_fichier, index=False, encoding='utf-8-sig')
    print(f"✓ CSV exporté : {chemin_fichier}")


def exporter_geojson(gdf, chemin_fichier):
    """
    Exporte un GeoDataFrame en GeoJSON.
    
    Parameters:
    -----------
    gdf : GeoDataFrame
        GeoDataFrame à exporter
    chemin_fichier : str
        Chemin du fichier de sortie
    """
    gdf.to_file(chemin_fichier, driver='GeoJSON')
    print(f"✓ GeoJSON exporté : {chemin_fichier}")


def charger_csv_avec_geometrie(chemin_fichier):
    """
    Charge un CSV et le retourne en GeoDataFrame s'il contient une colonne
    'geometry' (en WKT), ou en DataFrame classique sinon — les indicateurs
    par arrêt (cf. calculer_indicateurs_arrets, src/arrets.py) n'ont pas de
    géométrie (juste stop_lat/stop_lon), donc mis en cache sans colonne
    'geometry' : geopandas interdit d'assigner un crs à un GeoDataFrame
    sans géométrie.

    Parameters:
    -----------
    chemin_fichier : str
        Chemin du fichier CSV

    Returns:
    --------
    GeoDataFrame ou DataFrame
    """
    df = pd.read_csv(chemin_fichier)

    if 'geometry' in df.columns:
        df['geometry'] = df['geometry'].apply(wkt.loads)
        return gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')

    return df


def charger_ou_calculer_gdf(chemin_cache, fonction_calcul):
    """
    Cache disque pour une étape de calcul coûteuse (tronçons uniques,
    indicateurs de fréquentation par tronçon...) : charge chemin_cache s'il
    existe déjà, sinon appelle fonction_calcul() et sauvegarde le résultat
    pour que les prochaines exécutions (notebook relancé, app redémarrée,
    même réseau resélectionné) n'aient pas à tout recalculer.

    Sûr à réutiliser d'une exécution à l'autre car date_JOB est désormais
    déterministe pour un GTFS donné (cf. dates_service dans info_reseau.py) :
    les indicateurs par tronçon ne varient donc pas d'un run à l'autre tant
    que le GTFS ne change pas.

    Parameters:
    -----------
    chemin_cache : str
        Chemin du fichier CSV de cache (créé si absent).
    fonction_calcul : callable
        Fonction sans argument à appeler si le cache est absent ; doit
        renvoyer un DataFrame ou GeoDataFrame.

    Returns:
    --------
    DataFrame ou GeoDataFrame
    """
    if os.path.exists(chemin_cache):
        print(f"✓ Chargé depuis le cache : {chemin_cache}")
        return charger_csv_avec_geometrie(chemin_cache)

    resultat = fonction_calcul()
    os.makedirs(os.path.dirname(chemin_cache), exist_ok=True)
    resultat.to_csv(chemin_cache, index=False)
    print(f"✓ Calculé et mis en cache : {chemin_cache}")
    return resultat


# Construction du réseau de transport multimodal, équivalent de setup_r5(data_path).
# En r5py, il n'y a pas de connexion séparée type r5r_core : l'objet TransportNetwork
# joue à la fois le rôle du réseau construit et du point d'entrée pour les calculs
# (ex: TravelTimeMatrixComputer, utilisé ensuite pour la matrice de temps de trajet).



def dir_tree(path, prefix=""):
    """Équivalent de fs::dir_tree(data_path) : affiche l'arborescence d'un dossier."""
    path = pathlib.Path(path)
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        print(prefix + connector + entry.name)
        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            dir_tree(entry, prefix + extension)




