import folium
from folium import plugins
import numpy as np
import branca.colormap as cm
import gtfs_kit as gk
import base64
import json
import os
import mimetypes

from src.i18n import t


def titre_carte_html(titre):
    """Titre flottant pour une carte folium plein cadre (position: fixed,
    au-dessus de la carte), au lieu d'un <h3> en flux normal : le div Leaflet
    de la carte (position: absolute, top:0, height:100%) recouvre sinon
    entièrement un titre placé en flux normal au-dessus de lui dans le body,
    le rendant invisible à l'affichage bien qu'il soit présent dans le DOM."""
    return f"""
    <div style="
        position: fixed;
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 9999;
        background: white;
        padding: 10px 20px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        font-family: Arial, Helvetica, sans-serif;
        white-space: nowrap;
    ">
        <div style="font-size: 16px; font-weight: bold;">{titre}</div>
    </div>
    """


def script_reajuster_si_masque(m, bounds):
    """<script> qui réajuste une carte Leaflet (invalidateSize + fitBounds)
    une fois son conteneur stabilisé en taille.

    Cas concret : dans un onglet Streamlit (st.tabs), le script Python
    s'exécute une seule fois et rend TOUS les onglets dans le DOM, seul
    l'onglet actif étant affiché (masqué en CSS pour les autres). Leaflet
    calcule sa vue initiale (fit_bounds) sur un conteneur de taille 0 pour
    les onglets inactifs à ce moment-là, d'où la vue par défaut ([0, 0],
    zoom 1) qu'on obtient à l'affichage — et il ne la recalcule jamais tout
    seul une fois l'onglet effectivement affiché. Un ResizeObserver sur le
    conteneur de la carte permet de détecter ce moment et de la rafraîchir.

    Même un onglet actif dès le premier rendu n'est pas épargné : l'iframe
    (st.components.v1.html) n'a pas de largeur fixée, donc sa largeur réelle
    dépend de la mise en page Streamlit (colonnes, onglets, polices...) qui
    se stabilise en plusieurs passes après le premier paint. Réagir au tout
    premier redimensionnement (comme avant) applique la correction sur une
    largeur encore provisoire, avec une animation de zoom Leaflet visible à
    chaque nouvelle passe : la carte semblait "trembler" pendant la
    stabilisation de la page (signalé sur Chrome desktop). D'où le debounce
    ci-dessous : n'appliquer la correction, sans animation, qu'une fois les
    redimensionnements retombés au calme pendant DEBOUNCE_MS.

    carte._reajustementEnCours, posé pendant l'appel invalidateSize/
    fitBounds : signal lu par script_synchroniser_zoom pour ne PAS
    rediffuser ce recadrage correctif aux cartes synchronisées avec elle. Un
    fitBounds()/invalidateSize() déclenche "moveend" même avec animate:false
    — sans ce signal, une paire de cartes synchronisées (cf.
    accessibilite_urbaine_2._carte_accessibilite_45min_domaine, qui applique
    les deux scripts à la même carte) se rediffusaient l'une à l'autre leur
    propre recadrage correctif au chargement, chacune écrasant le cadrage
    tout juste reçu de l'autre avec le sien — un aller-retour qui produisait
    exactement le même symptôme de tremblement que le debounce ci-dessus est
    censé éliminer, mais entre deux cartes plutôt qu'au sein d'une seule.

    m: objet folium.Map (dont l'un des volets m1/m2 d'un DualMap).
    bounds: [[miny, minx], [maxy, maxx]] (mêmes coordonnées que fit_bounds).
        Accepte des numpy.float64 (ex: issus de .total_bounds) : castés en
        float Python avant sérialisation JSON — leur repr Python
        ("np.float64(43.57)", depuis numpy 2.x) n'est pas du JS valide, {{bounds!r}}
        cassait silencieusement ce script (ReferenceError: np is not defined,
        visible seulement en pageerror, jamais atteint la correction de zoom).
    """
    nom_carte = m.get_name()
    bounds_json = json.dumps([[float(lat), float(lon)] for lat, lon in bounds])
    return f"""
    <script>
    window.addEventListener("load", function() {{
        // Différé à "load" : ce <script>, ajouté via get_root().html, est
        // injecté dans le body AVANT le <script> (ajouté via get_root().script
        // par folium) qui déclare {nom_carte} = L.map(...) — y accéder plus tôt
        // lèverait une ReferenceError, {nom_carte} n'existe pas encore à ce
        // stade du parsing.
        var carte = {nom_carte};
        var DEBOUNCE_MS = 250;
        var minuteur = null;
        var observer = new ResizeObserver(function(entries) {{
            for (var entree of entries) {{
                if (entree.contentRect.width > 0 && entree.contentRect.height > 0) {{
                    // Redémarre le délai à chaque redimensionnement : tant que
                    // la mise en page bouge encore, on attend — la correction
                    // ne part qu'une fois DEBOUNCE_MS écoulées sans nouveau
                    // redimensionnement (mise en page réellement stabilisée).
                    clearTimeout(minuteur);
                    minuteur = setTimeout(function() {{
                        carte._reajustementEnCours = true;
                        carte.invalidateSize({{animate: false}});
                        carte.fitBounds({bounds_json}, {{animate: false}});
                        // setTimeout(0) plutôt qu'une remise à false immédiate :
                        // laisse le temps à "moveend" de se déclencher (synchrone
                        // avec animate:false chez Leaflet) et d'être vu par
                        // script_synchroniser_zoom avant de lever le signal.
                        setTimeout(function() {{ carte._reajustementEnCours = false; }}, 0);
                        // Un seul réajustement : cet observer sert uniquement à
                        // rattraper le cadrage initial calculé sur un conteneur
                        // pas encore à sa taille finale. Sans ce disconnect(),
                        // il continuerait à réagir à TOUT redimensionnement
                        // ultérieur du conteneur (autre onglet qui s'affiche...)
                        // et réappliquerait ce cadrage d'origine à chaque fois —
                        // ce qui écraserait silencieusement un zoom/déplacement
                        // manuel de l'utilisateur sur cette carte, ou une vue
                        // reçue via script_synchroniser_zoom.
                        observer.disconnect();
                    }}, DEBOUNCE_MS);
                    return;
                }}
            }}
        }});
        observer.observe(carte.getContainer());
    }});
    </script>
    """


def script_synchroniser_zoom(m, canal):
    """<script> qui synchronise le zoom/centre de cette carte Leaflet avec
    une (ou plusieurs) autre(s) carte(s) partageant le même `canal`, via
    BroadcastChannel.

    Nécessaire (plutôt qu'un simple folium.plugins.DualMap, cf.
    accessibilite_index.py) quand les deux cartes à synchroniser sont deux
    documents HTML distincts, chacun rendu dans sa propre iframe
    (st.components.v1.html) : DualMap ne synchronise que deux panneaux d'un
    même document, pas deux cartes indépendantes. BroadcastChannel
    fonctionne entre iframes same-origin sans passer par window.parent (que
    l'appelant ne maîtrise pas, c'est le JS interne de Streamlit).

    m: objet folium.Map à synchroniser.
    canal: nom de canal BroadcastChannel commun aux cartes à synchroniser
        entre elles. Doit être unique à la paire (ex: un canal par
        onglet/domaine ET par session utilisateur) : BroadcastChannel est
        visible par tout document same-origin, y compris d'autres onglets du
        navigateur ou d'autres sessions Streamlit — un canal partagé entre
        des cartes qui ne devraient pas être liées (ex: deux domaines BPE
        différents, restés simultanément dans le DOM par onglets Streamlit,
        cf. script_reajuster_si_masque) les synchroniserait par erreur.
    """
    nom_carte = m.get_name()
    canal_json = json.dumps(canal)
    return f"""
    <script>
    window.addEventListener("load", function() {{
        // Différé à "load" pour la même raison que script_reajuster_si_masque :
        // {nom_carte} n'existe pas encore au moment où ce <script> (ajouté via
        // get_root().html) est injecté dans le body.
        var carte = {nom_carte};
        var canal = new BroadcastChannel({canal_json});
        var enSynchronisation = false;

        carte.on("moveend", function() {{
            // carte._reajustementEnCours (posé par script_reajuster_si_masque
            // pendant son propre invalidateSize/fitBounds correctif au
            // chargement) : ce moveend-là n'est pas un déplacement de
            // l'utilisateur, ne pas le rediffuser — sinon deux cartes
            // synchronisées se renvoient chacune leur recadrage correctif au
            // chargement, chacune écrasant le cadrage tout juste reçu de
            // l'autre (effet de tremblement).
            if (enSynchronisation || carte._reajustementEnCours) return;
            var centre = carte.getCenter();
            canal.postMessage({{lat: centre.lat, lng: centre.lng, zoom: carte.getZoom()}});
        }});

        canal.onmessage = function(evenement) {{
            // setView(..., {{animate: false}}) déclenche moveend de façon
            // synchrone (avant le retour de l'appel) : le flag posé juste
            // avant suffit à empêcher la carte réceptrice de rediffuser à son
            // tour l'état qu'elle vient de recevoir (boucle infinie sinon).
            enSynchronisation = true;
            carte.setView([evenement.data.lat, evenement.data.lng], evenement.data.zoom, {{animate: false}});
            enSynchronisation = false;
        }};
    }});
    </script>
    """


def echelle_continue_html(vmin, vmax, cmap, caption, cote="gauche"):
    """Légende flottante (dégradé + bornes) en bas d'une carte folium plein
    cadre, position fixed (comme titre_carte_html) — pour une échelle
    continue (colonne numérique sans scheme).

    Remplace le colorbar ajouté par .explore(legend=True, ...) : sur un
    DualMap, le template branca cible TOUS les colorbars de la page via un
    sélecteur CSS non isolé par carte (d3.select(".legend.leaflet-control"),
    premier match seulement) — le second colorbar s'empile dans le premier
    au lieu de s'afficher sur son propre panneau. D'où .explore(...,
    legend=False) sur les cartes concernées et cette légende maison à la
    place (pas de bug équivalent pour une légende catégorielle à scheme,
    cf. script_legende_en_bas pour ce cas).

    vmin, vmax: bornes numériques de l'échelle.
    cmap: nom de colormap matplotlib (ex: "inferno", "cividis_r").
    caption: légende texte.
    cote: "gauche", "droite" ou "centre" — bas de la page (les deux
        panneaux d'un DualMap partagent le même document HTML, donc
        "gauche"/"droite" correspond directement à chaque panneau).
    """
    from matplotlib import colormaps
    from matplotlib.colors import to_hex

    palette = colormaps[cmap]
    degrade_css = ", ".join(to_hex(palette(i / 7)) for i in range(8))
    position_css = {
        "gauche": "left: 20px;",
        "droite": "right: 20px;",
        "centre": "left: 50%; transform: translateX(-50%);",
    }[cote]

    return f"""
    <div style="
        position: fixed;
        bottom: 20px;
        {position_css}
        z-index: 9999;
        background: white;
        padding: 8px 12px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        font-family: Arial, Helvetica, sans-serif;
        font-size: 12px;
        color: #333;
        width: 180px;
    ">
        <div style="margin-bottom:4px;">{caption}</div>
        <div style="height:10px;border-radius:3px;background:linear-gradient(to right,{degrade_css});"></div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;margin-top:2px;">
            <span>{vmin:.0f}</span><span>{vmax:.0f}</span>
        </div>
    </div>
    """


def script_legende_en_bas():
    """<style> qui déplace en bas la légende native ajoutée par
    .explore(legend=True, ...) (colorbar continu ou légende catégorielle à
    scheme, positionnée en haut à droite par défaut par branca — cf.
    branca/templates/color_scale.js, L.control({{position: 'topright'}})) :
    évite qu'elle chevauche le titre flottant (titre_carte_html, lui aussi
    en haut). Repositionnement CSS pur : sûr uniquement pour une carte
    SEULE dans la page (contrairement à un DualMap, où c'est le contenu
    même de la légende qui est cassé, pas seulement sa position — cf.
    echelle_continue_html pour ce cas)."""
    return """
    <style>
    .leaflet-top.leaflet-right {
        top: auto !important;
        bottom: 20px !important;
    }
    </style>
    """


def carte_population_infracommunale(population_grid_agglo, tiles="CartoDB positron", carreaux_filtre_ids=None):
    """Carte HTML interactive de la population par carreau (carroyage INSEE
    200x200, Filosofi 2019) — offre brute de données, pas d'accessibilité.
    Partagée entre le notebook (cellule "Retourne différentes dates") et
    l'onglet Cartographie INSEE de l'app (aperçu local au-dessus du lien
    vers l'outil officiel, qui ne peut pas être intégré en iframe).

    carreaux_filtre_ids: si fourni, restreint les carreaux affichés à cet
    ensemble d'id (ex: filtre par décile de niveau de vie, cf.
    src.utilitaires_matrix.deciles_niveau_vie). Retourne None si le filtre
    ne laisse aucun carreau.
    """
    grille = population_grid_agglo
    if carreaux_filtre_ids is not None:
        grille = grille[grille["id"].isin(carreaux_filtre_ids)]
    if grille.empty:
        return None

    carte = grille.explore(
        "population",
        cmap="Reds",
        tiles=tiles,
        style_kwds={
            "style_function": lambda x: {
                "fillOpacity": 0 if x["properties"]["population"] == 0 else 0.7,
                "weight": 0,
                "opacity": 0,
            }
        },
        # prefer_canvas : argument direct de .explore() (pas dans map_kwds,
        # que geopandas rejette explicitement pour ce paramètre) — cf. même
        # correctif dans accessibilite_index.py.
        prefer_canvas=True,
    )
    carte.get_root().html.add_child(
        folium.Element(titre_carte_html("Population infracommunale INSEE 2019"))
    )
    return carte


def create_carte_arrets(df, nom_reseau_str,date_service_str, date_analyse, zip_path, output_path, chemin_logo=None, lang="fr"):
    # Carte des arrêts avec leur nombre de passages

    # Définir les seuils pour les couleurs : 5 classes de même effectif
    # (quantiles), pas de même largeur
    passages_values = df["nombre_passages"].values
    bins = np.percentile(passages_values, [0, 20, 40, 60, 80, 100])  # 6 bornes -> 5 classes avec mêm nombre d'arrêts

    def get_color(passages):
        if passages <= bins[1]:
            return "#1BB092"  # classe 1 : la moins fréquentée
        elif passages <= bins[2]:
            return "#a6d96a"
        elif passages <= bins[3]:
            return "#ffcc00"
        elif passages <= bins[4]:
            return "#e67e22"
        else:
            return "#c0392b"  # classe 5 : la plus fréquentée
    m = folium.Map(
        location=[df["stop_lat"].mean(), df["stop_lon"].mean()],
        zoom_start=12,
        width="100%",
        height="1000px",
        tiles="cartodbpositron",
    )

    # --- Ajout des lignes GTFS sur la même carte ---
    feed = gk.read_feed(zip_path, dist_units="km")
    active_trips = feed.get_trips(date=date_analyse)
    trips_routes = active_trips.merge(feed.routes, on='route_id')

    if feed.shapes is not None and not feed.shapes.empty:
        shapes_actifs = feed.shapes[feed.shapes['shape_id'].isin(active_trips['shape_id'].unique())]
        geo_shapes = gk.geometrize_shapes(shapes_actifs)
        geo_shapes = geo_shapes.merge(
            trips_routes[['shape_id', 'route_short_name', 'route_color']].drop_duplicates(),
            on='shape_id'
        )

        for _, row in geo_shapes.iterrows():
            coords = [(lat, lon) for lon, lat in row.geometry.coords]
            folium.PolyLine(
                coords,
                color="grey" ,
                weight=1,
                opacity=0.7,
                tooltip=row['route_short_name']
            ).add_to(m)
    else:
        # shapes.txt est optionnel dans la spec GTFS (absent par exemple du
        # jeu de données TCL) : à défaut, on trace un trip représentatif par
        # ligne à partir des arrêts qu'il dessert.
        print("⚠ shapes.txt absent du GTFS : tracé des lignes estimé à partir des arrêts desservis")

        trip_par_route = trips_routes.drop_duplicates(subset='route_id')
        stops = feed.stops.set_index('stop_id')[['stop_lat', 'stop_lon']]

        stop_times = feed.stop_times[feed.stop_times['trip_id'].isin(trip_par_route['trip_id'])]
        stop_times = stop_times.merge(trip_par_route[['trip_id', 'route_short_name']], on='trip_id')
        stop_times = stop_times.merge(stops, on='stop_id')
        stop_times = stop_times.sort_values(['trip_id', 'stop_sequence'])

        for _, groupe in stop_times.groupby('trip_id'):
            coords = list(zip(groupe['stop_lat'], groupe['stop_lon']))
            folium.PolyLine(
                coords,
                color="grey",
                weight=1,
                opacity=0.7,
                tooltip=groupe['route_short_name'].iloc[0]
            ).add_to(m)
    # --- Fin ajout des lignes ---

    for _, row in df.iterrows():
        stop_id = row["stop_id"]
        lat = row["stop_lat"]
        lon = row["stop_lon"]
        passages = row["nombre_passages"]

        color = get_color(passages)

        folium.CircleMarker(
            location=[lat, lon],
            radius=2,
            popup=t("carto.arret_popup", lang, stop_id=stop_id, passages=passages),
            color=color,
            fill=True,
            fill_color=color,
        ).add_to(m)

    # Légende des classes de nombre de passages
    classes = [
        ("#1BB092", f"{int(bins[0])} – {int(bins[1])}"),
        ("#a6d96a", f"{int(bins[1])} – {int(bins[2])}"),
        ("#ffcc00", f"{int(bins[2])} – {int(bins[3])}"),
        ("#e67e22", f"{int(bins[3])} – {int(bins[4])}"),
        ("#c0392b", f"{int(bins[4])} – {int(bins[5])}"),
    ]
    suffixe_passages = t("carto.legende_passages_suffix", lang)
    items_html = "".join(
        f"""
        <div style="display:flex;align-items:center;gap:6px;margin:2px 0;">
            <span style="width:12px;height:12px;border-radius:50%;background:{couleur};display:inline-block;flex-shrink:0;"></span>
            <span>{libelle} {suffixe_passages}</span>
        </div>"""
        for couleur, libelle in classes
    )
    # Titre de la carte, en haut au centre
    titre_carte = t("carto.titre_reseau_job", lang, reseau=nom_reseau_str)
    titre_html = f"""
    <div style="
        position: fixed;
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 9999;
        background: white;
        padding: 10px 20px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        font-family: Arial, Helvetica, sans-serif;
        white-space: nowrap;
    ">
        <div style="font-size: 16px; font-weight: bold; color: #01B1EC;">{titre_carte}</div>
        <p style="margin: 4px 0 0; font-size: 12px; font-weight: normal; color: #555;">{date_service_str}</p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(titre_html))

    # Légende, en bas à gauche de la carte
    legende_html = f"""
    <div style="
        position: fixed;
        bottom: 30px;
        left: 30px;
        z-index: 9999;
        background: white;
        padding: 10px 14px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        font-family: Arial, Helvetica, sans-serif;
        font-size: 12px;
        color: #333;
    ">
        <div style="font-weight:bold;margin-bottom:6px;">{t("carto.legende_passages_titre", lang)}</div>
        {items_html}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legende_html))

    # Logo du réseau, en bas à droite de la carte
    if chemin_logo and os.path.isfile(chemin_logo):
        type_mime = mimetypes.guess_type(chemin_logo)[0] or "image/png"
        with open(chemin_logo, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")
        logo_html = f"""
        <div style="
            position: fixed;
            bottom: 30px;
            right: 30px;
            z-index: 9999;
            background: white;
            padding: 8px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        ">
            <img src="data:{type_mime};base64,{logo_base64}" style="height:48px;display:block;">
        </div>
        """
        m.get_root().html.add_child(folium.Element(logo_html))

    m.save(output_path)

    print(f"\n✓ Carte des arrêts enregistrée dans : {output_path}")

    return m

def _ajouter_couche_troncons_generique(
    m, gdf, nom_couche, emoji, colors, colonne_frequence, lang,
    legende_items_html, popup_id, popup_de, popup_a, popup_passages,
    popup_vitesse, popup_distance, suffixe_passages,
    popup_color=None, weight_base=2, weight_amplitude=6,
):
    """
    Ajoute une couche de tronçons colorée par fréquence à une carte Folium
    existante, avec le même rendu (popup, tooltip, légende) que les couches
    bus/tram/metro/trolley/ferry/train ci-dessous.

    Mécanisme générique sans connaissance d'un réseau particulier : permet
    à un appelant (ex: gtfs_notebook_idf.ipynb, pour distinguer RER,
    Transilien et TER) d'ajouter ses propres sous-catégories sur la carte
    sans dupliquer tout le code de rendu, et sans que cartographie.py ait à
    connaître ces sous-catégories.

    Returns:
    --------
    str : legende_items_html mis à jour (avec cette couche ajoutée si non vide)
    """
    if len(gdf) == 0 or colonne_frequence not in gdf.columns:
        return legende_items_html

    gdf_actif = gdf[gdf[colonne_frequence] > 0].copy()
    if len(gdf_actif) == 0:
        return legende_items_html

    vmin = gdf_actif[colonne_frequence].min()
    vmax = gdf_actif[colonne_frequence].max()
    popup_color = popup_color or colors[-1]

    colormap = cm.LinearColormap(
        colors=colors, vmin=vmin, vmax=vmax,
        caption=t("carto.caption_passages_mode", lang, mode=nom_couche),
    )

    feature_group = folium.FeatureGroup(name=f"{emoji} {nom_couche}", show=True)

    for idx, row in gdf_actif.iterrows():
        freq = row[colonne_frequence]
        color = colormap(freq)
        coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

        popup_html = f"""
        <div style="font-family: Arial; font-size: 12px; width: 250px;">
            <b style="color: {popup_color};">{emoji} {t("carto.popup_troncon_titre", lang, mode=nom_couche.upper())}</b><br>
            <hr style="margin: 5px 0;">
            <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
            <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
            <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
            <hr style="margin: 5px 0;">
            <b>{popup_passages}</b> {int(freq)}<br>
            <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
            <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
        </div>
        """

        weight = weight_base + (freq - vmin) / (vmax - vmin) * weight_amplitude

        folium.PolyLine(
            coords,
            color=color,
            weight=weight,
            opacity=0.8,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
        ).add_to(feature_group)

    feature_group.add_to(m)

    gradient = ",".join(colors)
    legende_items_html += f"""
    <div style="margin-bottom:8px;">
        <div style="font-size:11px;margin-bottom:2px;">{emoji} {t("carto.legende_passages_mode", lang, mode=nom_couche)}</div>
        <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,{gradient});"></div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
            <span>{int(vmin)}</span><span>{int(vmax)}</span>
        </div>
    </div>"""

    return legende_items_html


def creer_carte_troncons(gdf_bus, gdf_tram,gdf_metro, gdf_trolley, gdf_ferry, gdf_train, output_path,date_service_str, colonne_frequence="nombre_passages", nom_reseau_str=None, chemin_logo=None, lang="fr", couches_supplementaires=None):
    """
    Crée une carte Folium interactive avec les tronçons bus et tram.
    Les tronçons sont colorés selon la fréquence et peuvent être activés/désactivés.

    Parameters:
    -----------
    gdf_bus : GeoDataFrame
        GeoDataFrame des tronçons bus avec indicateurs
    gdf_tram : GeoDataFrame
        GeoDataFrame des tronçons tram avec indicateurs
    gdf_metro : GeoDataFrame
        GeoDataFrame des tronçons metro avec indicateurs
    gdf_trolley : GeoDataFrame
        GeoDataFrame des tronçons trolley avec indicateurs
    gdf_ferry : GeoDataFrame
        GeoDataFrame des tronçons ferry avec indicateurs
    gdf_train : GeoDataFrame
        GeoDataFrame des tronçons train (route_type=2 : RER, Transilien, TER...) avec indicateurs
    chemin output lien html
    date_service str
        colonne_frequence : str
        Nom de la colonne contenant la fréquence (défaut: 'nombre_passages')
    nom_reseau_str : str, optional
        Nom du réseau à afficher dans le titre de la carte.
    chemin_logo : str, optional
        Chemin local du logo du réseau (voir recuperer_logo_reseau dans
        utils.py), affiché en haut à droite de la carte.
    couches_supplementaires : list[tuple], optional
        Couches additionnelles à superposer, chacune sous la forme
        (gdf, nom_couche, emoji, colors, poids_trait) — même rendu que les
        couches fixes ci-dessus, via _ajouter_couche_troncons_generique.
        poids_trait multiplie l'épaisseur de trait par défaut (1 = normal,
        2 = deux fois plus épais, ex. RER). Permet à un appelant de
        distinguer de nouvelles sous-catégories (ex: RER pour IDFM, cf.
        gtfs_notebook_idf.ipynb) sans que cette fonction ait à les connaître.

    Returns:
    --------
    folium.Map
        Carte Folium interactive
    """

    # Déterminer le centre de la carte (moyenne des coordonnées)
    all_coords = []
    for gdf in [gdf_bus, gdf_tram, gdf_metro, gdf_trolley, gdf_ferry, gdf_train]:
        if len(gdf) > 0:
            all_coords.extend(gdf["lat_depart_parent"].dropna().tolist())
            all_coords.extend(gdf["lat_arrivee_parent"].dropna().tolist())

    if not all_coords:
        center_lat, center_lon = 45.75, 4.85  # Lyon par défaut
    else:
        center_lat = np.mean(all_coords)
        all_lons = []
        for gdf in [gdf_bus, gdf_tram, gdf_metro, gdf_trolley, gdf_ferry, gdf_train]:
            if len(gdf) > 0:
                all_lons.extend(gdf["lon_depart_parent"].dropna().tolist())
                all_lons.extend(gdf["lon_arrivee_parent"].dropna().tolist())
        center_lon = np.mean(all_lons)

    # Créer la carte de base (fond de carte en nuances de gris par défaut)
    m = folium.Map(
        location=[center_lat, center_lon], zoom_start=12, tiles="cartodbpositron"
    )
    # Ajouter des fonds de carte alternatifs
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer("cartodbdark_matter", name="Carto Dark").add_to(m)

    # Légende des échelles de couleur (construite au fil des blocs bus/tram
    # ci-dessous, affichée en bas de carte)
    legende_items_html = ""

    suffixe_passages = t("carto.legende_passages_suffix", lang)
    popup_id = t("carto.popup_id", lang)
    popup_de = t("carto.popup_de", lang)
    popup_a = t("carto.popup_a", lang)
    popup_passages = t("carto.popup_passages", lang)
    popup_vitesse = t("carto.popup_vitesse", lang)
    popup_distance = t("carto.popup_distance", lang)

    # ===== TRONÇONS BUS =====
    if len(gdf_bus) > 0 and colonne_frequence in gdf_bus.columns:
        # Filtrer les tronçons avec passages
        gdf_bus_actif = gdf_bus[gdf_bus[colonne_frequence] > 0].copy()

        if len(gdf_bus_actif) > 0:
            # Créer la palette de couleurs pour les bus
            vmin_bus = gdf_bus_actif[colonne_frequence].min()
            vmax_bus = gdf_bus_actif[colonne_frequence].max()

            colormap_bus = cm.LinearColormap(
                colors=["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
                vmin=vmin_bus,
                vmax=vmax_bus,
                caption=t("carto.caption_passages_mode", lang, mode="Bus"),
            )

            # Créer un groupe de features pour les bus
            feature_group_bus = folium.FeatureGroup(name="🚌 Bus", show=True)

            # Ajouter chaque tronçon bus
            for idx, row in gdf_bus_actif.iterrows():
                freq = row[colonne_frequence]
                color = colormap_bus(freq)

                # Extraire les coordonnées de la géométrie
                coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

                # Créer le popup avec les informations
                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <b style="color: #d63447;">🚌 {t("carto.popup_troncon_titre", lang, mode="BUS")}</b><br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
                    <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
                    <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_passages}</b> {int(freq)}<br>
                    <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
                    <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
                </div>
                """

                # Épaisseur proportionnelle à la fréquence
                weight = 2 + (freq - vmin_bus) / (vmax_bus - vmin_bus) * 6

                folium.PolyLine(
                    coords,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
                ).add_to(feature_group_bus)

            feature_group_bus.add_to(m)

            legende_items_html += f"""
            <div style="margin-bottom:8px;">
                <div style="font-size:11px;margin-bottom:2px;">🚌 {t("carto.legende_passages_mode", lang, mode="Bus")}</div>
                <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,#fee5d9,#fcae91,#fb6a4a,#de2d26,#a50f15);"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
                    <span>{int(vmin_bus)}</span><span>{int(vmax_bus)}</span>
                </div>
            </div>"""

    # ===== TRONÇONS TRAM =====
    if len(gdf_tram) > 0 and colonne_frequence in gdf_tram.columns:
        # Filtrer les tronçons avec passages
        gdf_tram_actif = gdf_tram[gdf_tram[colonne_frequence] > 0].copy()

        if len(gdf_tram_actif) > 0:
            # Créer la palette de couleurs pour les trams
            vmin_tram = gdf_tram_actif[colonne_frequence].min()
            vmax_tram = gdf_tram_actif[colonne_frequence].max()

            colormap_tram = cm.LinearColormap(
                colors=["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
                vmin=vmin_tram,
                vmax=vmax_tram,
                caption=t("carto.caption_passages_mode", lang, mode="Tram"),
            )

            # Créer un groupe de features pour les trams
            feature_group_tram = folium.FeatureGroup(name="🚊 Tram", show=True)

            # Ajouter chaque tronçon tram
            for idx, row in gdf_tram_actif.iterrows():
                freq = row[colonne_frequence]
                color = colormap_tram(freq)

                # Extraire les coordonnées de la géométrie
                coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

                # Créer le popup avec les informations
                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <b style="color: #28a745;">🚊 {t("carto.popup_troncon_titre", lang, mode="TRAM")}</b><br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
                    <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
                    <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_passages}</b> {int(freq)}<br>
                    <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
                    <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
                </div>
                """

                # Épaisseur proportionnelle à la fréquence
                weight = 2 + (freq - vmin_tram) / (vmax_tram - vmin_tram) * 6

                folium.PolyLine(
                    coords,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
                ).add_to(feature_group_tram)

            feature_group_tram.add_to(m)

            legende_items_html += f"""
            <div style="margin-bottom:8px;">
                <div style="font-size:11px;margin-bottom:2px;">🚊 {t("carto.legende_passages_mode", lang, mode="Tram")}</div>
                <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,#edf8e9,#bae4b3,#74c476,#31a354,#006d2c);"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
                    <span>{int(vmin_tram)}</span><span>{int(vmax_tram)}</span>
                </div>
            </div>"""



    # ===== TRONÇONS METRO =====
    if len(gdf_metro) > 0 and colonne_frequence in gdf_metro.columns:
        # Filtrer les tronçons avec passages
        gdf_metro_actif = gdf_metro[gdf_metro[colonne_frequence] > 0].copy()

        if len(gdf_metro_actif) > 0:
            # Créer la palette de couleurs pour les trams
            vmin_metro = gdf_metro_actif[colonne_frequence].min()
            vmax_metro = gdf_metro_actif[colonne_frequence].max()

            colormap_metro = cm.LinearColormap(
                colors=["#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08306b"],
                vmin=vmin_metro,
                vmax=vmax_metro,
                caption=t("carto.caption_passages_mode", lang, mode="Metro"),
            )

            # Créer un groupe de features pour les trams
            feature_group_metro = folium.FeatureGroup(name="🚇 Metro", show=True)

            # Ajouter chaque tronçon tram
            for idx, row in gdf_metro_actif.iterrows():
                freq = row[colonne_frequence]
                color = colormap_metro(freq)

                # Extraire les coordonnées de la géométrie
                coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

                # Créer le popup avec les informations
                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <b style="color: #28a745;">🚇 {t("carto.popup_troncon_titre", lang, mode="METRO")}</b><br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
                    <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
                    <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_passages}</b> {int(freq)}<br>
                    <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
                    <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
                </div>
                """

                # Épaisseur proportionnelle à la fréquence
                # Épaisseur de base plus élevée que bus/tram : le métro reste
                # visuellement au moins aussi épais même quand sa fréquence
                # (comparée aux autres tronçons métro) est la plus faible.
                weight = 12 + (freq - vmin_metro) / (vmax_metro - vmin_metro) * 2

                folium.PolyLine(
                    coords,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
                ).add_to(feature_group_metro)

            feature_group_metro.add_to(m)

            legende_items_html += f"""
            <div style="margin-bottom:8px;">
                <div style="font-size:11px;margin-bottom:2px;">🚇 {t("carto.legende_passages_mode", lang, mode="Metro")}</div>
                <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,#9ecae1,#6baed6,#4292c6,#2171b5,#08306b);"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
                    <span>{int(vmin_metro)}</span><span>{int(vmax_metro)}</span>
                </div>
            </div>"""



    # ===== TRONÇONS TROLLEY =====
    if len(gdf_trolley) > 0 and colonne_frequence in gdf_trolley.columns:
        # Filtrer les tronçons avec passages
        gdf_trolley_actif = gdf_trolley[gdf_trolley[colonne_frequence] > 0].copy()

        if len(gdf_trolley_actif) > 0:
            # Créer la palette de couleurs pour les trams
            vmin_trolley = gdf_trolley_actif[colonne_frequence].min()
            vmax_trolley = gdf_trolley_actif[colonne_frequence].max()

            colormap_trolley = cm.LinearColormap(
                colors=["#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08306b"],
                vmin=vmin_trolley,
                vmax=vmax_trolley,
                caption=t("carto.caption_passages_mode", lang, mode="Trolley"),
            )

            # Créer un groupe de features pour les trams
            feature_group_trolley = folium.FeatureGroup(name="🚎 Trolley", show=True)

            # Ajouter chaque tronçon tram
            for idx, row in gdf_trolley_actif.iterrows():
                freq = row[colonne_frequence]
                color = colormap_trolley(freq)

                # Extraire les coordonnées de la géométrie
                coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

                # Créer le popup avec les informations
                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <b style="color: #28a745;">🚎 {t("carto.popup_troncon_titre", lang, mode="TROLLEY")}</b><br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
                    <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
                    <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_passages}</b> {int(freq)}<br>
                    <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
                    <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
                </div>
                """

                # Épaisseur proportionnelle à la fréquence
                # Épaisseur de base plus élevée que bus/tram : le trolley reste
                # visuellement au moins aussi épais même quand sa fréquence
                # (comparée aux autres tronçons métro) est la plus faible.
                weight = 4 + (freq - vmin_trolley) / (vmax_trolley - vmin_trolley) * 2

                folium.PolyLine(
                    coords,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
                ).add_to(feature_group_trolley)

            feature_group_trolley.add_to(m)

            legende_items_html += f"""
            <div style="margin-bottom:8px;">
                <div style="font-size:11px;margin-bottom:2px;">🚎 {t("carto.legende_passages_mode", lang, mode="Trolley")}</div>
                <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,#9ecae1,#6baed6,#4292c6,#2171b5,#08306b);"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
                    <span>{int(vmin_trolley)}</span><span>{int(vmax_trolley)}</span>
                </div>
            </div>"""


    # ===== TRONÇONS FERRY =====
    if len(gdf_ferry) > 0 and colonne_frequence in gdf_ferry.columns:
        # Filtrer les tronçons avec passages
        gdf_ferry_actif = gdf_ferry[gdf_ferry[colonne_frequence] > 0].copy()

        if len(gdf_ferry_actif) > 0:
            # Créer la palette de couleurs pour les ferry
            vmin_ferry = gdf_ferry_actif[colonne_frequence].min()
            vmax_ferry = gdf_ferry_actif[colonne_frequence].max()

            colormap_ferry = cm.LinearColormap(
                colors=["#eff3ff", "#c6dbef", "#9ecae1", "#6baed6", "#2171b5"],
                vmin=vmin_ferry,
                vmax=vmax_ferry,
                caption=t("carto.caption_passages_mode", lang, mode="Ferry"),
            )

            # Créer un groupe de features pour les ferry
            feature_group_ferry = folium.FeatureGroup(name="⛴️ Ferry", show=True)

            # Ajouter chaque tronçon ferry
            for idx, row in gdf_ferry_actif.iterrows():
                freq = row[colonne_frequence]
                color = colormap_ferry(freq)

                # Extraire les coordonnées de la géométrie
                coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

                # Créer le popup avec les informations
                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <b style="color: #d63447;">⛴️ {t("carto.popup_troncon_titre", lang, mode="FERRY")}</b><br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
                    <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
                    <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_passages}</b> {int(freq)}<br>
                    <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
                    <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
                </div>
                """

                # Épaisseur proportionnelle à la fréquence
                weight = 2 + (freq - vmin_ferry) / (vmax_ferry - vmin_ferry) * 6

                folium.PolyLine(
                    coords,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
                ).add_to(feature_group_ferry)

            feature_group_ferry.add_to(m)

            legende_items_html += f"""
            <div style="margin-bottom:8px;">
                <div style="font-size:11px;margin-bottom:2px;">⛴️ {t("carto.legende_passages_mode", lang, mode="Ferry")}</div>
                <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,#eff3ff, #c6dbef, #9ecae1, #6baed6, #2171b5);"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
                    <span>{int(vmin_ferry)}</span><span>{int(vmax_ferry)}</span>
                </div>
            </div>"""

    # ===== TRONÇONS TRAIN (RER, Transilien, TER...) =====
    if len(gdf_train) > 0 and colonne_frequence in gdf_train.columns:
        # Filtrer les tronçons avec passages
        gdf_train_actif = gdf_train[gdf_train[colonne_frequence] > 0].copy()

        if len(gdf_train_actif) > 0:
            # Créer la palette de couleurs pour les trains
            vmin_train = gdf_train_actif[colonne_frequence].min()
            vmax_train = gdf_train_actif[colonne_frequence].max()

            colormap_train = cm.LinearColormap(
                colors=["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
                vmin=vmin_train,
                vmax=vmax_train,
                caption=t("carto.caption_passages_mode", lang, mode="Train"),
            )

            # Créer un groupe de features pour les trains
            feature_group_train = folium.FeatureGroup(name="🚆 Train", show=True)

            # Ajouter chaque tronçon train
            for idx, row in gdf_train_actif.iterrows():
                freq = row[colonne_frequence]
                color = colormap_train(freq)

                # Extraire les coordonnées de la géométrie
                coords = [(coord[1], coord[0]) for coord in row["geometry"].coords]

                # Créer le popup avec les informations
                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <b style="color: #54278f;">🚆 {t("carto.popup_troncon_titre", lang, mode="TRAIN")}</b><br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_id}</b> {row.get('troncon_unique_id', 'N/A')}<br>
                    <b>{popup_de}</b> {row.get('stop_depart_name', 'N/A')}<br>
                    <b>{popup_a}</b> {row.get('stop_arrivee_name', 'N/A')}<br>
                    <hr style="margin: 5px 0;">
                    <b>{popup_passages}</b> {int(freq)}<br>
                    <b>{popup_vitesse}</b> {row.get('vitesse_moyenne_kmh', 0):.1f} km/h<br>
                    <b>{popup_distance}</b> {row.get('distance_km', 0):.2f} km
                </div>
                """

                # Épaisseur proportionnelle à la fréquence
                weight = 2 + (freq - vmin_train) / (vmax_train - vmin_train) * 6

                folium.PolyLine(
                    coords,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{row.get('stop_depart_name', '')} → {row.get('stop_arrivee_name', '')}: {int(freq)} {suffixe_passages}",
                ).add_to(feature_group_train)

            feature_group_train.add_to(m)

            legende_items_html += f"""
            <div style="margin-bottom:8px;">
                <div style="font-size:11px;margin-bottom:2px;">🚆 {t("carto.legende_passages_mode", lang, mode="Train")}</div>
                <div style="width:180px;height:10px;border-radius:3px;background:linear-gradient(to right,#f2f0f7,#cbc9e2,#9e9ac8,#756bb1,#54278f);"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:#555;">
                    <span>{int(vmin_train)}</span><span>{int(vmax_train)}</span>
                </div>
            </div>"""

    # ===== COUCHES SUPPLÉMENTAIRES (optionnelles, fournies par l'appelant) =====
    # 5e élément du tuple : multiplicateur d'épaisseur de trait (1 par défaut),
    # ex. 2 pour le RER afin de le distinguer visuellement sur la carte.
    for gdf_extra, nom_couche, emoji_extra, colors_extra, poids_trait in (couches_supplementaires or []):
        legende_items_html = _ajouter_couche_troncons_generique(
            m, gdf_extra, nom_couche, emoji_extra, colors_extra, colonne_frequence, lang,
            legende_items_html, popup_id, popup_de, popup_a, popup_passages,
            popup_vitesse, popup_distance, suffixe_passages,
            weight_base=2 * poids_trait, weight_amplitude=6 * poids_trait,
        )

    # Ajouter le contrôle des couches (cases à cocher)
    folium.LayerControl(collapsed=False).add_to(m)

    # Ajouter un bouton plein écran
    plugins.Fullscreen(
        position="topright",
        title=t("carto.plein_ecran", lang),
        title_cancel=t("carto.quitter_plein_ecran", lang),
        force_separate_button=True,
    ).add_to(m)

    # Ajouter la mesure de distance
    plugins.MeasureControl(
        position="topleft",
        primary_length_unit="kilometers",
        secondary_length_unit="meters",
    ).add_to(m)

    # Titre de la carte, en haut au centre
    if nom_reseau_str:
        titre_carte = t("carto.titre_reseau_troncons", lang, reseau=nom_reseau_str)
        titre_html = f"""
        <div style="
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999;
            background: white;
            padding: 10px 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            font-family: Arial, Helvetica, sans-serif;
            white-space: nowrap;
        ">
            <div style="font-size: 16px; font-weight: bold; color: #01B1EC;">{titre_carte}</div>
            <p style="margin: 4px 0 0; font-size: 12px; font-weight: normal; color: #555;">{date_service_str}</p>
        </div>
        """
        m.get_root().html.add_child(folium.Element(titre_html))

    # Légende des échelles de couleur, en bas à gauche
    if legende_items_html:
        legende_html = f"""
        <div style="
            position: fixed;
            bottom: 30px;
            left: 30px;
            z-index: 9999;
            background: white;
            padding: 10px 14px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            font-family: Arial, Helvetica, sans-serif;
            color: #333;
        ">
            {legende_items_html}
        </div>
        """
        m.get_root().html.add_child(folium.Element(legende_html))

    # Logo du réseau, en bas à droite de la carte
    if chemin_logo and os.path.isfile(chemin_logo):
        type_mime = mimetypes.guess_type(chemin_logo)[0] or "image/png"
        with open(chemin_logo, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")
        logo_html = f"""
        <div style="
            position: fixed;
            bottom: 30px;
            right: 30px;
            z-index: 9999;
            background: white;
            padding: 8px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        ">
            <img src="data:{type_mime};base64,{logo_base64}" style="height:48px;display:block;">
        </div>
        """
        m.get_root().html.add_child(folium.Element(logo_html))

    m.save(output_path)

    return m
