"""
Recherche de jeux de données GTFS urbains sur le Point d'Accès National
(https://transport.data.gouv.fr), et suivi de leur provenance/fraîcheur par
rapport aux GTFS déjà présents sur le dataset HF antoinechevre/accessibility-data.

L'API du PAN (https://transport.data.gouv.fr/api/datasets) ne propose pas de
recherche par ville côté serveur : on récupère la liste complète des jeux de
données de type "public-transit" (un seul appel, mis en cache par l'appelant
— cf. st.cache_data côté app.py) puis on filtre côté client sur le titre et
les zones couvertes (covered_area).

Provenance : chaque téléchargement via ce module enregistre, dans un fichier
JSON partagé (gtfs_sources.json, même mécanisme de cache que le reste de
data/ — cf. src/hf_cache.py), la correspondance nom_fichier_gtfs -> dataset/
ressource source et sa date de mise à jour. Sert à la fois à afficher "déjà
à jour" dans la recherche, et de base pour un futur contrôle périodique
(cf. discussion "rafraîchissement mensuel").
"""

import csv
import io
import json
import os
import re
import unicodedata
import zipfile

import requests

from src.hf_cache import envoyer_vers_hf, recuperer_depuis_hf

API_DATASETS_URL = "https://transport.data.gouv.fr/api/datasets"

GTFS_SOURCES_LOCAL_PATH = os.path.join("data", "gtfs_sources.json")
GTFS_SOURCES_HF_PATH = "gtfs_sources.json"


def _sans_accents(texte):
    """Normalise pour une comparaison insensible aux accents/casse (ex:
    "Chateauroux" doit matcher "Châteauroux")."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", texte.lower()) if not unicodedata.combining(c)
    )


def recuperer_datasets_public_transit(timeout=30):
    """Récupère la liste brute (JSON) de tous les jeux de données de type
    "public-transit" du PAN. Un seul appel HTTP (~775 datasets début août
    2026, quelques Mo) — à l'appelant de mettre en cache (st.cache_data côté
    app.py) pour ne pas le refaire à chaque interaction.

    Lève requests.RequestException si le PAN est injoignable — laissé à
    l'appelant de décider comment l'afficher (pas de valeur de repli
    silencieuse : mieux vaut un message d'erreur clair qu'une recherche qui
    semble fonctionner mais ne renvoie jamais rien).
    """
    reponse = requests.get(API_DATASETS_URL, params={"type": "public-transit"}, timeout=timeout)
    reponse.raise_for_status()
    return reponse.json()


def _ressources_gtfs(dataset):
    """Toutes les ressources au format GTFS d'un dataset. Certains datasets
    ne couvrent qu'un seul réseau (une ressource, éventuellement plusieurs
    versions historiques — cf. TCL) ; d'autres agrègent plusieurs opérateurs
    d'une même métropole en un seul dataset PAN, une ressource par opérateur
    PLUS souvent une ressource "référentiel complet" qui les fusionne tous
    (ex: "Réseaux urbains de la Métropole Aix-Marseille-Provence" a une
    ressource "GTFS RTM" à côté d'un "Référentiel complet (tous les
    réseaux)" à 17 agences, que l'app ne peut pas charger — cf.
    TropAgencesError dans app.py). Choisir une seule ressource "la plus
    récente" comme avant reviendrait à piocher au hasard entre ces deux cas
    très différents ; les exposer toutes laisse l'appelant (recherche,
    vérification de fraîcheur) choisir la bonne."""
    return [r for r in dataset.get("resources", []) if (r.get("format") or "").upper() == "GTFS"]


def _resultat_depuis_ressource(dataset, ressource):
    """Construit le dict résultat (title, page_url, covered_area_noms,
    ressource_titre, ressource_url, ressource_maj) pour une ressource GTFS
    précise d'un dataset. Quand le dataset a plusieurs ressources GTFS
    (cf. _ressources_gtfs), le titre inclut celui de la ressource pour les
    distinguer dans la recherche (ex: "Réseaux urbains ... — GTFS RTM")."""
    noms_zones = [z.get("nom", "") for z in dataset.get("covered_area") or []]
    titre_dataset = dataset.get("title") or ""
    titre_ressource = ressource.get("title") or ""
    a_plusieurs_ressources = len(_ressources_gtfs(dataset)) > 1
    return {
        "title": f"{titre_dataset} — {titre_ressource}" if a_plusieurs_ressources else titre_dataset,
        "page_url": dataset.get("page_url"),
        "covered_area_noms": ", ".join(noms_zones[:3]) + ("…" if len(noms_zones) > 3 else ""),
        "ressource_titre": titre_ressource,
        "ressource_url": ressource.get("url"),
        "ressource_maj": ressource.get("updated"),
    }


def resultat_pour_page_url(page_url, ressource_url=None, datasets=None):
    """Retrouve, dans datasets (cf. recuperer_datasets_public_transit), le
    dataset dont page_url correspond exactement, et renvoie le résultat
    (même format que rechercher_gtfs_urbain) de sa ressource ressource_url —
    utilisé pour re-vérifier la fraîcheur d'un GTFS déjà associé (cf.
    scripts/rafraichir_gtfs.py), où provenance connaît déjà la ressource
    précise choisie au moment du téléchargement. Si ressource_url est None
    ou ne correspond à aucune ressource du dataset (ex: provenance créée
    avant l'ajout de ce paramètre), retombe sur la ressource GTFS la plus
    récente du dataset — comportement d'avant, gardé en repli uniquement.

    None si le dataset est introuvable (page supprimée/déplacée sur le PAN)
    ou sans ressource GTFS."""
    if datasets is None:
        datasets = recuperer_datasets_public_transit()
    for dataset in datasets:
        if dataset.get("page_url") != page_url:
            continue
        ressources = _ressources_gtfs(dataset)
        if not ressources:
            return None
        ressource = next((r for r in ressources if r.get("url") == ressource_url), None)
        if ressource is None:
            ressource = max(ressources, key=lambda r: r.get("updated") or "")
        return _resultat_depuis_ressource(dataset, ressource)
    return None


def rechercher_gtfs_urbain(nom_ville, datasets=None, limite=15):
    """Filtre les datasets (cf. recuperer_datasets_public_transit) dont le
    titre ou une zone couverte (covered_area) contient nom_ville, et qui ont
    au moins une ressource au format GTFS.

    datasets : liste déjà récupérée (évite un appel réseau si l'appelant l'a
    déjà en cache) ; sinon récupérée ici.

    Retourne une liste de dicts : title, page_url, covered_area_noms (str),
    ressource_titre, ressource_url, ressource_maj (date ISO ou None) — un
    élément par ressource GTFS (un dataset qui agrège plusieurs opérateurs,
    ex. une métropole, en fournit plusieurs — cf. _ressources_gtfs), triée
    par pertinence approximative (titre du dataset correspondant exactement
    d'abord, puis les ressources d'un même dataset par nombre d'agences
    croissant — une ressource par opérateur avant l'éventuel "référentiel
    complet" qui les fusionne tous et dépasse souvent la limite que l'app
    peut charger, cf. TropAgencesError dans app.py).
    """
    if datasets is None:
        datasets = recuperer_datasets_public_transit()

    cible = _sans_accents(nom_ville.strip())
    if not cible:
        return []

    resultats = []
    for dataset in datasets:
        titre = dataset.get("title") or ""
        covered_area = dataset.get("covered_area") or []
        noms_zones = [z.get("nom", "") for z in covered_area]

        correspond_titre = cible in _sans_accents(titre)
        correspond_zone = any(cible in _sans_accents(nom) for nom in noms_zones)
        if not (correspond_titre or correspond_zone):
            continue

        for ressource in _ressources_gtfs(dataset):
            resultats.append(_resultat_depuis_ressource(dataset, ressource))

    resultats.sort(
        key=lambda r: (
            0 if _sans_accents(r["title"]) == cible else 1,
            "complet" in _sans_accents(r["ressource_titre"] or ""),
        )
    )
    return resultats[:limite]


def charger_provenance():
    """Charge gtfs_sources.json (cache local, à défaut dataset HF) : dict
    nom_fichier_gtfs -> {page_url, ressource_url, ressource_maj, titre}.
    Dict vide si absent des deux côtés (première utilisation)."""
    if not os.path.exists(GTFS_SOURCES_LOCAL_PATH):
        recuperer_depuis_hf(GTFS_SOURCES_HF_PATH, GTFS_SOURCES_LOCAL_PATH)
    if not os.path.exists(GTFS_SOURCES_LOCAL_PATH):
        return {}
    with open(GTFS_SOURCES_LOCAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _provenance_hf_fraiche():
    """Retélécharge gtfs_sources.json depuis le dataset HF, en ignorant tout
    cache local existant. Contrairement à charger_provenance() (cache local
    prioritaire — adapté aux gros fichiers immuables de src/hf_cache.py),
    gtfs_sources.json est un petit index partagé modifié indépendamment par
    plusieurs écrivains (ce Mac en dev, le Space HF déployé qui enregistre sa
    propre provenance à chaque nouveau GTFS chargé par un visiteur) : se fier
    à un cache local pour la fusion avant écriture perdrait silencieusement
    les entrées ajoutées entre-temps par l'autre côté."""
    try:
        from huggingface_hub import hf_hub_download
        chemin = hf_hub_download(
            repo_id="antoinechevre/accessibility-data",
            repo_type="dataset",
            filename=GTFS_SOURCES_HF_PATH,
            token=os.environ.get("HF_TOKEN"),
            force_download=True,
        )
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # HF injoignable, jamais encore poussé, etc. : repli sur le cache
        # local plutôt que de bloquer l'enregistrement.
        return charger_provenance()


def enregistrer_zone(nom_fichier_gtfs, code_departement, academie, zone):
    """Enregistre/actualise, pour nom_fichier_gtfs, l'académie/zone de
    vacances scolaires déterminée à partir de la position de ses arrêts (cf.
    src/vacances_scolaires.departement_academie_zone_pour_feed) — même
    fichier partagé gtfs_sources.json que enregistrer_provenance, fusionné
    avec l'entrée existante plutôt que de l'écraser (un GTFS téléchargé via
    la recherche a déjà une entrée avec page_url/ressource_url ; un GTFS
    seulement uploadé n'en a pas encore, l'entrée est alors créée avec
    uniquement ces trois champs).

    Ne fait rien si academie est None (feed hors métropole ou API
    injoignable, cf. departement_academie_zone_pour_feed) — inutile de créer
    une entrée sans information exploitable."""
    if academie is None:
        return
    provenance = _provenance_hf_fraiche()
    entree = provenance.get(nom_fichier_gtfs, {})
    entree.update({"code_departement": code_departement, "academie": academie, "zone": zone})
    provenance[nom_fichier_gtfs] = entree
    os.makedirs(os.path.dirname(GTFS_SOURCES_LOCAL_PATH), exist_ok=True)
    with open(GTFS_SOURCES_LOCAL_PATH, "w", encoding="utf-8") as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2, sort_keys=True)
    envoyer_vers_hf(GTFS_SOURCES_LOCAL_PATH, GTFS_SOURCES_HF_PATH)


def enregistrer_provenance(nom_fichier_gtfs, dataset_resultat):
    """Enregistre/actualise, pour nom_fichier_gtfs, la provenance issue d'un
    résultat de rechercher_gtfs_urbain (page_url/ressource_url/ressource_maj/
    title) — fusionné avec l'état HF le plus récent (cf. _provenance_hf_fraiche)
    ET avec l'entrée existante (garde ses champs académie/zone déjà
    enregistrés par enregistrer_zone, s'il y en a), local + dataset HF
    (best-effort, comme les autres écritures de src/hf_cache.py)."""
    provenance = _provenance_hf_fraiche()
    entree = provenance.get(nom_fichier_gtfs, {})
    entree.update({
        "page_url": dataset_resultat["page_url"],
        "ressource_url": dataset_resultat["ressource_url"],
        "ressource_maj": dataset_resultat["ressource_maj"],
        "titre": dataset_resultat["title"],
    })
    provenance[nom_fichier_gtfs] = entree
    os.makedirs(os.path.dirname(GTFS_SOURCES_LOCAL_PATH), exist_ok=True)
    with open(GTFS_SOURCES_LOCAL_PATH, "w", encoding="utf-8") as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2, sort_keys=True)
    envoyer_vers_hf(GTFS_SOURCES_LOCAL_PATH, GTFS_SOURCES_HF_PATH)


def statut_resultat(dataset_resultat, provenance):
    """Compare dataset_resultat (issu de rechercher_gtfs_urbain) à la
    provenance enregistrée (cf. charger_provenance) : renvoie un tuple
    (statut, nom_fichier_local_ou_None).

    statut : "a_jour" (une entrée de provenance pointe vers cette même
    ressource, à la même date de màj ou plus récente — ne devrait pas
    arriver en pratique, mais couvre le cas d'une horloge source qui recule),
    "maj_disponible" (une entrée de provenance pointe vers cette même
    ressource mais à une date de màj antérieure), ou "nouveau" (aucune
    entrée de provenance ne référence cette ressource)."""
    for nom_fichier, info in provenance.items():
        if info.get("ressource_url") != dataset_resultat["ressource_url"]:
            continue
        maj_connue = info.get("ressource_maj") or ""
        maj_source = dataset_resultat.get("ressource_maj") or ""
        if maj_connue >= maj_source:
            return "a_jour", nom_fichier
        return "maj_disponible", nom_fichier
    return "nouveau", None


def telecharger_gtfs(dataset_resultat, timeout=60):
    """Télécharge le contenu (bytes) de la ressource GTFS d'un résultat de
    rechercher_gtfs_urbain. Ne l'enregistre pas sur disque — à l'appelant de
    choisir le nom de fichier final et d'appeler enregistrer_provenance."""
    reponse = requests.get(dataset_resultat["ressource_url"], timeout=timeout)
    reponse.raise_for_status()
    return reponse.content


def nb_agences_gtfs(contenu_zip):
    """Nombre d'agences (lignes d'agency.txt) d'un GTFS téléchargé (bytes).
    Ne lit que agency.txt (pas gtfs_kit.read_feed, coûteux) — juste assez
    pour appliquer le même garde-fou "max 4 agences" que app.py
    (TropAgencesError) avant d'enregistrer un GTFS trouvé via la recherche :
    une recherche par nom de ville peut remonter un jeu de données régional
    agrégeant de nombreux opérateurs (ex: "Marseille" -> GTFS métropolitain
    Aix-Marseille, 17 agences) que l'app ne peut de toute façon pas charger."""
    with zipfile.ZipFile(io.BytesIO(contenu_zip)) as archive:
        with archive.open("agency.txt") as f:
            lignes = list(csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    return max(len(lignes) - 1, 0)


def noms_agences_gtfs(contenu_zip):
    """Ensemble des noms d'agence (normalisés — cf. _sans_accents) d'un GTFS
    téléchargé (bytes). Sert de "signature" de contenu pour rapprocher un
    GTFS local d'un jeu de données PAN (cf. associer_gtfs_a_pan) — plus
    fiable qu'un nom de fichier, potentiellement renommé depuis son
    téléchargement d'origine."""
    with zipfile.ZipFile(io.BytesIO(contenu_zip)) as archive:
        with archive.open("agency.txt") as f:
            lignes = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    return {_sans_accents(l["agency_name"].strip()) for l in lignes if l.get("agency_name")}


# Corrections pour quelques noms de fichiers dont le préfixe ne suffit pas à
# retrouver la ville sur le PAN (typo, ville composée coupée par un "_"...).
# À compléter au besoin plutôt qu'à essayer de deviner algorithmiquement.
CORRECTIONS_TERME_RECHERCHE = {
    "Auxerres_gtfs-mdma.zip": "Auxerre",
    "SaintEtienne_STAS.GTFS.zip": "Saint-Étienne",
}


def associer_gtfs_a_pan(nom_fichier, contenu_local, datasets, max_candidats=8):
    """Cherche, parmi les résultats PAN pour un terme dérivé de nom_fichier
    (cf. CORRECTIONS_TERME_RECHERCHE), le premier (dans l'ordre déjà trié
    par pertinence par rechercher_gtfs_urbain, qui déprioritise les
    ressources "référentiel complet") dont les agences (agency.txt)
    recoupent celles de contenu_local — télécharge chaque candidat jusqu'à
    trouver une correspondance ou épuiser max_candidats.

    Le nom de fichier n'est qu'un point de départ pour la recherche : la
    confirmation se fait sur le contenu réel (agences en commun), jamais sur
    le seul nom, potentiellement renommé depuis son téléchargement d'origine
    — utilisé aussi bien pour indexer un GTFS déjà présent (cf.
    scripts/indexer_gtfs_locaux.py) que pour associer automatiquement un
    nouvel upload dans l'app (cf. app.py).

    Retourne (resultat, None) en cas de succès. En cas d'échec, (None,
    motif) avec motif = "trop_agences" (candidat trouvé — agences en
    commun — mais dépasse le garde-fou "max 4 agences", cf. nb_agences_gtfs
    ; mieux vaut ne rien associer qu'associer à tort un jeu de données
    régional agrégé) ou "aucune_correspondance" (aucun candidat, parmi les
    max_candidats essayés, ne partage d'agence avec le GTFS local)."""
    agences_locales = noms_agences_gtfs(contenu_local)
    terme = CORRECTIONS_TERME_RECHERCHE.get(nom_fichier) or re.split(r"[_\-.]", nom_fichier, maxsplit=1)[0]
    resultats = rechercher_gtfs_urbain(terme, datasets=datasets)
    for resultat in resultats[:max_candidats]:
        try:
            contenu_candidat = telecharger_gtfs(resultat)
        except requests.RequestException:
            continue
        if not (agences_locales & noms_agences_gtfs(contenu_candidat)):
            continue
        nb_agences = nb_agences_gtfs(contenu_candidat)
        if nb_agences > 4:
            return None, "trop_agences"
        return resultat, None
    return None, "aucune_correspondance"
