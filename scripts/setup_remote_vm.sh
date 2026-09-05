#!/usr/bin/env bash
set -euo pipefail

# Prépare une VM Ubuntu 24.04 fraîche (ex. Hetzner Cloud) pour faire tourner
# notebook_region_accessibilite.ipynb avec plus de RAM que localement (JVM
# r5py + traitement OSM/GTFS à l'échelle d'une région entière plutôt qu'une
# agglo, cf. les kernels morts en boucle du 20/08 sur une machine 16 Go).
#
# À exécuter UNE FOIS sur la VM, dans le dossier du projet (déjà transféré
# via rsync, cf. instructions de connexion) — depuis un terminal SSH classique
# ou celui intégré à une fenêtre VS Code connectée en Remote-SSH.

sudo apt-get update
sudo apt-get install -y \
    osmium-tool \
    default-jdk-headless \
    build-essential

# Python 3.12 via Miniforge (conda-forge) plutôt que le python3 système :
# sur une image Ubuntu récente (26.04+), le python3 système est déjà en
# 3.14+, trop récent pour la plupart des wheels dont dépend ce projet
# (r5py, jpype1, geopandas...) qui n'ont pas encore de build cp314 — d'où
# l'environnement dédié figé sur 3.12 (version utilisée en développement,
# cf. requirements.txt), quelle que soit la version du python3 système.
if [ ! -d "$HOME/miniforge3" ]; then
    curl -fsSL -o /tmp/Miniforge3.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
    bash /tmp/Miniforge3.sh -b -p "$HOME/miniforge3"
fi
"$HOME/miniforge3/bin/conda" create -y -n accessibilite python=3.12

# Swap de sécurité : absent par défaut sur une image Hetzner fraîche (0B
# constaté). Sans lui, un pic mémoire du process JVM r5py (déjà observé en
# local, cf. commentaire ci-dessus) fait tuer le process net par l'OOM
# killer plutôt que de dégrader progressivement — 8 Go, à ajuster si la VM a
# moins de RAM que prévu (cf. `free -h`).
if [ "$(swapon --show | wc -l)" -eq 0 ]; then
    sudo fallocate -l 8G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "✓ Swap de 8G créé et activé"
else
    echo "Swap déjà actif, pas de changement"
fi

source "$HOME/miniforge3/bin/activate" accessibilite

pip install --upgrade pip
pip install -r requirements.txt
# Kernel Jupyter pour que VS Code (Remote-SSH) propose cet environnement
# comme kernel du notebook :
pip install ipykernel
python -m ipykernel install --user --name accessibilite --display-name "Accessibilité (venv distant)"

echo
echo "✓ Environnement prêt."
echo "  - Active-le dans un terminal :  source ~/miniforge3/bin/activate accessibilite"
echo "  - Dans VS Code (fenêtre Remote-SSH) : ouvre le notebook, puis en haut à"
echo "    droite \"Select Kernel\" -> \"Accessibilité (venv distant)\"."
echo "  - Pense à définir HF_TOKEN si tu veux profiter du cache Hugging Face"
echo "    partagé (cf. src/hf_cache.py) :  export HF_TOKEN=hf_xxx"
