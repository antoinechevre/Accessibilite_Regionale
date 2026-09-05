"""
Page Streamlit : réseau de transport multimodal (r5py) et indicateurs
d'accessibilité régionale (% d'équipements accessibles en <= 60/90 min) —
équivalent des cellules "Construction du réseau", "ttm" et "Analyse
accessibilité régionale" du notebook_region_accessibilite.ipynb.

Étape volontairement déclenchée par un bouton plutôt qu'automatiquement à
l'affichage de la page : construire le réseau + la matrice de temps de
trajet sur une région entière est lourd (JVM r5py, plusieurs minutes,
cf. les kernels morts rencontrés en local sur ce même calcul) — pas quelque
chose à relancer par accident à chaque rerun Streamlit.
"""

import streamlit as st

from src.info_reseau import dates_service
from src.pipeline_donnees_region import (
    CUTOFFS_MINUTES_DEFAUT,
    calculer_index_accessibilite_region,
    calculer_ttm_region,
    construire_reseau_region,
)
from src.utils import charger_gtfs


@st.cache_data(show_spinner=False)
def _date_job(gtfs_path):
    feed = charger_gtfs(gtfs_path)
    _, _, _, date_job = dates_service(feed)
    return date_job


@st.cache_resource(show_spinner=False)
def _reseau_region(gtfs_path, nom_region_str):
    return construire_reseau_region(gtfs_path, nom_region_str, on_step=st.write)


@st.cache_data(show_spinner=False)
def _ttm_region(_transport_network, _population_grid_region, nom_region_str, date_job, max_time_minutes, cache_key):
    # cache_key (gtfs_path) : force un nouveau calcul si le GTFS change, même
    # si nom_region_str est identique (deux GTFS différents pour la même
    # région) — _transport_network/_population_grid_region préfixés par "_"
    # pour que st.cache_data ne tente pas de les hasher (objets non
    # sérialisables/lourds), seul cache_key sert de clé. Retourne un chemin
    # (str), pas le ttm chargé — cf. calculer_ttm_region.
    return calculer_ttm_region(
        _transport_network,
        _population_grid_region,
        nom_region_str,
        date_job,
        max_time_minutes=max_time_minutes,
        on_step=st.write,
    )


def afficher(donnees, gtfs_path):
    """donnees : dict retourné par construire_donnees_region (cf. app.py)."""
    st.header("Accessibilité régionale en transport en commun")
    st.caption(
        "Réseau multimodal r5py (marche + transport en commun) et % d'équipements "
        "accessibles en <= 60 min et <= 90 min, pondéré par la population d'origine."
    )

    nom_region_str = donnees["nom_region_str"]
    population_grid_region = donnees["population_grid_region"]
    land_use_data = donnees["land_use_data"]
    BPE_region = donnees["BPE_region"]

    cutoffs = st.multiselect(
        "Seuils d'accès (minutes)",
        options=[30, 45, 60, 90, 120],
        default=list(CUTOFFS_MINUTES_DEFAUT),
    )
    cutoffs = tuple(sorted(cutoffs)) or CUTOFFS_MINUTES_DEFAUT
    max_time_minutes = max(cutoffs)

    lancer = st.button("Lancer le calcul réseau + accessibilité", type="primary")
    if not lancer and "ttm_region_path" not in st.session_state:
        st.info(
            "Calcul non lancé (construction du réseau + matrice des temps de trajet sur "
            "toute la région : plusieurs minutes). Clique sur le bouton pour démarrer."
        )
        return

    if lancer:
        date_job = _date_job(gtfs_path)
        with st.spinner(f"Construction du réseau de {nom_region_str}..."):
            transport_network = _reseau_region(gtfs_path, nom_region_str)
        with st.spinner("Calcul de la matrice des temps de trajet..."):
            ttm_path = _ttm_region(
                transport_network, population_grid_region, nom_region_str, date_job, max_time_minutes, gtfs_path
            )
        st.session_state["ttm_region_path"] = ttm_path
        st.session_state["ttm_region_key"] = (nom_region_str, gtfs_path, max_time_minutes)

    ttm_path = st.session_state.get("ttm_region_path")
    cle_actuelle = (nom_region_str, gtfs_path, max_time_minutes)
    if ttm_path is None:
        return
    if st.session_state.get("ttm_region_key") != cle_actuelle:
        st.warning(
            "Les seuils/région/GTFS ont changé depuis le dernier calcul — relance le calcul "
            "pour des résultats à jour."
        )

    with st.spinner("Calcul des indicateurs d'accessibilité..."):
        index_accessibilite = calculer_index_accessibilite_region(
            BPE_region, land_use_data, ttm_path, population_grid_region, cutoffs=cutoffs, on_step=st.write
        )

    st.subheader("% moyen d'équipements accessibles (pondéré population)")
    colonnes_pct = [f"pct_equipement_pondere_{c}min" for c in cutoffs]
    vue_synthese = (
        index_accessibilite.loc[index_accessibilite["decile"] == "Tous", ["nom_domaine", *colonnes_pct]]
        .set_index("nom_domaine")
        .rename(columns={c: f"<= {c.replace('pct_equipement_pondere_', '').replace('min', '')} min" for c in colonnes_pct})
    )
    st.dataframe(vue_synthese.style.format("{:.1f}%"), width="stretch")

    with st.expander("Détail par décile de niveau de vie"):
        st.dataframe(index_accessibilite, width="stretch")

    from src.utils import exporter_df_to_csv
    import os

    chemin_export = os.path.join("output", f"index_accessibilite_{nom_region_str}.csv")
    os.makedirs("output", exist_ok=True)
    exporter_df_to_csv(index_accessibilite, chemin_export)
    st.caption(f"Résultat exporté : {chemin_export}")
