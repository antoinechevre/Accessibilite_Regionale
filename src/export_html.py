import pandas as pd

import sys
from pathlib import Path

# Ajoute gtfs_analysis/ (le dossier parent de src/) à sys.path, en se basant
# sur l'emplacement réel de ce fichier plutôt que sur le répertoire de travail
# courant — indispensable pour que l'import fonctionne quel que soit l'endroit
# d'où le script est lancé (notebook ou ligne de commande).
sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.utils import km_par_ligne_plage
from src.i18n import t


def formater_km(valeur):
    if pd.isna(valeur):
        return "-"
    return f"{valeur:,.0f} km".replace(",", " ")


# Palette de couleurs pour le camembert, dans l'esprit TBM (bleu en tête)
PALETTE_MODE = ["#01B1EC", "#c1d4e2", "#8e44ad", "#1BB092", "#e67e22", "#c0392b", "#722f37"]

# CSS partagé entre les pages exportées (tableau des lignes et camembert seul)
CSS_BASE = """
    body {
        font-family: Arial, Helvetica, sans-serif;
        background-color: #f7f7f5;
        margin: 0;
        padding: 40px;
    }
    .conteneur {
        max-width: 700px;
        margin: 0 auto;
        background: white;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
    }
    .entete {
        background-color: #01B1EC;
        padding: 20px 24px;
    }
    .entete h1 {
        margin: 0;
        font-size: 20px;
        color: #ffffff;
    }
    .entete p {
        margin: 4px 0 0;
        font-size: 13px;
        color: #e5f8fe;
    }
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th {
        background-color: #e5f8fe;
        text-align: left;
        padding: 12px 24px;
        font-size: 13px;
        color: #01578a;
        border-bottom: 2px solid #01B1EC;
    }
    td {
        padding: 12px 24px;
        font-size: 14px;
        border-bottom: 1px solid #eee;
    }
    tr:hover td {
        background-color: #f0fbff;
    }
    .col-numero {
        font-weight: bold;
        color: #01B1EC;
    }
    .col-km {
        text-align: right;
        color: #01578a;
        white-space: nowrap;
    }
    .section-titre {
        margin: 28px 24px 12px;
        font-size: 15px;
        color: #1a1a1a;
    }
    .camembert-conteneur {
        display: flex;
        align-items: center;
        gap: 24px;
        padding: 0 24px 24px;
        flex-wrap: wrap;
    }
    .camembert {
        width: 280px;
        height: 280px;
        border-radius: 50%;
        flex-shrink: 0;
    }
    .legende {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .legende-item {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        color: #333;
    }
    .pastille {
        width: 12px;
        height: 12px;
        border-radius: 3px;
        flex-shrink: 0;
    }
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 12px;
        padding: 24px;
    }
    .stat-card {
        background: #e5f8fe;
        border-radius: 8px;
        padding: 12px 16px;
    }
    .stat-valeur {
        font-size: 22px;
        font-weight: bold;
        color: #01B1EC;
    }
    .stat-label {
        font-size: 12px;
        color: #555;
        margin-top: 2px;
    }
    .arret-vedette {
        margin: 0 24px 8px;
        padding: 12px 16px;
        background: #fff8d6;
        border-radius: 8px;
        font-size: 13px;
        color: #333;
        line-height: 1.6;
    }
"""


def construire_camembert_html(lignes, lang="fr"):
    """
    Construit un camembert (en CSS conic-gradient, sans dépendance externe)
    représentant la répartition des véhicule.km sur plage par mode de transport.

    Parameters
    ----------
    lignes : DataFrame
        DataFrame des lignes actives, avec les colonnes "mode" et
        "total_veh.km_plage".

    Returns
    -------
    str
        Bloc HTML autonome (camembert + légende), à insérer dans une page.
    """
    repartition = (
        lignes.groupby("mode")["total_km_plage"]
        .sum()
        .sort_values(ascending=False)
    )
    total = repartition.sum()

    if total == 0 or pd.isna(total):
        return f"<p>{t('export.aucune_donnee_km', lang)}</p>"

    stops = []
    legende_html = ""
    cumul_pct = 0.0
    for i, (mode, valeur) in enumerate(repartition.items()):
        pct = valeur / total * 100
        couleur = PALETTE_MODE[i % len(PALETTE_MODE)]
        stops.append(f"{couleur} {cumul_pct:.3f}% {cumul_pct + pct:.3f}%")
        cumul_pct += pct
        legende_html += f"""
                <div class="legende-item">
                    <span class="pastille" style="background-color:{couleur};"></span>
                    <span>{mode} — {formater_km(valeur)} ({pct:.1f}%)</span>
                </div>"""

    gradient = ", ".join(stops)
    return f"""
        <div class="camembert-conteneur">
            <div class="camembert" style="background: conic-gradient({gradient});"></div>
            <div class="legende">{legende_html}
            </div>
        </div>"""


def exporter_camembert_html(nom_reseau_str, date_service_str, lignes, output_path, lang="fr"):
    """
    Génère un fichier HTML autonome ne contenant que le camembert de
    répartition des véhicule.km plage par mode de transport, avec le style TBM.

    Parameters
    ----------
    lignes : DataFrame
        DataFrame des lignes actives, avec les colonnes "mode" et
        "total_veh.km_plage" (voir exporter_tableau_lignes_html).
    output_path : str
        Chemin du fichier HTML de sortie.
    """
    camembert_html = construire_camembert_html(lignes, lang=lang)

    titre_page = t("export.titre_page_camembert", lang, reseau=nom_reseau_str)
    titre = t("export.titre_camembert", lang, reseau=nom_reseau_str)

    html_content = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<title>{titre_page}</title>
<style>
{CSS_BASE}
</style>
</head>
<body>
    <div class="conteneur">
        <div class="entete">
            <h1>{titre}</h1>
            <p>{date_service_str}</p>
        </div>
        {camembert_html}
    </div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✓ Camembert HTML exporté : {output_path}")


def exporter_tableau_lignes_html(nom_reseau_str, date_service_str, feed, output_path, total_vk_plage=None, lang="fr", ordre_mode=None):
    """
    Génère un fichier HTML autonome présentant la liste des lignes et les vk par an, avec un style prédéfini.

    Parameters
    ----------
    feed : gtfs_kit.Feed
        Le feed GTFS chargé.
    output_path : str
        Chemin du fichier HTML de sortie
    total_vk_plage : DataFrame, optional
        DataFrame avec les colonnes route_id et total_km_annee (voir
        calculer_total_km_plage  / Gtfs_notebook_3.ipynb). Si non fourni,
        il est recalculé à partir du feed.
    ordre_mode : dict, optional
        Mapping nom de mode -> rang de tri, pour remplacer l'ordre par
        défaut (ex: ORDRE_MODE_IDFM dans views/troncons.py, qui place "RER"
        en tête pour distinguer RER/Transilien/TER sur IDFM).
    """
    if total_vk_plage is None:
        total_vk_plage = km_par_ligne_plage(feed.get_dates(), feed)

    # total_vk_year contient déjà "mode" (voir km_par_ligne_an) : on renomme
    # juste la colonne de kilométrage pour l'affichage
    lignes = total_vk_plage.rename(columns={"total_km_plage": "total_veh.km_plage"})

    # Tri : d'abord par mode (Métro, puis Tram, puis Bus, puis les autres modes),
    # puis au sein d'un même mode par numéro de ligne (valeur numérique
    # d'abord, puis les lignes non-numériques par ordre alphabétique)
    ORDRE_MODE = ordre_mode if ordre_mode is not None else {"Métro": 0, "Tram": 1, "Trolley": 2, "Ferry": 3, "Bus": 4}

    def cle_tri(row):
        nom = str(row["route_short_name"])
        ordre_mode = ORDRE_MODE.get(row["mode"], len(ORDRE_MODE))
        if nom.isdigit():
            return (ordre_mode, 0, int(nom), "")
        return (ordre_mode, 1, 0, nom)

    lignes = lignes.assign(_cle=lignes.apply(cle_tri, axis=1))
    lignes = lignes.sort_values("_cle").drop(columns="_cle").reset_index(drop=True)

    # Construction des lignes du tableau HTML
    lignes_html = ""
    for _, row in lignes.iterrows():
        lignes_html += f"""
        <tr>
            <td><span class="col-numero">{row['route_short_name']}</span> — {row['route_long_name']}</td>
            <td>{row['mode']}</td>
            <td class="col-km">{formater_km(row['total_veh.km_plage'])}</td>
        </tr>"""
   

    titre_page = t("export.titre_page_tableau", lang, reseau=nom_reseau_str)
    titre = t("export.titre_tableau", lang, reseau=nom_reseau_str)

    html_content = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<title>{titre_page}</title>
<style>
{CSS_BASE}
</style>
</head>
<body>
    <div class="conteneur">
        <div class="entete">
            <h1>{titre}</h1>
            <p>{date_service_str}</p>
        </div>
        <table>
            <thead>
                <tr>
                    <th>{t("export.col_ligne", lang)}</th>
                    <th>{t("export.col_mode", lang)}</th>
                    <th>{t("export.col_total_vkm", lang)}</th>
                </tr>
            </thead>
            <tbody>
                {lignes_html}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✓ Tableau HTML exporté : {output_path}")
    
    
def exporter_statistiques_html(df, date_service_str, date_job_text, output_path, nom_reseau_str=None, lang="fr"):
    """
    Génère un fichier HTML autonome présentant les statistiques résumées des
    arrêts (issues de calculer_indicateurs_arrets), avec le même style que
    les autres pages exportées.

    Reprend les informations affichées par afficher_statistiques (src/arrets.py) :
    nombre d'arrêts, total/moyenne/médiane de passages, arrêt le plus
    fréquenté, plage horaire globale, et le top 10 des arrêts les plus
    fréquentés.

    Parameters
    ----------
    df : DataFrame
        DataFrame des indicateurs par arrêt, trié par nombre_passages
        décroissant (voir calculer_indicateurs_arrets). Doit contenir au
        moins stop_name, nombre_passages, premier_depart, dernier_depart.
    date_job : str
        Date du jour analysé (format YYYYMMDD, voir date_JOB dans le
        notebook), affichée dans le titre.
    output_path : str
        Chemin du fichier HTML de sortie.
    nom_reseau_str : str, optional
        Nom du réseau à afficher dans le titre (voir nom_reseau_str dans utils.py).
    """
    titre = (
        t("export.titre_stats_reseau", lang, reseau=nom_reseau_str)
        if nom_reseau_str
        else t("export.titre_stats", lang)
    )
    sous_titre = t("export.sous_titre_job", lang, date_job=date_job_text, plage=date_service_str)

    arret_vedette = df.iloc[0]

    stats_html = f"""
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-valeur">{len(df):,}</div>
                <div class="stat-label">{t("export.stat_arrets_desservis", lang)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-valeur">{df['nombre_passages'].sum():,}</div>
                <div class="stat-label">{t("export.stat_passages_total", lang)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-valeur">{df['nombre_passages'].mean():.1f}</div>
                <div class="stat-label">{t("export.stat_moyenne", lang)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-valeur">{df['nombre_passages'].median():.1f}</div>
                <div class="stat-label">{t("export.stat_mediane", lang)}</div>
            </div>
        </div>
        <div class="arret-vedette">
            <strong>{t("export.arret_vedette_label", lang)}</strong> {arret_vedette['stop_name']} ({arret_vedette['nombre_passages']} {t("export.arret_vedette_passages", lang)})<br>
            <strong>{t("export.premier_depart_global", lang)}</strong> {df['premier_depart'].min()} —
            <strong>{t("export.dernier_depart_global", lang)}</strong> {df['dernier_depart'].max()}
        </div>"""

    lignes_top10 = ""
    for _, row in df.head(10).iterrows():
        lignes_top10 += f"""
        <tr>
            <td>{row['stop_name']}</td>
            <td class="col-km">{row['nombre_passages']:,}</td>
            <td>{row['premier_depart']}</td>
            <td>{row['dernier_depart']}</td>
        </tr>"""

    html_content = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<title>{titre}</title>
<style>
{CSS_BASE}
</style>
</head>
<body>
    <div class="conteneur">
        <div class="entete">
            <h1>{titre}</h1>
            <p>{sous_titre}</p>
        </div>
        {stats_html}
        <h2 class="section-titre">{t("export.top10_titre", lang)}</h2>
        <table>
            <thead>
                <tr>
                    <th>{t("export.col_arret", lang)}</th>
                    <th>{t("export.col_passages_jour", lang)}</th>
                    <th>{t("export.col_premier_depart", lang)}</th>
                    <th>{t("export.col_dernier_depart", lang)}</th>
                </tr>
            </thead>
            <tbody>
                {lignes_top10}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✓ Statistiques HTML exportées : {output_path}")
