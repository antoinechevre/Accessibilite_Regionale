"""
Page HTML autonome listant, pour chaque réseau de l'index de benchmark
(output/index_benchmark_reseaux.csv) : ville principale, nom de réseau,
période de validité du GTFS (date_debut/date_fin) et date_JOB — cf. le
bouton "Ouvrir la liste des réseaux" de l'onglet Benchmark villes
françaises (views/benchmark_reseaux.py), ouverte dans un nouvel onglet via
un lien data: URI (pas de fichier à servir, autonome comme les nuages de
points de src/nuage_points_*.py).

Valeurs telles qu'enregistrées lors du dernier run ayant écrit ce réseau
dans le benchmark (pas recalculées à la volée) — cf. la discussion "valeurs
déjà dans le benchmark" plutôt qu'un recalcul en direct, trop coûteux pour
~60 réseaux à chaque ouverture.
"""

import html


def _formater_date(valeur):
    """YYYYMMDD (str/int, format GTFS) -> YYYY-MM-DD, ou "?" si absent."""
    if valeur is None:
        return "?"
    s = str(int(valeur)) if isinstance(valeur, float) else str(valeur)
    if len(s) != 8 or not s.isdigit():
        return s
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def generer_html_str(df):
    """Retourne le HTML de la page (chaîne, pas de fichier écrit).

    Une ligne par réseau (dédoublonné — le benchmark a plusieurs lignes par
    réseau, une par domaine x décile, mais ville_principale/date_JOB etc.
    sont les mêmes sur toutes), trié par ville principale."""
    colonnes = ["ville_principale", "reseau", "date_debut", "date_fin", "date_JOB"]
    for col in colonnes:
        if col not in df.columns:
            raise ValueError(f"Colonne attendue absente du benchmark : {col}")

    tableau = df[colonnes].drop_duplicates(subset="reseau").sort_values("ville_principale")

    lignes_html = []
    for _, ligne in tableau.iterrows():
        lignes_html.append(
            "<tr><td>{ville}</td><td>{reseau}</td><td class='num'>{debut}</td>"
            "<td class='num'>{fin}</td><td class='num'>{job}</td></tr>".format(
                ville=html.escape(str(ligne["ville_principale"])),
                reseau=html.escape(str(ligne["reseau"])),
                debut=_formater_date(ligne["date_debut"]),
                fin=_formater_date(ligne["date_fin"]),
                job=_formater_date(ligne["date_JOB"]),
            )
        )

    return TEMPLATE_HTML.format(nb_reseaux=len(tableau), lignes="\n".join(lignes_html))


TEMPLATE_HTML = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Réseaux du benchmark — période de validité</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --border: rgba(11,11,11,0.10);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --gridline: #2c2c2a;
      --border: rgba(255,255,255,0.10);
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .page {{ max-width: 900px; margin: 0 auto; padding: 24px 20px 40px; }}
  h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 4px; }}
  .sous-titre {{ color: var(--text-secondary); font-size: 13px; margin: 0 0 20px; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--gridline); }}
  th {{ color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: .03em; }}
  td.num, th.num {{ font-variant-numeric: tabular-nums; }}
  tr:last-child td {{ border-bottom: none; }}
</style>
</head>
<body>
<div class="page">
  <h1>Réseaux du benchmark — période de validité</h1>
  <p class="sous-titre">{nb_reseaux} réseau(x) — issu de output/index_benchmark_reseaux.csv</p>
  <table>
    <thead>
      <tr><th>Ville principale</th><th>Réseau</th><th class="num">Début validité</th><th class="num">Fin validité</th><th class="num">Date JOB</th></tr>
    </thead>
    <tbody>
{lignes}
    </tbody>
  </table>
</div>
</body>
</html>
"""
