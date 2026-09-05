"""
Force un nom d'agence unique sur le GTFS agrégé de la Métropole
Aix-Marseille-Provence (MAMP).

data/GTFS_agrégé/Metropole_Aix_Marseille_mamp.gtfs 2.zip agrège les feeds
de 17 opérateurs distincts (Aix lebus, RTM/Marseille, SNCF/TER, Aubagne,
La Ciotat...), chacun avec son propre agency_id/agency_name dans
agency.txt. src.info_reseau.nom_reseau() concatène tous les agency_name
distincts d'un feed avec " / " pour déterminer le nom du réseau — utilisé
partout comme clé de cache (memory_gpkg/memory_ttm/... cf. src/hf_cache.py)
et comme identifiant dans le benchmark inter-réseaux. Avec 17 agences,
cette concaténation produirait un nom de réseau ingérable.

Ce script réécrit agency_name pour toutes les lignes d'agency.txt avec un
seul nom (NOM_RESEAU_CIBLE ci-dessous, à ajuster si besoin), sans toucher à
agency_id (donc sans casser les références routes.agency_id) ni à aucune
autre table, puis écrit le GTFS corrigé dans data/GTFS/ pour qu'il soit
pris en compte par le pipeline normal (scripts/run_benchmark_batch.py
scanne data/GTFS/*.zip).

Le fichier source n'est jamais modifié en place : comme dans
scripts/run_benchmark_batch.py, il est d'abord copié dans un fichier
temporaire avant chargement, car charger_gtfs() peut réécrire un zip en
place (aplatissement de sous-dossier, tables vides...).
"""

import os
import shutil
import tempfile
from pathlib import Path

from src.utils import charger_gtfs

BASE_DIR = Path(__file__).resolve().parent.parent

GTFS_ZIP_PATH_MAMP = os.path.join(
    BASE_DIR, "data", "GTFS_agrégé", "Metropole_Aix_Marseille_mamp.gtfs 2.zip"
)
GTFS_ZIP_PATH_SORTIE = os.path.join(
    BASE_DIR, "data", "GTFS", "Metropole_Aix_Marseille_mamp.gtfs.zip"
)

# Nom d'agence forcé sur tout le feed — ajuster ici si un autre libellé est
# préférable (ex. "MAMP" tout court, ou le nom exact souhaité pour le
# benchmark inter-réseaux).
NOM_RESEAU_CIBLE = "Métropole Aix-Marseille-Provence"


def forcer_nom_agence_unique(chemin_zip_entree, chemin_zip_sortie, nom_agence=NOM_RESEAU_CIBLE):
    """
    Force agency_name à nom_agence pour toutes les lignes d'agency.txt du
    GTFS chemin_zip_entree, puis écrit le résultat dans chemin_zip_sortie.

    agency_id (clé référencée par routes.agency_id) n'est pas modifié :
    seul le libellé change, pas les identifiants ni aucune autre table.

    Parameters
    ----------
    chemin_zip_entree : str
        Chemin du GTFS agrégé source (plusieurs agences dans agency.txt).
    chemin_zip_sortie : str
        Chemin du GTFS corrigé à écrire (une seule agence).
    nom_agence : str
        Nom d'agence unique à appliquer à toutes les lignes.

    Returns
    -------
    gtfs_kit Feed object
        Le feed corrigé (agency_name déjà réécrit).
    """
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        chemin_tmp = tmp.name
    shutil.copy(chemin_zip_entree, chemin_tmp)

    try:
        print(f"Chargement de {os.path.basename(chemin_zip_entree)}...")
        feed = charger_gtfs(chemin_tmp)

        noms_origine = feed.agency["agency_name"].dropna().unique()
        print(f"  {len(noms_origine)} agence(s) d'origine : {', '.join(noms_origine)}")

        feed.agency["agency_name"] = nom_agence
        print(f"  → agency_name forcé à '{nom_agence}' pour {len(feed.agency)} ligne(s) d'agency.txt")

        os.makedirs(os.path.dirname(chemin_zip_sortie), exist_ok=True)
        feed.to_file(chemin_zip_sortie)
        print(f"✓ GTFS avec agence unique enregistré dans : {chemin_zip_sortie}")
    finally:
        os.unlink(chemin_tmp)

    return feed


if __name__ == "__main__":
    forcer_nom_agence_unique(GTFS_ZIP_PATH_MAMP, GTFS_ZIP_PATH_SORTIE)
