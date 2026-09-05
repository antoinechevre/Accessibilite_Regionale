"""
Génère le nuage de points HTML interactif (autonome, Plotly.js via CDN,
aucune dépendance serveur) du benchmark inter-réseaux — cf.
output/index_benchmark_reseaux.csv, alimenté par la cellule "#sauvegarde
index" du notebook et le bouton "Enregistrer les indicateurs de ce run" de
l'onglet Accessibilité.

Partagé entre scripts/generer_nuage_points_benchmark.py (fichier HTML
autonome, tous les réseaux traités comme égaux) et l'onglet Accessibilité de
l'app (intégré en fin de page, reseau_actuel surligné en rouge parmi les
autres réseaux en bleu).

Un point par (réseau, décile) sélectionné, étiqueté par la ville principale
(pas l'acronyme du réseau). Axes et filtres paramétrables directement dans
la page générée :
- Abscisses : population totale, véhicules.km (JOB), ou date JOB.
- Ordonnées : n'importe quelle colonne pct_equipement_pondere_<x>min ou
  temps_atteinte_<x>pct_min présente dans le CSV (détectée dynamiquement).
- Domaine BPE : un seul à la fois (mélanger les domaines sur un même nuage
  n'aurait pas de sens).
- Décile de niveau de vie : sélection multiple, comme le filtre de l'onglet
  Accessibilité (défaut : "Tous" seul, un point par réseau).
"""

import json
import re
import string

# Abscisses proposées : métadonnées de run (une valeur par réseau, répétée
# sur toutes ses lignes domaine x décile) — cf. calculer_index_benchmark et
# la cellule "#sauvegarde index" du notebook.
OPTIONS_X = [
    ("population_totale", "Population totale", "numerique"),
    ("vehicules_km_JOB", "Véhicules.km (jour JOB)", "numerique"),
    ("surface_km2", "Surface périmètre des transports urbains (km²)", "numerique"),
    ("date_JOB", "Date JOB", "date"),
]

LIBELLES_Y_FIXES = {
    "temps_atteinte_25pct_min": "Temps moyen pour atteindre 25% des équipements (min)",
    "temps_atteinte_50pct_min": "Temps moyen pour atteindre 50% des équipements (min)",
    "temps_atteinte_75pct_min": "Temps moyen pour atteindre 75% des équipements (min)",
}


def _libelle_pct_equipement(colonne):
    m = re.match(r"pct_equipement_pondere_(\d+)min", colonne)
    return f"% équipements pondérés accessibles à {m.group(1)} min"


def options_y(colonnes):
    """Détecte dynamiquement les colonnes de métrique Y disponibles dans le
    CSV plutôt que de les figer en dur : suit calculer_index_benchmark
    (src/utilitaires_matrix.py) si ses cutoffs/seuils changent un jour."""
    temps, pct = [], []
    for c in colonnes:
        if c in LIBELLES_Y_FIXES:
            temps.append((c, LIBELLES_Y_FIXES[c]))
        elif re.match(r"pct_equipement_pondere_\d+min", c):
            pct.append((c, _libelle_pct_equipement(c)))
    temps.sort(key=lambda t: int(re.search(r"\d+", t[0]).group()))
    pct.sort(key=lambda t: int(re.search(r"\d+", t[0]).group()))
    return temps + pct


def generer_html_str(df, reseau_actuel=None):
    """Retourne le HTML du nuage de points (chaîne, pas de fichier écrit).

    reseau_actuel: si fourni (valeur de la colonne "reseau"), les points de
    ce réseau sont surlignés en rouge parmi les autres en bleu (mode
    comparaison, utilisé par l'onglet Accessibilité) — indépendamment du
    nombre de déciles sélectionnés. Sinon (défaut, mode autonome), la
    couleur suit le décile (dégradé bleu ordinal, cf. couleurDecile en JS).

    Si un même réseau apparaît avec plusieurs date_JOB différentes (ex:
    fusions concurrentes app/notebook/batch qui se sont chevauchées), seules
    les lignes de la date_JOB la plus récente pour ce réseau sont gardées —
    date_JOB est au format YYYYMMDD (ordre lexicographique = ordre
    chronologique), pas besoin de le parser en date.
    """
    if "date_JOB" in df.columns:
        df = df.loc[df.groupby("reseau")["date_JOB"].transform("max") == df["date_JOB"]]

    options_x_dispo = [(c, l, t) for c, l, t in OPTIONS_X if c in df.columns]
    options_y_dispo = options_y(df.columns)

    for col in ("reseau", "ville_principale", "domaine", "nom_domaine", "decile"):
        if col not in df.columns:
            raise ValueError(f"Colonne attendue absente du benchmark : {col}")
    if not options_x_dispo:
        raise ValueError("Aucune colonne d'abscisses reconnue (population_totale / vehicules_km_JOB / date_JOB).")
    if not options_y_dispo:
        raise ValueError("Aucune colonne d'ordonnées reconnue (pct_equipement_pondere_*min / temps_atteinte_*pct_min).")

    domaines_dispo = df[["domaine", "nom_domaine"]].drop_duplicates().sort_values("domaine").values.tolist()

    def _cle_tri_decile(d):
        return (d != "Tous", int(d[1:]) if d.startswith("D") else 0)

    deciles_dispo = sorted(df["decile"].unique(), key=_cle_tri_decile)

    colonnes_utiles = ["reseau", "ville_principale", "domaine", "decile"] + [c for c, _, _ in options_x_dispo] + [
        c for c, _ in options_y_dispo
    ]
    donnees = df[colonnes_utiles].to_dict(orient="records")

    template = string.Template(TEMPLATE_HTML)
    return template.substitute(
        donnees_json=json.dumps(donnees, ensure_ascii=False, default=str),
        options_x_json=json.dumps(options_x_dispo, ensure_ascii=False),
        options_y_json=json.dumps(options_y_dispo, ensure_ascii=False),
        domaines_json=json.dumps(domaines_dispo, ensure_ascii=False),
        deciles_json=json.dumps(list(deciles_dispo), ensure_ascii=False),
        nb_reseaux=df["reseau"].nunique(),
        reseau_actuel_json=json.dumps(reseau_actuel, ensure_ascii=False) if reseau_actuel else "null",
    )


TEMPLATE_HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Benchmark inter-réseaux — nuage de points</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6;
    --couleur-actuel: #e34948;
    --couleur-autres: #2a78d6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --gridline: #2c2c2a;
      --baseline: #383835;
      --border: rgba(255,255,255,0.10);
      --series-1: #3987e5;
      --couleur-actuel: #e66767;
      --couleur-autres: #3987e5;
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .page {
    max-width: 1200px;
    height: 100vh;
    margin: 0 auto;
    padding: 24px 20px 20px;
    overflow-y: auto;
  }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
  .sous-titre { color: var(--text-secondary); font-size: 13px; margin: 0 0 20px; }
  .filtres {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: flex-end;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  .filtre label {
    display: block;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .03em;
    color: var(--text-muted);
    margin-bottom: 4px;
  }
  .filtre select {
    font: inherit;
    font-size: 13px;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid var(--baseline);
    background: var(--surface-1);
    color: var(--text-primary);
    min-width: 220px;
  }
  .menu-perso { position: relative; }
  .menu-perso-bouton {
    font: inherit;
    font-size: 13px;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid var(--baseline);
    background: var(--surface-1);
    color: var(--text-primary);
    min-width: 220px;
    text-align: left;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }
  .menu-perso-bouton::after { content: "▾"; color: var(--text-muted); font-size: 10px; }
  .menu-perso-liste {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 20;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 4px;
    margin: 0;
    list-style: none;
    min-width: 100%;
    max-height: 240px;
    overflow-y: auto;
    box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  }
  .menu-perso-liste[hidden] { display: none; }
  .menu-perso-liste li {
    padding: 6px 10px;
    font-size: 13px;
    border-radius: 6px;
    cursor: pointer;
    white-space: nowrap;
  }
  .menu-perso-liste li:hover, .menu-perso-liste li.actif { background: var(--gridline); }
  .deciles { display: flex; flex-wrap: wrap; gap: 4px 10px; max-width: 420px; }
  .deciles label {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    text-transform: none;
    letter-spacing: 0;
    color: var(--text-primary);
    cursor: pointer;
  }
  .deciles input { cursor: pointer; }
  #chart {
    min-height: 260px;
    width: 100%;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  .bas-de-page { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }
  .bas-de-page button {
    font: inherit;
    font-size: 12px;
    padding: 6px 10px;
    border-radius: 6px;
    border: 1px solid var(--baseline);
    background: var(--surface-1);
    color: var(--text-primary);
    cursor: pointer;
  }
  #zone-tableau { margin-top: 16px; display: none; max-height: 240px; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; background: var(--surface-1); }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--gridline); }
  th { color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: .03em; }
  td.num { font-variant-numeric: tabular-nums; text-align: right; }
</style>
</head>
<body>
<div class="page">
  <h1>Benchmark inter-réseaux — accessibilité aux équipements</h1>
  <p class="sous-titre">$nb_reseaux réseau(x) — issu de output/index_benchmark_reseaux.csv</p>

  <div class="filtres">
    <div class="filtre">
      <label>Abscisses</label>
      <select id="select-x" hidden></select>
    </div>
    <div class="filtre">
      <label>Ordonnées</label>
      <select id="select-y" hidden></select>
    </div>
    <div class="filtre">
      <label>Domaine d'équipement</label>
      <select id="select-domaine" hidden></select>
    </div>
    <div class="filtre">
      <label>Décile de niveau de vie (D1 = plus modeste, D10 = plus aisé)</label>
      <div class="deciles" id="deciles"></div>
    </div>
  </div>

  <div id="chart"></div>

  <div class="bas-de-page">
    <span class="sous-titre" id="compte-points"></span>
    <button id="btn-tableau" type="button">Afficher le tableau</button>
  </div>

  <div id="zone-tableau"><table id="tableau"><thead></thead><tbody></tbody></table></div>
</div>

<script>
const DONNEES = $donnees_json;
const OPTIONS_X = $options_x_json;   // [[colonne, libelle, type], ...]
const OPTIONS_Y = $options_y_json;   // [[colonne, libelle], ...]
const DOMAINES = $domaines_json;     // [[code, libelle], ...]
const DECILES = $deciles_json;       // ["Tous", "D1", ...]
const RESEAU_ACTUEL = $reseau_actuel_json;  // nom du réseau à surligner, ou null

function cssVar(nom) {
  return getComputedStyle(document.documentElement).getPropertyValue(nom).trim();
}

function interpolerHex(hexA, hexB, t) {
  const versRgb = h => [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  const a = versRgb(hexA.slice(1)), b = versRgb(hexB.slice(1));
  return "#" + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
// Rampe séquentielle ordinale (palette.md) : D1 = step250, D10 = step600.
function couleurDecile(decile) {
  if (decile === "Tous") return "#2a78d6"; // slot catégoriel 1 : seule série par défaut
  const rang = parseInt(decile.slice(1), 10) - 1; // D1 -> 0 ... D10 -> 9
  return interpolerHex("#86b6ef", "#184f95", rang / 9);
}

// Hauteur du graphique fixée une fois (espace disponible dans .page, sous
// le titre/les filtres, avant l'apparition éventuelle du tableau) plutôt que
// recalculée en continu via flexbox : #zone-tableau est display:none à ce
// stade, donc l'espace mesuré ici correspond à la vue "tableau fermé" —
// l'ouvrir agrandit .page (qui défile, cf. overflow-y:auto) sans jamais
// redimensionner le graphique déjà tracé. Un graphique redimensionné en
// continu par flexbox ne peut pas rétrécir sous la hauteur que Plotly lui a
// donnée à son premier rendu (min-height auto = taille du contenu), ce qui
// écrasait titres d'axes/légende les uns sur les autres dès que le tableau
// s'ouvrait.
function ajusterHauteurChart() {
  const page = document.querySelector(".page");
  const chart = document.getElementById("chart");
  const autresElements = Array.from(page.children).filter(el => el !== chart && el.id !== "zone-tableau");
  const hauteurAutres = autresElements.reduce((somme, el) => somme + el.offsetHeight, 0);
  const stylePage = getComputedStyle(page);
  const paddingVertical = parseFloat(stylePage.paddingTop) + parseFloat(stylePage.paddingBottom);
  const hauteurDisponible = page.clientHeight - hauteurAutres - paddingVertical;
  chart.style.height = Math.max(280, hauteurDisponible) + "px";
}

function remplirSelect(select, options, valeurDefaut) {
  select.textContent = "";
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt[0];
    el.textContent = opt[1];
    select.appendChild(el);
  }
  if (valeurDefaut) select.value = valeurDefaut;
}

// Menu déroulant "maison" (bouton + liste en JS pur) au-dessus d'un <select>
// natif gardé caché en DOM comme état/interface (redessiner() continue de
// lire selectX.value etc. sans changement) : l'iframe sandboxée de Streamlit
// (components.v1.html) a sandbox="allow-same-origin allow-scripts
// allow-downloads", sans allow-forms — Chrome y bloque l'ouverture du menu
// natif d'un <select> au clic réel (les <input type=checkbox> des déciles,
// eux, n'ont pas besoin d'un popup natif et restent utilisables). Vérifié
// par un test headless reproduisant exactement ce sandbox.
function creerMenuPersonnalise(select) {
  const conteneur = document.createElement("div");
  conteneur.className = "menu-perso";
  const bouton = document.createElement("button");
  bouton.type = "button";
  bouton.className = "menu-perso-bouton";
  const liste = document.createElement("ul");
  liste.className = "menu-perso-liste";
  liste.hidden = true;
  conteneur.appendChild(bouton);
  conteneur.appendChild(liste);
  select.insertAdjacentElement("afterend", conteneur);

  function libelleDe(valeur) {
    const opt = Array.from(select.options).find(o => o.value === valeur);
    return opt ? opt.textContent : "";
  }

  function rafraichir() {
    bouton.textContent = libelleDe(select.value);
    liste.textContent = "";
    for (const opt of select.options) {
      const li = document.createElement("li");
      li.textContent = opt.textContent;
      if (opt.value === select.value) li.classList.add("actif");
      li.addEventListener("click", () => {
        select.value = opt.value;
        select.dispatchEvent(new Event("change"));
        liste.hidden = true;
        rafraichir();
      });
      liste.appendChild(li);
    }
  }

  bouton.addEventListener("click", (e) => {
    e.stopPropagation();
    liste.hidden = !liste.hidden;
  });
  document.addEventListener("click", () => { liste.hidden = true; });

  rafraichir();
}

const selectX = document.getElementById("select-x");
const selectY = document.getElementById("select-y");
const selectDomaine = document.getElementById("select-domaine");
const zoneDeciles = document.getElementById("deciles");

remplirSelect(selectX, OPTIONS_X.map(o => [o[0], o[1]]), OPTIONS_X[0][0]);
remplirSelect(selectY, OPTIONS_Y, OPTIONS_Y[0][0]);
remplirSelect(
  selectDomaine,
  DOMAINES.map(([code, libelle]) => [code, `$${code} - $${libelle}`]),
  (DOMAINES.find(([code]) => code === "O") || DOMAINES[0])[0]
);

[selectX, selectY, selectDomaine].forEach(creerMenuPersonnalise);

for (const decile of DECILES) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.value = decile;
  input.checked = decile === "Tous";
  label.appendChild(input);
  label.appendChild(document.createTextNode(decile));
  zoneDeciles.appendChild(label);
}
// Si aucun "Tous" dans les données (CSV généré autrement), cocher le premier décile par défaut.
if (!DECILES.includes("Tous") && zoneDeciles.firstChild) {
  zoneDeciles.firstChild.querySelector("input").checked = true;
}

function decilesSelectionnes() {
  return Array.from(zoneDeciles.querySelectorAll("input:checked")).map(i => i.value);
}

function typeX(colonneX) {
  return (OPTIONS_X.find(o => o[0] === colonneX) || [null, null, "numerique"])[2];
}

function valeurX(ligne, colonneX) {
  if (typeX(colonneX) === "date") {
    const s = String(ligne[colonneX]);
    return `$${s.slice(0,4)}-$${s.slice(4,6)}-$${s.slice(6,8)}`;
  }
  return ligne[colonneX];
}

let derniereSelection = [];

// Plusieurs déciles cochés à la fois : un point par réseau x décile
// tombait à la même abscisse (population_totale/vehicules_km_JOB/date_JOB
// ne varient pas par décile pour un même réseau), donc les points/étiquettes
// se superposaient. Combine en un seul point par réseau, moyenne (simple)
// de colY sur les déciles sélectionnés.
function agregerParReseau(pts, colY) {
  const parReseau = new Map();
  for (const p of pts) {
    if (!parReseau.has(p.reseau)) parReseau.set(p.reseau, []);
    parReseau.get(p.reseau).push(p);
  }
  const resultat = [];
  for (const lignes of parReseau.values()) {
    const moyenneY = lignes.reduce((somme, l) => somme + l[colY], 0) / lignes.length;
    resultat.push({ ...lignes[0], [colY]: moyenneY, decile: lignes.map(l => l.decile).join(", ") });
  }
  return resultat;
}

function traceDe(nom, couleur, pts, colX, colY, libelleX, libelleY, couleurTexte, couleurAnneau) {
  return {
    x: pts.map(l => valeurX(l, colX)),
    y: pts.map(l => l[colY]),
    text: pts.map(l => l.ville_principale),
    customdata: pts.map(l => [l.reseau, l.decile]),
    mode: "markers+text",
    type: "scatter",
    name: nom,
    textposition: "top center",
    textfont: { size: 11, color: couleurTexte },
    marker: { size: 10, color: couleur, line: { width: 2, color: couleurAnneau } },
    hovertemplate:
      "<b>%{text}</b> (%{customdata[0]})<br>" +
      libelleX + " : %{x}<br>" +
      libelleY + " : %{y:.1f}<br>" +
      "Décile : %{customdata[1]}<extra></extra>",
  };
}

function redessiner() {
  const colX = selectX.value, colY = selectY.value, domaine = selectDomaine.value;
  const deciles = decilesSelectionnes();
  const libelleX = OPTIONS_X.find(o => o[0] === colX)[1];
  const libelleY = OPTIONS_Y.find(o => o[0] === colY)[1];

  const filtreBrut = DONNEES.filter(l => l.domaine === domaine && deciles.includes(l.decile));
  const combineDeciles = deciles.length > 1;
  const filtre = combineDeciles ? agregerParReseau(filtreBrut, colY) : filtreBrut;
  derniereSelection = filtre;

  const couleurTexte = cssVar("--text-secondary");
  const couleurAnneau = cssVar("--surface-1");

  let traces, showlegend;
  if (RESEAU_ACTUEL) {
    // Comparaison : 2 couleurs fixes (réseau analysé vs les autres),
    // indépendamment du nombre de déciles cochés.
    const autres = filtre.filter(l => l.reseau !== RESEAU_ACTUEL);
    const actuel = filtre.filter(l => l.reseau === RESEAU_ACTUEL);
    traces = [
      traceDe("Autres réseaux", cssVar("--couleur-autres"), autres, colX, colY, libelleX, libelleY, couleurTexte, couleurAnneau),
      traceDe(`$${RESEAU_ACTUEL} (ce réseau)`, cssVar("--couleur-actuel"), actuel, colX, colY, libelleX, libelleY, couleurTexte, couleurAnneau),
    ];
    showlegend = true;
  } else if (combineDeciles) {
    // Plusieurs déciles cochés, mode autonome : un seul point (agrégé) par
    // réseau plutôt qu'une couleur par décile, qui n'a plus de sens ici.
    traces = [
      traceDe(`Moyenne ($${deciles.join(", ")})`, couleurDecile("Tous"), filtre, colX, colY, libelleX, libelleY, couleurTexte, couleurAnneau),
    ];
    showlegend = true;
  } else {
    // Un seul décile coché : une couleur par décile (dégradé ordinal) —
    // en pratique une seule trace ici, mais garde la couleur cohérente
    // avec le reste du dégradé D1..D10.
    traces = deciles.map(decile =>
      traceDe(decile, couleurDecile(decile), filtre.filter(l => l.decile === decile), colX, colY, libelleX, libelleY, couleurTexte, couleurAnneau)
    );
    showlegend = false;
  }

  const couleurGrille = cssVar("--gridline");
  const couleurAxe = cssVar("--baseline");

  const layout = {
    margin: { l: 60, r: 20, t: 10, b: 50 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, -apple-system, Segoe UI, sans-serif", color: "#898781", size: 12 },
    xaxis: {
      title: libelleX,
      type: typeX(colX) === "date" ? "date" : "linear",
      gridcolor: couleurGrille,
      zerolinecolor: couleurAxe,
      linecolor: couleurAxe,
    },
    yaxis: {
      title: libelleY,
      gridcolor: couleurGrille,
      zerolinecolor: couleurAxe,
      linecolor: couleurAxe,
    },
    showlegend: showlegend,
    legend: { orientation: "h", y: -0.18 },
    hovermode: "closest",
  };

  Plotly.react("chart", traces, layout, { displayModeBar: true, responsive: true });
  document.getElementById("compte-points").textContent = `$${filtre.length} point(s) affiché(s)`;
  if (document.getElementById("zone-tableau").style.display !== "none") remplirTableau(colX, colY, libelleX, libelleY);
}

function remplirTableau(colX, colY, libelleX, libelleY) {
  const thead = document.querySelector("#tableau thead");
  const tbody = document.querySelector("#tableau tbody");
  thead.textContent = "";
  tbody.textContent = "";

  const ligneEntete = document.createElement("tr");
  for (const texte of ["Ville principale", "Réseau", "Décile", libelleX, libelleY]) {
    const th = document.createElement("th");
    th.textContent = texte;
    ligneEntete.appendChild(th);
  }
  thead.appendChild(ligneEntete);

  for (const l of derniereSelection) {
    const tr = document.createElement("tr");
    const cellules = [l.ville_principale, l.reseau, l.decile, valeurX(l, colX), typeof l[colY] === "number" ? l[colY].toFixed(1) : l[colY]];
    cellules.forEach((valeur, i) => {
      const td = document.createElement("td");
      td.textContent = valeur;
      if (i >= 3) td.className = "num";
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
}

// Synchronise le style inline avec l'état initial défini en CSS (display:
// none) : sans ça, zone.style.display vaut "" (jamais fixé en inline) au
// premier clic, donc affichee (comparé à "none") est vrai à tort et le
// bouton ne fait rien visuellement avant le DEUXIÈME clic.
document.getElementById("zone-tableau").style.display = "none";

document.getElementById("btn-tableau").addEventListener("click", () => {
  const zone = document.getElementById("zone-tableau");
  const affichee = zone.style.display !== "none";
  zone.style.display = affichee ? "none" : "block";
  document.getElementById("btn-tableau").textContent = affichee ? "Afficher le tableau" : "Masquer le tableau";
  if (!affichee) remplirTableau(selectX.value, selectY.value, OPTIONS_X.find(o=>o[0]===selectX.value)[1], OPTIONS_Y.find(o=>o[0]===selectY.value)[1]);
});

[selectX, selectY, selectDomaine].forEach(el => el.addEventListener("change", redessiner));
zoneDeciles.addEventListener("change", redessiner);

window.addEventListener("resize", () => {
  ajusterHauteurChart();
  Plotly.Plots.resize("chart");
});

ajusterHauteurChart();
redessiner();
</script>
</body>
</html>
"""
