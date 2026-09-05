#!/usr/bin/env python3
"""
Exécute le pipeline complet d'accessibilité régionale (cf.
notebook_region_accessibilite.ipynb / src/pipeline_donnees_region.py) pour
UN SEUL GTFS régional, passé en argument.

Prévu pour être lancé en sous-processus par scripts/run_toutes_regions.py
(un process Python par région) plutôt qu'importé/bouclé dans un seul
process : la JVM r5py démarre une fois par process et grossit à chaque
région traitée (nouveau TransportNetwork + ttm), donc traiter N régions
dans le même process risquerait d'accumuler la mémoire au fil du batch —
même classe de problème que les kernels morts rencontrés en local sur une
seule région (cf. commentaires de la cellule 0 du notebook). Un
sous-process par région repart d'une JVM neuve à chaque fois, et une région
qui plante n'affecte pas les suivantes.

Usage :
    python3 scripts/run_region.py data/GTFS_Region/Bretagne_KORRIGOBRET.gtfs.zip
"""

import argparse
import datetime
import os
import sys
import time

# Autorise `from src...` quel que soit le répertoire courant depuis lequel ce
# script est lancé : exécuter un .py (contrairement à `python3 -c`, ou à
# lancer depuis la racine avec `python3 -m`) place le dossier du SCRIPT
# (scripts/) en tête de sys.path, pas la racine du projet ni le cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    # Args du SCRIPT parsés avec le sys.argv d'origine, AVANT tout import
    # touchant r5py — cf. bloc --max-memory ci-dessous, qui réécrit sys.argv
    # pour r5py juste après (le remplacer plus tôt casserait argparse ici :
    # --help/gtfs_path/--cutoffs seraient perdus, "unrecognized arguments").
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gtfs_path", help="Chemin vers un GTFS régional (.zip)")
    parser.add_argument(
        "--cutoffs",
        type=int,
        nargs="+",
        default=[60, 90],
        help="Seuils d'accès en minutes (défaut : 60 90)",
    )
    args = parser.parse_args()

    # DOIT être fait AVANT le premier import de r5py (cf. cellule 0 du
    # notebook et src/pipeline_donnees_region.py) : --max-memory est le seul
    # levier qui fonctionne réellement pour limiter le heap JVM. r5py lit
    # cette valeur via configargparse.parse_known_args() (tolère les
    # arguments qu'il ne reconnaît pas), donc sys.argv peut être remplacé
    # sans risque ici — le parsing du script lui-même est déjà fait.
    max_memory = os.environ.get("R5PY_MAX_MEMORY", "8G")
    sys.argv = [sys.argv[0], "--max-memory", max_memory]

    import r5py  # noqa: F401  (déclenche le démarrage de la JVM avec --max-memory déjà fixé)
    from src.info_reseau import dates_service
    from src.pipeline_donnees_region import (
        DOMAINES_BPE,
        calculer_index_accessibilite_region,
        calculer_ttm_region,
        chemins_region,
        construire_donnees_region,
        construire_reseau_region,
    )
    from src.utils import charger_gtfs, exporter_df_to_csv
    from src.BPE_traitement import carte_ponderation_domaine
    from src.cartographie import titre_carte_html
    from src.hf_cache import HF_DATA_REPO_ID_REGION, envoyer_vers_hf, recuperer_depuis_hf
    import folium

    max_time_minutes = max(args.cutoffs)

    def _step(message):
        print(f"[{datetime.datetime.now():%H:%M:%S}] {message}", flush=True)

    def _ttm_cache_est_recent(nom_region_str, cache_max_age_jours=10):
        """Vrai si un ttm valide (< cache_max_age_jours) est déjà disponible
        (local ou HF) pour cette région — cf. calculer_ttm_region, même
        logique de cache dupliquée ici pour savoir AVANT de construire le
        réseau si celui-ci sera réellement nécessaire."""
        ttm_path = chemins_region(nom_region_str)["ttm"]
        recuperer_depuis_hf(
            f"memory_ttm/ttm_region_{nom_region_str}.parquet", ttm_path, repo_id=HF_DATA_REPO_ID_REGION
        )
        return (
            os.path.exists(ttm_path)
            and (time.time() - os.path.getmtime(ttm_path)) < cache_max_age_jours * 24 * 3600
        )

    _step(f"=== Région : {args.gtfs_path} ===")

    donnees = construire_donnees_region(args.gtfs_path, on_step=_step)
    nom_region_str = donnees["nom_region_str"]
    population_grid_region = donnees["population_grid_region"]
    land_use_data = donnees["land_use_data"]
    BPE_region = donnees["BPE_region"]

    output_path_reseau = os.path.join("output", nom_region_str)
    os.makedirs(output_path_reseau, exist_ok=True)

    # --- Cartes de pondération des équipements par domaine (HTML), cf.
    # cellule "analyse BPE 1.2" du notebook — export local + cache HF.
    for d, nom_domaine in DOMAINES_BPE.items():
        _step(f"Carte de pondération : {nom_domaine}")
        carte = carte_ponderation_domaine(
            DOMAINES_BPE, population_grid_region, BPE_region, land_use_data, d, tiles="CartoDB positron"
        )
        carte.get_root().html.add_child(
            folium.Element(titre_carte_html(f"Pondération {nom_domaine} – {nom_region_str}"))
        )
        nom_fichier = f"ponderation_{d}_{nom_region_str}.html"
        chemin_carte = os.path.join(output_path_reseau, nom_fichier)
        carte.save(chemin_carte)
        envoyer_vers_hf(chemin_carte, f"output/{nom_region_str}/{nom_fichier}", repo_id=HF_DATA_REPO_ID_REGION)

    # --- Réseau de transport + matrice des temps de trajet -----------------
    # transport_network=None tant que le cache ttm n'a pas été vérifié : si
    # calculer_ttm_region trouve un ttm valide en cache (local ou HF, < 10
    # jours), il le recharge directement sans jamais utiliser
    # transport_network — inutile de construire le réseau (extrait OSM +
    # JVM r5py, l'étape la plus lourde et la plus fragile de tout le
    # pipeline, cf. cellule "Construction du réseau" du notebook) dans ce cas.
    feed = charger_gtfs(args.gtfs_path)
    _, _, _, date_job = dates_service(feed)

    if _ttm_cache_est_recent(nom_region_str):
        _step("ttm déjà en cache local (< 10 jours) — réseau non reconstruit")
        transport_network = None
    else:
        transport_network = construire_reseau_region(
            args.gtfs_path, nom_region_str, allow_errors=True, on_step=_step
        )

    # calculer_ttm_region retourne le CHEMIN du ttm (pas un DataFrame chargé)
    # — cf. sa docstring : calculer_index_accessibilite_region lit ce fichier
    # par lots plutôt que de charger tout le ttm en mémoire (déjà fait
    # exploser la RAM à cette échelle, cf. calculer_index_benchmark_par_lots).
    ttm_path = calculer_ttm_region(
        transport_network,
        population_grid_region,
        nom_region_str,
        date_job,
        max_time_minutes=max_time_minutes,
        on_step=_step,
    )

    # --- Indicateurs d'accessibilité (60/90 min par défaut) -----------------
    _step("Calcul des indicateurs d'accessibilité...")
    index_accessibilite = calculer_index_accessibilite_region(
        BPE_region, land_use_data, ttm_path, population_grid_region, cutoffs=tuple(args.cutoffs), on_step=_step
    )

    chemin_index = os.path.join(output_path_reseau, f"index_accessibilite_{nom_region_str}.csv")
    exporter_df_to_csv(index_accessibilite, chemin_index)
    envoyer_vers_hf(
        chemin_index, f"output/{nom_region_str}/index_accessibilite_{nom_region_str}.csv",
        repo_id=HF_DATA_REPO_ID_REGION,
    )

    _step(f"✓ Région {nom_region_str} terminée — résultats dans {output_path_reseau}")


if __name__ == "__main__":
    main()
