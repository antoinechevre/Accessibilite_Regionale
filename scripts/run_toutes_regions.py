#!/usr/bin/env python3
"""
Lance le pipeline d'accessibilité régionale (scripts/run_region.py) pour
CHAQUE GTFS trouvé dans data/GTFS_Region/*.zip — équivalent, en mode batch
non interactif, de rejouer notebook_region_accessibilite.ipynb une fois par
région (Bretagne, et toute autre région dont tu déposes le GTFS régional
dans ce dossier).

Un sous-process par région (cf. run_region.py pour le détail) : une région
qui plante (mémoire, GTFS malformé...) n'empêche pas les suivantes de
tourner, et chaque région repart d'une JVM r5py neuve plutôt que d'accumuler
la mémoire des régions précédentes dans un seul long-running process.

Usage :
    python3 scripts/run_toutes_regions.py
    python3 scripts/run_toutes_regions.py --cutoffs 30 60 90
    python3 scripts/run_toutes_regions.py --max-memory 12G
"""

import argparse
import datetime
import os
import subprocess
import sys

GTFS_DIR = os.path.join("data", "GTFS_Region")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--cutoffs", type=int, nargs="+", default=[60, 90], help="Seuils d'accès en minutes (défaut : 60 90)"
    )
    parser.add_argument(
        "--max-memory",
        default=os.environ.get("R5PY_MAX_MEMORY", "8G"),
        help='Heap JVM par région, ex. "8G" ou "12G" (défaut : 8G, ou $R5PY_MAX_MEMORY)',
    )
    args = parser.parse_args()

    if not os.path.isdir(GTFS_DIR):
        sys.exit(f"Dossier introuvable : {GTFS_DIR}")

    gtfs_files = sorted(f for f in os.listdir(GTFS_DIR) if f.endswith(".zip"))
    if not gtfs_files:
        sys.exit(f"Aucun GTFS (.zip) trouvé dans {GTFS_DIR}")

    print(f"{len(gtfs_files)} GTFS régional(aux) trouvé(s) : {', '.join(gtfs_files)}")

    resultats = []
    for i, nom_fichier in enumerate(gtfs_files, start=1):
        gtfs_path = os.path.join(GTFS_DIR, nom_fichier)
        debut = datetime.datetime.now()
        print(f"\n{'=' * 70}\n[{i}/{len(gtfs_files)}] {nom_fichier} — début {debut:%H:%M:%S}\n{'=' * 70}")

        env = dict(os.environ, R5PY_MAX_MEMORY=args.max_memory)
        commande = [
            sys.executable,
            os.path.join("scripts", "run_region.py"),
            gtfs_path,
            "--cutoffs",
            *[str(c) for c in args.cutoffs],
        ]
        processus = subprocess.run(commande, env=env)
        duree = datetime.datetime.now() - debut

        if processus.returncode == 0:
            print(f"✓ {nom_fichier} terminé en {duree}")
            resultats.append((nom_fichier, "OK", duree))
        else:
            print(f"✗ {nom_fichier} a échoué (code {processus.returncode}) après {duree}")
            resultats.append((nom_fichier, f"ÉCHEC (code {processus.returncode})", duree))

    print(f"\n{'=' * 70}\nRésumé\n{'=' * 70}")
    for nom_fichier, statut, duree in resultats:
        print(f"{statut:<20} {nom_fichier}  ({duree})")

    if any(statut != "OK" for _, statut, _ in resultats):
        sys.exit(1)


if __name__ == "__main__":
    main()
