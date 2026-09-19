"""
Contrôle qualité des rencontres ingérées.

Un moteur d'analyse ne vaut que ce que valent ses entrées. Ce module refuse ou
signale les rencontres invraisemblables **avant** qu'elles n'atteignent le
calcul, puis le snapshot, puis l'utilisateur.

Chaque anomalie est nommée : une rencontre écartée est traçable, jamais
silencieuse.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# Deux rencontres d'une même équipe ne peuvent pas se chevaucher.
ECART_MIN_ENTRE_MATCHS = timedelta(hours=5)
# Bornes de plausibilité d'un score de football.
BUTS_MAX = 15
# Bornes d'une ligne de totaux crédible.
LIGNE_MIN, LIGNE_MAX = 0.5, 8.5


class Anomalie:
    """Un défaut constaté sur une rencontre."""

    __slots__ = ('cle', 'code', 'detail')

    def __init__(self, cle: str, code: str, detail: str = '') -> None:
        self.cle = cle
        self.code = code
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f'<Anomalie {self.code} {self.cle} {self.detail}>'

    def texte(self) -> str:
        return f'{self.code}: {self.detail}' if self.detail else self.code


def _dt(valeur: Any) -> datetime | None:
    if isinstance(valeur, datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)
    try:
        s = str(valeur).replace('Z', '+00:00')
        d = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def verifier_match(m: dict[str, Any]) -> list[Anomalie]:
    """Défauts internes à une rencontre, sans regarder les autres."""
    cle = m.get('cle') or ''
    out: list[Anomalie] = []

    dom, ext = m.get('domicile_slug'), m.get('exterieur_slug')
    if not dom or not ext:
        out.append(Anomalie(cle, 'equipe_manquante'))
    elif dom == ext:
        out.append(Anomalie(cle, 'equipe_contre_elle_meme', dom))

    if _dt(m.get('coup_denvoi')) is None:
        out.append(Anomalie(cle, 'date_illisible', str(m.get('coup_denvoi'))))

    for champ in ('buts_dom', 'buts_ext', 'buts_dom_mt', 'buts_ext_mt'):
        v = m.get(champ)
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            out.append(Anomalie(cle, 'score_illisible', f'{champ}={v!r}'))
            continue
        if n < 0 or n > BUTS_MAX:
            out.append(Anomalie(cle, 'score_aberrant', f'{champ}={n}'))

    bd, be = m.get('buts_dom'), m.get('buts_ext')
    bdm, bem = m.get('buts_dom_mt'), m.get('buts_ext_mt')
    if None not in (bd, be, bdm, bem):
        if int(bdm) > int(bd) or int(bem) > int(be):
            out.append(
                Anomalie(cle, 'mi_temps_incoherente', f'{bdm}-{bem} > {bd}-{be}')
            )
    return out


def verifier_cotes(
    cotes_1x2: tuple[float, float, float] | None,
    ligne: float | None = None,
    prix_ou: tuple[float, float] | None = None,
    *,
    marge_max: float = 0.35,
) -> list[str]:
    """Défauts d'un jeu de cotes. Renvoie une liste de codes d'anomalie."""
    out: list[str] = []
    if cotes_1x2:
        try:
            c1, cn, c2 = (float(x) for x in cotes_1x2)
        except (TypeError, ValueError):
            return ['cotes_illisibles']
        if min(c1, cn, c2) < 1.01 or max(c1, cn, c2) > 1000:
            out.append('cote_hors_bornes')
        else:
            marge = 1 / c1 + 1 / cn + 1 / c2 - 1
            if marge < -1e-6:
                out.append('marge_negative')
            elif marge > marge_max:
                out.append('marge_excessive')
            # Un nul plus probable qu'un camp est un signe de flux corrompu.
            if cn < min(c1, c2):
                out.append('nul_favori')
    if ligne is not None:
        try:
            lg = float(ligne)
        except (TypeError, ValueError):
            out.append('ligne_illisible')
        else:
            if not (LIGNE_MIN <= lg <= LIGNE_MAX):
                out.append('ligne_hors_bornes')
            elif (lg * 4) % 1 != 0:
                out.append('ligne_non_standard')
    if prix_ou:
        try:
            o, u = float(prix_ou[0]), float(prix_ou[1])
        except (TypeError, ValueError):
            out.append('prix_ou_illisibles')
        else:
            if min(o, u) < 1.01 or max(o, u) > 100:
                out.append('prix_ou_hors_bornes')
            elif 1 / o + 1 / u - 1 > marge_max:
                out.append('marge_ou_excessive')
    return out


def conflits_calendrier(matchs: Iterable[dict[str, Any]]) -> list[Anomalie]:
    """Une même équipe engagée deux fois dans un intervalle impossible.

    C'est le filet qui attrape les identités mal résolues : si deux clubs
    distincts ont été fusionnés par erreur, leurs calendriers se télescopent.
    """
    par_equipe: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for m in matchs:
        if (m.get('statut') or '') == 'reporte':
            continue
        quand = _dt(m.get('coup_denvoi'))
        if quand is None:
            continue
        cle = m.get('cle') or ''
        for slug in (m.get('domicile_slug'), m.get('exterieur_slug')):
            if slug:
                par_equipe[slug].append((quand, cle))

    out: list[Anomalie] = []
    vus: set[tuple[str, str]] = set()
    for slug, rencontres in par_equipe.items():
        rencontres.sort()
        for (t1, c1), (t2, c2) in zip(rencontres, rencontres[1:]):
            if c1 == c2 or t2 - t1 >= ECART_MIN_ENTRE_MATCHS:
                continue
            paire = tuple(sorted((c1, c2)))
            if paire in vus:
                continue
            vus.add(paire)
            heures = (t2 - t1).total_seconds() / 3600
            out.append(
                Anomalie(c2, 'chevauchement_calendrier', f'{slug} ~{heures:.1f} h après {c1}')
            )
    return out


def doublons_residuels(matchs: Iterable[dict[str, Any]]) -> list[Anomalie]:
    """Deux clés différentes pour la même affiche à moins d'un jour d'écart.

    Ne devrait plus arriver une fois l'identité canonique en place ; sert de
    détecteur de régression.
    """
    par_affiche: dict[tuple[str, str, str], list[tuple[datetime, str]]] = defaultdict(list)
    for m in matchs:
        quand = _dt(m.get('coup_denvoi'))
        dom, ext = m.get('domicile_slug'), m.get('exterieur_slug')
        if quand is None or not dom or not ext:
            continue
        par_affiche[(m.get('competition_code') or '', dom, ext)].append(
            (quand, m.get('cle') or '')
        )
    out: list[Anomalie] = []
    for (_code, dom, ext), items in par_affiche.items():
        items.sort()
        for (t1, c1), (t2, c2) in zip(items, items[1:]):
            if c1 != c2 and t2 - t1 < timedelta(hours=24):
                out.append(
                    Anomalie(c2, 'doublon_residuel', f'{dom} vs {ext}, aussi {c1}')
                )
    return out


def auditer(matchs: list[dict[str, Any]]) -> dict[str, list[Anomalie]]:
    """Audit complet. Renvoie les anomalies groupées par clé de match."""
    par_cle: dict[str, list[Anomalie]] = defaultdict(list)
    for m in matchs:
        for a in verifier_match(m):
            par_cle[a.cle].append(a)
    for a in conflits_calendrier(matchs):
        par_cle[a.cle].append(a)
    for a in doublons_residuels(matchs):
        par_cle[a.cle].append(a)
    return dict(par_cle)


# Anomalies qui interdisent d'exposer la rencontre à l'utilisateur.
BLOQUANTES = frozenset({
    'equipe_manquante',
    'equipe_contre_elle_meme',
    'date_illisible',
    'score_aberrant',
    'score_illisible',
    'chevauchement_calendrier',
    'doublon_residuel',
})


def est_bloquant(anomalies: Iterable[Anomalie]) -> bool:
    return any(a.code in BLOQUANTES for a in anomalies)
