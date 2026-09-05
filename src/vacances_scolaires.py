"""
Vacances scolaires françaises (métropole) par académie, et détermination de
l'académie/zone d'un réseau GTFS à partir de la position de ses arrêts — sert
à choisir une date JOB (cf. dates_service, src/info_reseau.py) qui ne tombe
pas en période de vacances scolaires : le service GTFS y est souvent allégé,
non représentatif d'un jour de référence "ouvré" pour l'analyse
d'accessibilité.

Données statiques dans data/ :
- vacances_scolaires.csv : academie, zone, description, date_debut, date_fin,
  annee_scolaire — source data.education.gouv.fr (API
  fr-en-calendrier-scolaire), quelques années récentes/à venir.
- departements_academies.csv : code_departement, departement, academie —
  association stable (ne change pas d'une année scolaire à l'autre),
  construite à partir de la liste des académies métropolitaines.
"""

import os
import time

import pandas as pd
import requests

VACANCES_CSV = os.path.join("data", "vacances_scolaires.csv")
DEPARTEMENTS_ACADEMIES_CSV = os.path.join("data", "departements_academies.csv")


def charger_vacances():
    """DataFrame des périodes de vacances (colonnes : academie, zone,
    description, date_debut, date_fin, annee_scolaire — dates au format
    YYYY-MM-DD). DataFrame vide si le CSV est absent."""
    if not os.path.exists(VACANCES_CSV):
        return pd.DataFrame(columns=["academie", "zone", "description", "date_debut", "date_fin", "annee_scolaire"])
    return pd.read_csv(VACANCES_CSV, dtype=str)


def charger_departements_academies():
    """dict code_departement ("01".."2B"..."95") -> nom d'académie."""
    if not os.path.exists(DEPARTEMENTS_ACADEMIES_CSV):
        return {}
    df = pd.read_csv(DEPARTEMENTS_ACADEMIES_CSV, dtype=str)
    return dict(zip(df["code_departement"], df["academie"]))


def zone_pour_academie(academie):
    """Zone (ex: "Zone B", "Corse") de l'académie donnée, d'après la première
    ligne correspondante de vacances_scolaires.csv — None si académie
    inconnue du CSV (ex: CSV pas encore régénéré pour une nouvelle académie,
    ne devrait pas arriver en pratique)."""
    vacances = charger_vacances()
    lignes = vacances[vacances["academie"] == academie]
    return lignes["zone"].iloc[0] if not lignes.empty else None


def departement_academie_zone_pour_feed(feed, timeout=10, tentatives=3):
    """Détermine (code_departement, academie, zone) d'un réseau GTFS à
    partir du barycentre de ses arrêts — un appel HTTP (reverse géocodage) à
    geo.api.gouv.fr, rapide, contrairement au géocodage complet arrêt par
    arrêt de build_data_agglo.codes_communes_via_api (fait plus tard dans le
    pipeline, seulement si l'analyse d'accessibilité est lancée). Suffisant
    ici : la vraisemblance qu'un réseau urbain chevauche deux académies est
    négligeable.

    Répète l'appel jusqu'à `tentatives` fois (courte pause entre chaque) :
    un aléa réseau ponctuel (timeout côté Space...) sur ce seul appel ne
    doit pas désactiver silencieusement l'évitement des vacances scolaires
    pour tout le calcul de date_JOB qui suit (cf. dates_service).

    (None, None, None) si hors métropole ou API injoignable après toutes
    les tentatives (reste best-effort, jamais bloquant)."""
    stops = feed.stops
    lat = stops["stop_lat"].astype(float).mean()
    lon = stops["stop_lon"].astype(float).mean()
    resultats = None
    for essai in range(tentatives):
        try:
            reponse = requests.get(
                "https://geo.api.gouv.fr/communes",
                params={"lat": lat, "lon": lon, "fields": "departement"},
                timeout=timeout,
            )
            reponse.raise_for_status()
            resultats = reponse.json()
            break
        except requests.RequestException:
            if essai < tentatives - 1:
                time.sleep(1)
    else:
        return None, None, None

    if not resultats or not resultats[0].get("departement"):
        return None, None, None

    code_departement = resultats[0]["departement"]["code"]
    academie = charger_departements_academies().get(code_departement)
    if academie is None:
        return code_departement, None, None
    return code_departement, academie, zone_pour_academie(academie)


def est_en_vacances(date_str_yyyymmdd, academie):
    """True si date_str_yyyymmdd (format GTFS "YYYYMMDD", cf.
    feed.get_dates()) tombe dans une période de vacances scolaires connue de
    cette académie — n'importe quelle année scolaire du CSV : les périodes
    (Toussaint, Noël, Hiver, Printemps, Été) sont récurrentes d'une année sur
    l'autre pour une même zone, pas besoin de faire correspondre l'année
    scolaire exacte du GTFS à une ligne précise du CSV."""
    date_iso = f"{date_str_yyyymmdd[:4]}-{date_str_yyyymmdd[4:6]}-{date_str_yyyymmdd[6:8]}"
    vacances = charger_vacances()
    lignes = vacances[vacances["academie"] == academie]
    return bool(((lignes["date_debut"] <= date_iso) & (date_iso <= lignes["date_fin"])).any())
