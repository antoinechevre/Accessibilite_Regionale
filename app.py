"""
App Streamlit — accessibilité régionale en transport en commun.

Pipeline volontairement distinct de l'app agglo de référence
(github.com/antoinechevre/Accessibility_analysis) : pas de fusion multi-GTFS
ni de garde-fou "max 4 agences" (un GTFS régional agrège légitimement des
dizaines d'agences, cf. src/build_data_agglo.nom_region_via_gtfs), carroyage
1km directement (une région est toujours plus grosse qu'une agglo), cutoffs
d'accessibilité à 60/90 min par défaut plutôt que 30/45/60.
"""

import os

import streamlit as st

import views.accessibilite_region as accessibilite_region
import views.ponderation_equipements_region as ponderation_equipements_region
from src.pipeline_donnees_region import construire_donnees_region

st.set_page_config(page_title="Accessibilité régionale", layout="wide")

GTFS_DIR = os.path.join("data", "GTFS_Region")
os.makedirs(GTFS_DIR, exist_ok=True)

st.sidebar.title("Accessibilité régionale")

catalogue = sorted(f for f in os.listdir(GTFS_DIR) if f.endswith(".zip"))
OPTION_UPLOAD = "(uploader un fichier)"
choix = st.sidebar.selectbox("GTFS régional", options=[OPTION_UPLOAD, *catalogue])

gtfs_path = None
if choix == OPTION_UPLOAD:
    fichier = st.sidebar.file_uploader("Fichier GTFS régional (.zip)", type="zip")
    if fichier is not None:
        gtfs_path = os.path.join(GTFS_DIR, fichier.name)
        with open(gtfs_path, "wb") as f:
            f.write(fichier.getbuffer())
        st.sidebar.success(f"Fichier enregistré : {fichier.name}")
        st.rerun()
else:
    gtfs_path = os.path.join(GTFS_DIR, choix)

if gtfs_path is None:
    st.title("Accessibilité régionale")
    st.write(
        "Choisis un GTFS régional dans le catalogue, ou uploade-en un (GTFS agrégé à "
        "l'échelle d'une région française, ex. réseau BreizhGo/Korrigo en Bretagne), "
        "dans la barre latérale."
    )
    st.stop()

page = st.sidebar.radio("Page", ["Pondération des équipements", "Accessibilité (temps de trajet)"])


@st.cache_data(show_spinner=False)
def _donnees_region(gtfs_path):
    return construire_donnees_region(gtfs_path, on_step=st.write)


with st.spinner("Préparation des données régionales (découpage, grille population, BPE)..."):
    donnees = _donnees_region(gtfs_path)

if page == "Pondération des équipements":
    ponderation_equipements_region.afficher(donnees)
else:
    accessibilite_region.afficher(donnees, gtfs_path)
