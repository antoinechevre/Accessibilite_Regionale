"""
Page Streamlit : cartes de pondération des équipements BPE par domaine, à
l'échelle régionale — équivalent des cellules "analyse BPE 1.1/1.2" du
notebook_region_accessibilite.ipynb. Ne nécessite pas r5py/JVM (pas de temps
de trajet ici, juste l'offre d'équipements par carreau).
"""

import folium
import pandas as pd
import streamlit as st

from src.BPE_traitement import carte_ponderation_domaine, land_use_data_domaine
from src.cartographie import titre_carte_html
from src.pipeline_donnees_region import DOMAINES_BPE

FONDS_CARTE = {
    "CartoDB Positron": "CartoDB positron",
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Dark Matter": "CartoDB dark_matter",
}


def afficher(donnees):
    """donnees : dict retourné par construire_donnees_region (cf. app.py,
    qui l'appelle une seule fois via st.cache_data et le passe à chaque page
    plutôt que de le reconstruire ici à chaque interaction)."""
    st.header("Pondération des équipements par domaine")
    st.caption(
        "Offre d'équipements (BPE INSEE), pondérée par gamme, agrégée par carreau de "
        "population 1km — indépendant du réseau de transport (pas de calcul r5py ici)."
    )

    nom_region_str = donnees["nom_region_str"]
    population_grid_region = donnees["population_grid_region"]
    BPE_region = donnees["BPE_region"]
    land_use_data = donnees["land_use_data"]

    st.success(f"Région : **{nom_region_str}** — {len(population_grid_region)} carreaux actifs")

    col1, col2 = st.columns([2, 1])
    with col1:
        domaine = st.selectbox(
            "Domaine BPE",
            options=list(DOMAINES_BPE.keys()),
            format_func=lambda d: DOMAINES_BPE[d],
            index=0,
        )
    with col2:
        fond_carte = st.selectbox("Fond de carte", options=list(FONDS_CARTE.keys()), index=0)

    carte = carte_ponderation_domaine(
        DOMAINES_BPE, population_grid_region, BPE_region, land_use_data, domaine, tiles=FONDS_CARTE[fond_carte]
    )
    carte.get_root().html.add_child(
        folium.Element(titre_carte_html(f"Pondération {DOMAINES_BPE[domaine]} – {nom_region_str}"))
    )
    st.iframe(carte._repr_html_(), height=650)

    st.subheader("Pondération totale par domaine")
    tableau = {
        "Domaine": [],
        "Pondération totale (carreaux actifs)": [],
        "Pondération carreaux > seuil": [],
    }
    for d, nom in DOMAINES_BPE.items():
        valeurs = land_use_data_domaine(BPE_region, land_use_data, d)
        tableau["Domaine"].append(nom)
        tableau["Pondération totale (carreaux actifs)"].append(float(valeurs[d].sum()))
        tableau["Pondération carreaux > seuil"].append(
            float(valeurs.loc[land_use_data[f"pole_equipements_{d}"] == 1, d].sum())
        )

    st.dataframe(pd.DataFrame(tableau).set_index("Domaine"), width="stretch")
