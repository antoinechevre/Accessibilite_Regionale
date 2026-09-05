"""
Pondérations et seuils utilisés pour scorer la Base Permanente des Équipements
(BPE, INSEE) par domaine (cf. src/pipeline_donnees.py::ponderer_bpe et
notebook index_accessibility_notebook_def.ipynb, cellule "analyse BPE 1.1").
"""

# Poids par gamme (proximité/intermédiaire/supérieure/hors gamme), spécifique
# à chaque grand domaine BPE (A-G, première lettre de TYPEQU) plutôt qu'une
# seule grille commune : on peut ainsi valoriser une même gamme différemment
# selon le domaine (ex: "Hors Gamme" pèse 3 en A mais 8 ailleurs).
# Proposition de pondération par gammes cf. BPE_gammes_equipements_2025.xlsx
# (dossier data).
GAMMES_POIDS_PAR_DOMAINE = {
    "A": {"Gamme de proximité": 2, "Gamme intermédiaire": 3, "Gamme supérieure": 4, "Hors Gamme": 3},
    "B": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "C": {"Gamme de proximité": 4, "Gamme intermédiaire": 6, "Gamme supérieure": 8, "Hors Gamme": 10},
    "D": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "E": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "F": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "G": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
}

# Seuil (en multiple de la moyenne du domaine) au-delà duquel un carreau est
# considéré comme un "pôle d'équipements" pour ce domaine (cf. notebook
# "analyse BPE 1.1" et sections 9.1/9.2). Utilisé par les cartes "temps d'accès
# au pôle le plus proche" et "pôles accessibles" de views/accessibilite_index.py.
SEUILS_DOMAINE = {
    "A": 1,
    "B": 1,
    "C": 1,
    "D": 1,
    "E": 1,
    "F": 1,
    "G": 1,
    "O": 1.5,
}
