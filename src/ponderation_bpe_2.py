"""
Recalcul de la pondération BPE à partir d'une grille de poids/seuils custom
(éditée en direct dans app_2.py, cf. views/accessibilite_urbaine_2.py) —
même logique que src.pipeline_donnees.ponderer_bpe et la boucle de seuils
"pôles" de construire_donnees_bpe (src/pipeline_donnees.py:139-159,
300-303), mais appliquée à un BPE_agglo/land_use_data déjà construits
(cf. views.accessibilite_index._construire_pipeline) plutôt que de relire
le parquet BPE (~160 Mo) ou de refaire le filtrage géographique
(filtre_BPE) : BPE_agglo contient déjà les colonnes domaine/GAMME (ajoutées
par ponderer_bpe avant sa propre fusion avec les poids), donc ne changer
que les poids ne coûte qu'une fusion pandas en mémoire.
"""

import pandas as pd

from src.BPE_traitement import land_use_data_domaine


def reponderer_bpe_2(BPE_agglo, land_use_data, gammes_poids_par_domaine, seuils_domaine):
    """Recalcule poids_gamme (BPE_agglo), equipements_pondere et
    pole_equipements_{domaine} (land_use_data) à partir d'une grille de
    poids/seuils custom. Ne modifie pas les DataFrames reçus (copies) —
    l'appelant doit toujours partir du BPE_agglo/land_use_data de base
    (pondération par défaut), jamais du résultat d'un appel précédent,
    pour ne pas cumuler les éditions successives.

    Returns
    -------
    (BPE_agglo, land_use_data) : mêmes colonnes que construire_donnees_bpe,
    recalculées avec gammes_poids_par_domaine/seuils_domaine.
    """
    table_poids = pd.DataFrame(
        [
            {"domaine": domaine, "GAMME": gamme, "poids_gamme": poids}
            for domaine, poids_par_gamme in gammes_poids_par_domaine.items()
            for gamme, poids in poids_par_gamme.items()
        ]
    )
    BPE_agglo = BPE_agglo.drop(columns=["poids_gamme"], errors="ignore").merge(
        table_poids, on=["domaine", "GAMME"], how="left"
    )

    land_use_data = land_use_data.copy()
    equipements_pondere_par_carreau = (
        BPE_agglo.dropna(subset=["id_carreau", "poids_gamme"])
        .groupby("id_carreau")["poids_gamme"]
        .sum()
    )
    land_use_data["equipements_pondere"] = (
        land_use_data["id"].map(equipements_pondere_par_carreau).fillna(0.0)
    )

    for domaine, seuil_pct in seuils_domaine.items():
        valeurs_domaine = land_use_data_domaine(BPE_agglo, land_use_data, domaine)
        seuil = seuil_pct * valeurs_domaine[domaine].mean()
        land_use_data[f"pole_equipements_{domaine}"] = (valeurs_domaine[domaine] > seuil).astype(int)

    return BPE_agglo, land_use_data
