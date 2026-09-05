"""
Traductions de l'interface (fr/en) et fonction d'accès t().

Chaque texte visible par l'utilisateur (app Streamlit, cartes Folium,
exports HTML) est identifié par une clé stable ; TRANSLATIONS contient
la version française et anglaise de chaque clé. t() renvoie la traduction
demandée, avec repli sur le français si la clé manque dans la langue
demandée, et sur la clé elle-même si elle n'existe nulle part.
"""

LANGUES = {"fr": "🇫🇷 Français", "en": "🇬🇧 English"}

TRANSLATIONS = {
    "fr": {
        # --- app.py : navigation et chargement ---
        "app.title": "🚌 Analyse GTFS - Indicateurs de Transport",
        "app.nav_accueil": "🏠 Accueil",
        "app.nav_arrets": "📍 Arrêts",
        "app.nav_lignes": "🛤️ Lignes",
        "app.sidebar_header": "📁 Paramètres d'analyse",
        "app.sidebar_uploader": "Uploader le fichier GTFS (zip)",
        "app.sidebar_langue": "🌐 Langue",
        "app.spinner_chargement": "Chargement du fichier GTFS...",
        "app.erreur_chargement": "Erreur lors du chargement : {erreur}",
        "app.erreur_trop_agences": "⚠ Ce GTFS regroupe {n} agences : ce que l'app ne peut pas gérer. Charger un GTFS urbain uniquement.",

        # --- infos réseau, communes à Arrêts et Lignes ---
        "commun.reseau_info": "Le GTFS concerne le réseau {reseau}",
        "commun.analyse_du": "Analyse du {date}",
        "commun.periode_service": "Période de service du {debut} au {fin}",
        "commun.plage_info": "Il est valide sur la plage {plage}, le JOB le plus loin dans le temps est {job}",
        "commun.veuillez_charger_gtfs": "👆 Veuillez charger un fichier GTFS.",
        "commun.calcul_en_cours": "🔄 Calcul des indicateurs en cours...",
        "commun.header_telechargement": "💾 Téléchargement",

        # --- views/home.py ---
        "home.hackathon_md": """
    ## Application analyse GTFS

    Ce projet a été développé lors du Hackathon TSNI 2025 du Cerema et repris par Antoine Chevre (et claude.ai...)

    **Équipe CEREMA :** Patrick GENDRE, Hugo DE LUCA et Maxence LIOGIER

    **Contributeur :** Antoine Chèvre 🐐 (et claude.ai....)
    """,
        "home.liens_md": """
    ## 🔗 Liens rapides

    Le projet originel
    Pour aller plus loin, vous pouvez consulter le notebook disponible sur Colab :
    - **📓 [Notebook Google Colab](https://colab.research.google.com/github/CEREMA/hackathon-gtfs/blob/main/gtfs_notebook.ipynb)** : Prendre en main le code, exécuter les cellules et regarder les cartographies dynamiques


    - Le projet amélioré https://github.com/antoinechevre/gtfs_analysis_app
    """,
        "home.objectifs_md": """
    ## Objectifs

    - **Offrir une chaîne de traitement** pour passer d'un jeu GTFS brut à des exports géolocalisés d'indicateurs à l'échelle des arrêts et des tronçons
    - **Proposer une offre d'indicateurs sur les tronçons** même en l'absence du fichier shapes.txt dans les données GTFS
    - **Proposer à la fois des scripts utilisables en local**, une interface web conviviale (via Streamlit) pour les utilisateurs non-techniques, et un notebook d'exemple pour tester / explorer les résultats
    """,
        "home.fonctionnalites_md": """
    ## Bienvenue dans l'application d'analyse GTFS

    Cette application vous permet d'analyser les données GTFS (General Transit Feed Specification)
    pour extraire des indicateurs clés sur les transports en commun.
    Elle détermine la plage temporelle sur laquelle le GTFS est actif et identifie un JOB (jour ouvrable de base mardi ou jeudi de manière aléatoire)


    ### Fonctionnalités disponibles :

    #### 📍 **Analyse par Arrêts**
    - Nombre de passages par arrêt
    - Carte interactive des arrêts
    - Statistiques détaillées

    #### 🛤️ **Analyse par Tronçons**
    - Nombre de passages par tronçon (bus, tram, métro, trolley, etc.)
    - Calcul des vitesses moyennes
    - Carte interactive des tronçons
    - app élargie à plusieurs GTFS français à retrouver sur https://transport.data.gouv.fr/ ou à l'international https://www.transit.land/ 


    ### Instructions :
    1. **Chargez un fichier GTFS** dans la barre latérale (format ZIP)
    2. **Naviguez entre les pages** pour explorer les analyses

    >
    > L'analyse par arrêts fonctionne quant à elle avec n'importe quel GTFS.

    pour aller chercher des jeux de données GTFS https://transport.data.gouv.fr/ pi à l'international https://www.transit.land/ 
    """,
        "home.contributeurs_md": """
    ## Contributeurs originaux  :
    - Hugo De Luca ([@hugo-deluca](https://github.com/hugo-deluca))
    - Maxence Liogier ([@maxenceLIOGIER](https://github.com/maxenceLIOGIER))
    - Patrick Gendre ([@PatGendre](https://github.com/PatGendre))

    ## Contributeur amélioration
    - Antoine Chèvre https://github.com/antoinechevre 🐐


    ---

    [*Projet open-source - Cerema 2025*](https://github.com/CEREMA/hackathon-gtfs)
    """,

        # --- views/arrets.py ---
        "arrets.spinner_indicateurs": "Calcul des indicateurs d'arrêts...",
        "arrets.erreur_indicateurs": "Erreur lors du calcul des arrêts : {erreur}",
        "arrets.succes": "✅ Analyse des arrêts terminée !",
        "arrets.header_stats": "📊 Statistiques Globales",
        "arrets.metric_nb_arrets": "Nombre d'arrêts",
        "arrets.metric_arrets_actifs": "Arrêts actifs",
        "arrets.metric_total_passages": "Total passages",
        "arrets.header_top10": "🏆 Top 10 Arrêts les plus fréquentés",
        "arrets.aucun_actif": "Aucun arrêt actif trouvé.",
        "arrets.header_fiche": "📄 Fiche Statistiques",
        "arrets.header_carte": "🗺️ Carte des Arrêts",
        "arrets.telecharger_csv": "📥 Télécharger les résultats CSV",

        # --- views/troncons.py ---
        "troncons.warning_limitations": """
    ⚠️ Cette analyse a été debuggée sur plusieurs GTFS en mentionnant les modes bus / tram / metro / trolley / ferry
    """,
        "troncons.spinner_calcul_auto": "🔄 Calcul automatique des tronçons {mode} depuis le GTFS...",
        "troncons.succes_calcul_auto": "✅ {n} tronçons {mode} calculés automatiquement",
        "troncons.erreur_calcul_auto": "❌ Erreur lors du calcul automatique des tronçons {mode}: {erreur}",
        "troncons.spinner_reference": "Chargement/Calcul des tronçons de référence...",
        "troncons.erreur_reference": "Impossible de calculer les tronçons de référence.",
        "troncons.spinner_indicateurs": "Calcul des indicateurs de tronçons...",
        "troncons.erreur_indicateurs": "Erreur lors du calcul des tronçons : {erreur}",
        "troncons.succes": "✅ Analyse des tronçons terminée !",
        "troncons.header_stats": "📊 Statistiques Globales",
        "troncons.metric_actifs": "Tronçons {mode} actifs",
        "troncons.metric_total_passages": "Total passages {mode}",
        "troncons.spinner_vkm": "Calcul des véh.km par ligne sur la plage de service...",
        "troncons.header_top": "Top 10 Tronçons {mode}",
        "troncons.aucun_actif": "Aucun tronçon {mode} actif.",
        "troncons.header_carte": "🗺️ Carte Interactive des Tronçons",
        "troncons.telecharger_csv": "📥 Télécharger {mode} CSV",
        "troncons.veuillez_charger_et_date": "👆 Veuillez charger un fichier GTFS et sélectionner une date dans la barre latérale.",

        # --- src/export_html.py ---
        "export.aucune_donnee_km": "Aucune donnée de kilométrage disponible.",
        "export.titre_page_camembert": "Répartition des véh.km par mode {reseau}",
        "export.titre_camembert": "Répartition des véh.km sur plage par mode {reseau}",
        "export.titre_page_tableau": "Lignes du réseau {reseau}",
        "export.titre_tableau": "Lignes du réseau {reseau}",
        "export.col_ligne": "Ligne",
        "export.col_mode": "Mode",
        "export.col_total_vkm": "Total veh.km/plage",
        "export.titre_stats_reseau": "Statistiques des arrêts {reseau}",
        "export.titre_stats": "Statistiques des arrêts",
        "export.sous_titre_job": "JOB - {date_job}, {plage}",
        "export.stat_arrets_desservis": "Arrêts desservis",
        "export.stat_passages_total": "Passages au total",
        "export.stat_moyenne": "Moyenne par arrêt",
        "export.stat_mediane": "Médiane par arrêt",
        "export.arret_vedette_label": "Arrêt le plus fréquenté :",
        "export.arret_vedette_passages": "passages",
        "export.premier_depart_global": "Premier départ global :",
        "export.dernier_depart_global": "Dernier départ global :",
        "export.top10_titre": "Top 10 des arrêts les plus fréquentés",
        "export.col_arret": "Arrêt",
        "export.col_passages_jour": "Passages / jour",
        "export.col_premier_depart": "Premier départ",
        "export.col_dernier_depart": "Dernier départ",

        # --- src/cartographie.py ---
        "carto.arret_popup": "Arrêt ID: {stop_id}\nPassages: {passages}",
        "carto.legende_passages_titre": "Nombre de passages",
        "carto.legende_passages_suffix": "passages",
        "carto.titre_reseau_job": "Réseau {reseau} en JOB",
        "carto.titre_reseau_troncons": "Réseau {reseau} - nombre de passages par tronçon et par mode en JOB",
        "carto.plein_ecran": "Plein écran",
        "carto.quitter_plein_ecran": "Quitter le plein écran",
        "carto.legende_passages_mode": "Nombre de passages {mode}",
        "carto.caption_passages_mode": "Nombre de passages {mode}",
        "carto.popup_troncon_titre": "TRONÇON {mode}",
        "carto.popup_id": "ID:",
        "carto.popup_de": "De:",
        "carto.popup_a": "À:",
        "carto.popup_passages": "Passages:",
        "carto.popup_vitesse": "Vitesse moy.:",
        "carto.popup_distance": "Distance:",
    },
    "en": {
        # --- app.py ---
        "app.title": "🚌 GTFS Analysis - Transit Indicators",
        "app.nav_accueil": "🏠 Home",
        "app.nav_arrets": "📍 Stops",
        "app.nav_lignes": "🛤️ Lines",
        "app.sidebar_header": "📁 Analysis settings",
        "app.sidebar_uploader": "Upload the GTFS file (zip)",
        "app.sidebar_langue": "🌐 Language",
        "app.spinner_chargement": "Loading GTFS file...",
        "app.erreur_chargement": "Error while loading: {erreur}",
        "app.erreur_trop_agences": "⚠ This GTFS covers {n} agencies, which the app cannot handle. Please load an urban GTFS only.",

        "commun.reseau_info": "This GTFS covers the {reseau} network",
        "commun.analyse_du": "Analysis of {date}",
        "commun.periode_service": "Service period from {debut} to {fin}",
        "commun.plage_info": "It is valid over the {plage} range, the latest Base Weekday (tuesday or thursday) is {job}",
        "commun.veuillez_charger_gtfs": "👆 Please upload a GTFS file.",
        "commun.calcul_en_cours": "🔄 Computing indicators...",
        "commun.header_telechargement": "💾 Download",

        # --- views/home.py ---
        "home.hackathon_md": """
    ## GTFS analysis application

    This project was developed during Cerema's 2025 TSNI Hackathon and later picked up by Antoine Chevre (and claude.ai...)

    **CEREMA team:** Patrick GENDRE, Hugo DE LUCA and Maxence LIOGIER

    **Contributor:** Antoine Chèvre 🐐 (and claude.ai....)
    """,
        "home.liens_md": """
    ## 🔗 Quick links

    The original project
    To go further, you can check out the notebook available on Colab:
    - **📓 [Google Colab Notebook](https://colab.research.google.com/github/CEREMA/hackathon-gtfs/blob/main/gtfs_notebook.ipynb)**: get familiar with the code, run the cells and look at the dynamic maps


    - The improved project https://github.com/antoinechevre/gtfs_analysis_app
    """,
        "home.objectifs_md": """
    ## Goals

    - **Provide a processing pipeline** to go from a raw GTFS dataset to geolocated exports of indicators at the stop and segment level
    - **Provide segment-level indicators** even when the GTFS data has no shapes.txt file
    - **Provide both locally runnable scripts**, a user-friendly web interface (via Streamlit) for non-technical users, and an example notebook to test / explore the results
    """,
        "home.fonctionnalites_md": """
    ## Welcome to the GTFS analysis application

    This application lets you analyze GTFS (General Transit Feed Specification) data
    to extract key public transit indicators.
    It determines the time range over which the GTFS is active and identifies a JOB (baseline weekday, randomly a Tuesday or Thursday)


    ### Available features:

    #### 📍 **Stop analysis**
    - Number of passages per stop
    - Interactive stop map
    - Detailed statistics

    #### 🛤️ **Segment analysis**
    - Number of passages per segment (bus, tram, metro, trolley, etc.)
    - Average speed calculation
    - Interactive segment map
    - App extended to several French GTFS feeds, available on https://transport.data.gouv.fr/ or https://www.transit.land/ for worldwide 


    ### Instructions:
    1. **Upload a GTFS file** in the sidebar (ZIP format)
    2. **Navigate between pages** to explore the analyses

    >
    > Stop analysis works with any GTFS.

    to find GTFS datasets: https://transport.data.gouv.fr/
    """,
        "home.contributeurs_md": """
    ## Original contributors:
    - Hugo De Luca ([@hugo-deluca](https://github.com/hugo-deluca))
    - Maxence Liogier ([@maxenceLIOGIER](https://github.com/maxenceLIOGIER))
    - Patrick Gendre ([@PatGendre](https://github.com/PatGendre))

    ## Improvement contributor
    - Antoine Chèvre https://github.com/antoinechevre 🐐


    ---

    [*Open-source project - Cerema 2025*](https://github.com/CEREMA/hackathon-gtfs)
    """,

        # --- views/arrets.py ---
        "arrets.spinner_indicateurs": "Computing stop indicators...",
        "arrets.erreur_indicateurs": "Error while computing stops: {erreur}",
        "arrets.succes": "✅ Stop analysis complete!",
        "arrets.header_stats": "📊 Overall statistics",
        "arrets.metric_nb_arrets": "Number of stops",
        "arrets.metric_arrets_actifs": "Active stops",
        "arrets.metric_total_passages": "Total passages",
        "arrets.header_top10": "🏆 Top 10 busiest stops",
        "arrets.aucun_actif": "No active stop found.",
        "arrets.header_fiche": "📄 Statistics sheet",
        "arrets.header_carte": "🗺️ Stop map",
        "arrets.telecharger_csv": "📥 Download CSV results",

        # --- views/troncons.py ---
        "troncons.warning_limitations": """
    ⚠️ This analysis has been debugged on several GTFS feeds covering the bus / tram / metro / trolley / ferry modes
    """,
        "troncons.spinner_calcul_auto": "🔄 Automatically computing {mode} segments from the GTFS...",
        "troncons.succes_calcul_auto": "✅ {n} {mode} segments computed automatically",
        "troncons.erreur_calcul_auto": "❌ Error while automatically computing {mode} segments: {erreur}",
        "troncons.spinner_reference": "Loading/computing reference segments...",
        "troncons.erreur_reference": "Unable to compute reference segments.",
        "troncons.spinner_indicateurs": "Computing segment indicators...",
        "troncons.erreur_indicateurs": "Error while computing segments: {erreur}",
        "troncons.succes": "✅ Segment analysis complete!",
        "troncons.header_stats": "📊 Overall statistics",
        "troncons.metric_actifs": "Active {mode} segments",
        "troncons.metric_total_passages": "Total {mode} passages",
        "troncons.spinner_vkm": "Computing vehicle-km per line over the service range...",
        "troncons.header_top": "Top 10 {mode} segments",
        "troncons.aucun_actif": "No active {mode} segment.",
        "troncons.header_carte": "🗺️ Interactive segment map",
        "troncons.telecharger_csv": "📥 Download {mode} CSV",
        "troncons.veuillez_charger_et_date": "👆 Please upload a GTFS file and select a date in the sidebar.",

        # --- src/export_html.py ---
        "export.aucune_donnee_km": "No mileage data available.",
        "export.titre_page_camembert": "Vehicle-km breakdown by mode {reseau}",
        "export.titre_camembert": "Vehicle-km breakdown over range by mode {reseau}",
        "export.titre_page_tableau": "{reseau} network lines",
        "export.titre_tableau": "{reseau} network lines",
        "export.col_ligne": "Line",
        "export.col_mode": "Mode",
        "export.col_total_vkm": "Total veh.km/range",
        "export.titre_stats_reseau": "{reseau} stop statistics",
        "export.titre_stats": "Stop statistics",
        "export.sous_titre_job": "JOB - {date_job}, {plage}",
        "export.stat_arrets_desservis": "Stops served",
        "export.stat_passages_total": "Total passages",
        "export.stat_moyenne": "Average per stop",
        "export.stat_mediane": "Median per stop",
        "export.arret_vedette_label": "Busiest stop:",
        "export.arret_vedette_passages": "passages",
        "export.premier_depart_global": "Overall first departure:",
        "export.dernier_depart_global": "Overall last departure:",
        "export.top10_titre": "Top 10 busiest stops",
        "export.col_arret": "Stop",
        "export.col_passages_jour": "Passages / day",
        "export.col_premier_depart": "First departure",
        "export.col_dernier_depart": "Last departure",

        # --- src/cartographie.py ---
        "carto.arret_popup": "Stop ID: {stop_id}\nPassages: {passages}",
        "carto.legende_passages_titre": "Number of passages",
        "carto.legende_passages_suffix": "passages",
        "carto.titre_reseau_job": "{reseau} network on JOB",
        "carto.titre_reseau_troncons": "{reseau} network - number of passages per segment and mode on JOB",
        "carto.plein_ecran": "Fullscreen",
        "carto.quitter_plein_ecran": "Exit fullscreen",
        "carto.legende_passages_mode": "Number of {mode} passages",
        "carto.caption_passages_mode": "Number of {mode} passages",
        "carto.popup_troncon_titre": "{mode} SEGMENT",
        "carto.popup_id": "ID:",
        "carto.popup_de": "From:",
        "carto.popup_a": "To:",
        "carto.popup_passages": "Passages:",
        "carto.popup_vitesse": "Avg. speed:",
        "carto.popup_distance": "Distance:",
    },
}


def t(key, lang="fr", **kwargs):
    """
    Renvoie la traduction de `key` dans la langue `lang`.

    Repli sur le français si la clé n'existe pas dans `lang`, puis sur la
    clé elle-même si elle n'existe nulle part (pour ne jamais planter
    l'affichage sur une clé oubliée).
    """
    texte = TRANSLATIONS.get(lang, {}).get(key, TRANSLATIONS["fr"].get(key, key))
    return texte.format(**kwargs) if kwargs else texte
