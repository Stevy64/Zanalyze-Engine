"""
Boucle d'auto-amélioration.

Principe
--------
Le moteur ne se règle pas à la main. Il compare ce qu'il a annoncé à ce qui
s'est produit, en tire des courbes de correction, puis **ne les publie que si
elles gagnent hors échantillon**. Une version candidate qui ne fait pas mieux
que celle en place est abandonnée sans bruit.

Trois garde-fous, tirés des erreurs constatées sur la journée 1 de Ligue des
champions :

1. **Validation chronologique.** On apprend sur le passé, on teste sur
   l'avenir. Un tirage aléatoire laisserait fuir l'information : deux options
   d'un même match tomberaient des deux côtés de la coupure.
2. **Volume minimal.** En dessous du seuil, aucune courbe n'est produite.
   Un signal mesuré sur 12 matchs s'était évaporé au 18ᵉ.
3. **Rétrécissement vers l'identité.** Chaque point est tiré vers « ne rien
   corriger » proportionnellement à son manque d'observations. Une case peu
   peuplée ne peut pas déplacer le moteur.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from engine.calibrage import COMPLEMENT, SEP_LIGUE, cle_marche_depuis_code
from engine.evaluation import evaluer

# Volume minimal pour qu'une courbe soit publiée.
MIN_OBS_MARCHE = 400
MIN_OBS_LIGUE = 1200
MIN_OBS_CASE = 40
# Force du rétrécissement vers l'identité, en nombre d'observations fictives.
LISSAGE = 60.0
# Découpage des probabilités annoncées.
BORNES = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0)
# Part de l'historique réservée au test hors échantillon.
PART_TEST = 0.30
# Gain minimal de Brier pour qu'une candidate remplace la version en place.
GAIN_MINIMAL = 1e-4


class Observation:
    """Une option réglée : ce qui a été annoncé, ce qui s'est produit."""

    __slots__ = ('marche', 'complement', 'ligue', 'p', 'y', 'quand')

    def __init__(self, marche: str, complement: bool, ligue: str,
                 p: float, y: int, quand: str) -> None:
        self.marche = marche
        self.complement = complement
        self.ligue = ligue
        self.p = p
        self.y = y
        self.quand = quand

    @property
    def p_directe(self) -> float:
        """Probabilité ramenée dans le sens de la courbe du marché.

        « Moins de 4 buts » est le complément de « plus de 3,5 » : une seule
        courbe est ajustée, l'autre sens en est déduit. C'est ce qui garantit
        que les deux options somment toujours à 100 %.
        """
        return 1.0 - self.p if self.complement else self.p

    @property
    def y_direct(self) -> int:
        return 1 - self.y if self.complement else self.y


def _cle_et_sens(code: str) -> tuple[str, bool] | None:
    mk = cle_marche_depuis_code(code)
    if mk is None:
        return None
    if isinstance(mk, tuple) and mk and mk[0] == COMPLEMENT:
        return str(mk[1]), True
    if isinstance(mk, str):
        return mk, False
    return None


def collecter_observations(conn: sqlite3.Connection) -> list[Observation]:
    """Croise les prédictions figées avant match avec les résultats.

    Seules les prédictions **antérieures au coup d'envoi** comptent : une
    analyse recalculée après coup n'est pas une prédiction.
    """
    lignes = conn.execute(
        """SELECT p.cle, p.payload, p.calcule_le,
                  r.buts_dom, r.buts_ext, r.buts_dom_mt, r.buts_ext_mt,
                  r.competition_code, r.coup_denvoi
             FROM predictions p
             JOIN resultats r ON r.cle = p.cle
            WHERE p.avant_match = 1
            ORDER BY r.coup_denvoi"""
    ).fetchall()

    # Une seule prédiction par match : la dernière avant le coup d'envoi.
    derniere: dict[str, sqlite3.Row] = {}
    for r in lignes:
        courant = derniere.get(r['cle'])
        if courant is None or r['calcule_le'] > courant['calcule_le']:
            derniere[r['cle']] = r

    out: list[Observation] = []
    for r in derniere.values():
        try:
            payload = json.loads(r['payload'])
        except json.JSONDecodeError:
            continue
        ligue = r['competition_code'] or ''
        quand = r['coup_denvoi'] or ''
        for o in payload.get('options') or []:
            code = o.get('code')
            p = o.get('p_brute', o.get('probabilite'))
            if not code or p is None:
                continue
            # Une probabilité lue sur le marché n'a pas à être corrigée :
            # la corriger reviendrait à apprendre le bruit du bookmaker.
            if o.get('origine') != 'calcul':
                continue
            sens = _cle_et_sens(code)
            if sens is None:
                continue
            try:
                gagne = evaluer(
                    code, r['buts_dom'], r['buts_ext'],
                    r['buts_dom_mt'], r['buts_ext_mt'],
                )
            except (ValueError, TypeError):
                continue
            if gagne is None:
                continue
            out.append(
                Observation(sens[0], sens[1], ligue, float(p), 1 if gagne else 0, quand)
            )
    out.sort(key=lambda ob: ob.quand)
    return out


def _isotonise(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Rend la courbe croissante (pool adjacent violators, pondéré à l'unité).

    Une courbe de calibration décroissante n'a pas de sens : annoncer plus
    ne peut pas produire moins.
    """
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    poids = [1.0] * len(ys)
    i = 0
    while i < len(ys) - 1:
        if ys[i] <= ys[i + 1] + 1e-12:
            i += 1
            continue
        total = poids[i] + poids[i + 1]
        moyenne = (ys[i] * poids[i] + ys[i + 1] * poids[i + 1]) / total
        ys[i:i + 2] = [moyenne]
        poids[i:i + 2] = [total]
        xs[i:i + 2] = [(xs[i] + xs[i + 1]) / 2]
        i = max(i - 1, 0)
    return list(zip(xs, ys))


def courbe(observations: Sequence[Observation]) -> list[tuple[float, float]] | None:
    """Construit une courbe annoncé → observé, lissée et croissante."""
    if len(observations) < MIN_OBS_MARCHE:
        return None
    cases: dict[int, list[Observation]] = defaultdict(list)
    for ob in observations:
        p = ob.p_directe
        for k in range(len(BORNES) - 1):
            if BORNES[k] <= p < BORNES[k + 1] or (k == len(BORNES) - 2 and p == 1.0):
                cases[k].append(ob)
                break

    points: list[tuple[float, float]] = []
    for k in sorted(cases):
        lot = cases[k]
        if len(lot) < MIN_OBS_CASE:
            continue
        p_moy = sum(ob.p_directe for ob in lot) / len(lot)
        observe = sum(ob.y_direct for ob in lot) / len(lot)
        # Rétrécissement : peu d'observations → on reste près de l'identité.
        n = len(lot)
        corrige = (n * observe + LISSAGE * p_moy) / (n + LISSAGE)
        points.append((round(p_moy, 4), round(corrige, 4)))

    if len(points) < 3:
        return None
    points = _isotonise(points)
    # Ancrages : une probabilité nulle reste nulle, une certitude reste certaine.
    if points[0][0] > 0.02:
        points.insert(0, (0.0, 0.0))
    if points[-1][0] < 0.98:
        points.append((1.0, 1.0))
    return [(round(x, 4), round(min(max(y, 0.001), 0.999), 4)) for x, y in points]


def _interp(p: float, table: Sequence[tuple[float, float]]) -> float:
    xs = [a for a, _ in table]
    ys = [b for _, b in table]
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    for k in range(len(xs) - 1):
        if xs[k] <= p <= xs[k + 1]:
            if xs[k + 1] - xs[k] < 1e-12:
                return ys[k]
            t = (p - xs[k]) / (xs[k + 1] - xs[k])
            return ys[k] + t * (ys[k + 1] - ys[k])
    return ys[-1]


def _applique(ob: Observation, tables: dict) -> float:
    cle = f'{ob.ligue}{SEP_LIGUE}{ob.marche}'
    table = tables.get(cle) or tables.get(ob.marche)
    if not table:
        return ob.p
    corrige = _interp(ob.p_directe, table)
    return 1.0 - corrige if ob.complement else corrige


def brier(observations: Iterable[Observation], tables: dict) -> float:
    obs = list(observations)
    if not obs:
        return float('nan')
    return sum((_applique(ob, tables) - ob.y) ** 2 for ob in obs) / len(obs)


def log_loss(observations: Iterable[Observation], tables: dict) -> float:
    obs = list(observations)
    if not obs:
        return float('nan')
    total = 0.0
    for ob in obs:
        p = min(max(_applique(ob, tables), 1e-6), 1 - 1e-6)
        total -= math.log(p) if ob.y else math.log(1 - p)
    return total / len(obs)


def apprendre(observations: Sequence[Observation]) -> dict[str, list]:
    """Courbes candidates : générales, puis par ligue quand le volume suit."""
    par_marche: dict[str, list[Observation]] = defaultdict(list)
    par_ligue: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for ob in observations:
        par_marche[ob.marche].append(ob)
        if ob.ligue:
            par_ligue[(ob.ligue, ob.marche)].append(ob)

    tables: dict[str, list] = {}
    for marche, lot in par_marche.items():
        c = courbe(lot)
        if c:
            tables[marche] = [list(pt) for pt in c]
    for (ligue, marche), lot in par_ligue.items():
        if len(lot) < MIN_OBS_LIGUE:
            continue
        c = courbe(lot)
        if c:
            tables[f'{ligue}{SEP_LIGUE}{marche}'] = [list(pt) for pt in c]
    return tables


# Courbe neutre : « ne rien corriger ». Publiée explicitement pour **annuler**
# une courbe par défaut devenue nuisible — sans elle, une table héritée que
# les données contredisent resterait active indéfiniment.
IDENTITE = [[0.0, 0.0], [1.0, 1.0]]
# Observations minimales pour trancher entre deux courbes sur un marché.
MIN_OBS_ARBITRAGE = 150
# Gain minimal d'erreur de calibration, en points, pour adopter une courbe.
# Réglé sur douze mois puis confirmé sur les mois suivants : 0 et 0,05 sont
# indiscernables, 0,25 est nettement moins bon. On garde 0,05, qui évite de
# republier une courbe à chaque cycle pour un gain nul. Le vrai garde-fou
# n'est pas cette marge mais la contrainte de Brier ci-dessous.
MARGE_ECE = 0.05
# Dégradation de Brier au-delà de laquelle une courbe est refusée malgré tout.
# Réglé puis confirmé sur deux périodes disjointes : 1e-3 divise l'erreur de
# calibration là où elle est forte pour un coût de Brier de 0,6 %, invisible
# en pratique. Plus serré (1e-4), le garde-fou refusait des courbes qui
# ramenaient l'erreur de 2,1 à 1,0 point.
TOLERANCE_BRIER = 1e-3
# Découpage utilisé pour mesurer l'erreur de calibration.
BORNES_ECE = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)
MIN_OBS_CASE_ECE = 25


def _probas(observations: Sequence[Observation], table) -> list[tuple[float, int]]:
    out = []
    for ob in observations:
        p = _interp(ob.p_directe, table) if table else ob.p_directe
        p = 1.0 - p if ob.complement else p
        out.append((min(max(p, 1e-6), 1 - 1e-6), ob.y))
    return out


def _brier_marche(observations: Sequence[Observation], table) -> float:
    paires = _probas(observations, table)
    return sum((p - y) ** 2 for p, y in paires) / max(len(paires), 1)


def erreur_calibration(observations: Sequence[Observation], table=None) -> float:
    """Écart moyen entre annoncé et observé, en points, pondéré par effectif.

    C'est **la** mesure qui compte pour l'utilisateur : elle dit si « 74 % »
    veut bien dire 74 %. Le score de Brier, lui, est dominé par le pouvoir
    séparateur et bouge à peine quand la calibration dérive de trois points.
    Mesuré sur trois saisons : une courbe apprise ramène l'erreur de
    « extérieur marque » de 3,04 à 1,39 point, pour un gain de Brier de
    0,0006 — invisible si l'on ne regarde que le Brier.
    """
    paires = _probas(observations, table)
    n = len(paires)
    if not n:
        return 0.0
    cases: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in paires:
        for k in range(len(BORNES_ECE) - 1):
            if BORNES_ECE[k] <= p < BORNES_ECE[k + 1]:
                cases[k].append((p, y))
                break
    total = 0.0
    for lot in cases.values():
        if len(lot) < MIN_OBS_CASE_ECE:
            continue
        annonce = sum(p for p, _ in lot) / len(lot)
        observe = sum(y for _, y in lot) / len(lot)
        total += (len(lot) / n) * abs(annonce - observe)
    return 100 * total


def selectionner_courbes(
    candidates: dict[str, list], validation: Sequence[Observation], actuelles: dict,
) -> dict[str, list]:
    """Garde, marché par marché, la courbe qui gagne sur la validation.

    Trois prétendantes par marché : la courbe qui vient d'être apprise, celle
    déjà en place, et l'absence de correction. La validation est postérieure
    à l'apprentissage et antérieure au test : aucune des deux n'est juge et
    partie.

    Le critère est l'**erreur de calibration**, pas le score de Brier : une
    dérive de trois points sur les pourcentages annoncés ne déplace le Brier
    que de quelques dix-millièmes, alors qu'elle change tout pour qui lit
    « 74 % ». Un garde-fou reste en place : une courbe qui dégraderait le
    Brier est refusée même si elle flatte la calibration.

    Quand l'absence de correction l'emporte sur une courbe déjà en place, on
    publie explicitement la courbe neutre. C'est ce qui permet au moteur de
    revenir sur un réglage hérité que les résultats démentent.
    """
    par_marche: dict[str, list[Observation]] = defaultdict(list)
    for ob in validation:
        par_marche[ob.marche].append(ob)

    retenues: dict[str, list] = {}
    for marche, lot in par_marche.items():
        if len(lot) < MIN_OBS_ARBITRAGE:
            continue
        en_place = actuelles.get(marche)
        prets = {
            'apprise': candidates.get(marche),
            'en_place': en_place,
            'neutre': None,
        }
        scores = {
            nom: (erreur_calibration(lot, table), _brier_marche(lot, table))
            for nom, table in prets.items()
            if nom != 'apprise' or table is not None
        }
        ece_neutre, brier_neutre = scores['neutre']
        # Référence : ce qui s'applique aujourd'hui si l'on ne publie rien.
        ece_ref, brier_ref = scores.get('en_place', scores['neutre'])

        gagnante, (ece_g, brier_g) = 'en_place' if 'en_place' in scores else 'neutre', (
            ece_ref, brier_ref)
        for nom in ('apprise', 'neutre'):
            if nom not in scores:
                continue
            ece_n, brier_n = scores[nom]
            if ece_n < ece_g - MARGE_ECE and brier_n <= brier_g + TOLERANCE_BRIER:
                gagnante, (ece_g, brier_g) = nom, (ece_n, brier_n)

        if gagnante == 'apprise':
            retenues[marche] = candidates[marche]
        elif gagnante == 'neutre' and en_place is not None:
            # La table en place nuit : on la neutralise explicitement.
            retenues[marche] = list(IDENTITE)
        # « en_place » gagnante : on n'écrit rien, le défaut continue de servir.

    # Les courbes par ligue suivent la décision prise sur leur marché.
    for cle, table in candidates.items():
        if SEP_LIGUE in cle and cle.partition(SEP_LIGUE)[2] in retenues:
            retenues[cle] = table
    return retenues


def _coupure_chronologique(observations: Sequence[Observation]) -> int:
    """Indice de coupure, aligné sur une frontière de date.

    Deux options du même match ne peuvent pas se retrouver de part et
    d'autre : ce serait une fuite d'information.
    """
    n = len(observations)
    cible = int(n * (1 - PART_TEST))
    if cible <= 0 or cible >= n:
        return cible
    jour = observations[cible].quand[:10]
    while cible < n and observations[cible].quand[:10] == jour:
        cible += 1
    return cible


def evaluer_candidate(observations: Sequence[Observation]) -> dict[str, Any]:
    """Compare la candidate à la version en place, hors échantillon."""
    coupure = _coupure_chronologique(observations)
    apprentissage_, test = observations[:coupure], observations[coupure:]
    rapport: dict[str, Any] = {
        'observations': len(observations),
        'apprentissage': len(apprentissage_),
        'test': len(test),
        'retenue': False,
        'raison': '',
    }
    if len(test) < MIN_OBS_MARCHE // 2 or len(apprentissage_) < MIN_OBS_MARCHE:
        rapport['raison'] = (
            f'volume insuffisant : {len(apprentissage_)} pour apprendre, '
            f'{len(test)} pour tester'
        )
        return rapport

    # Trois tranches chronologiques : on apprend sur la première, on arbitre
    # entre courbes sur la deuxième, on juge sur la troisième. Sans la
    # tranche d'arbitrage, la courbe choisie serait jugée sur les données qui
    # l'ont choisie.
    coupure_val = _coupure_chronologique(apprentissage_)
    ajustement, validation = apprentissage_[:coupure_val], apprentissage_[coupure_val:]
    actuelle_pour_tri = charger_tables_actives()
    brutes = apprendre(ajustement) if len(validation) >= MIN_OBS_ARBITRAGE else {}
    candidate = selectionner_courbes(brutes, validation, actuelle_pour_tri) if brutes else {}
    if not candidate:
        compte: dict[str, int] = defaultdict(int)
        for ob in apprentissage_:
            compte[ob.marche] += 1
        rapport['volume_par_marche'] = dict(sorted(compte.items()))
        if not brutes:
            proches = sorted(compte.items(), key=lambda kv: -kv[1])[:3]
            detail = ', '.join(f'{m} {n}/{MIN_OBS_MARCHE}' for m, n in proches)
            rapport['raison'] = (
                'aucun marché n’atteint encore le seuil de '
                f'{MIN_OBS_MARCHE} observations'
                + (f' (les plus avancés : {detail})' if detail else '')
            )
        else:
            # Cas bien différent : il y avait de quoi apprendre, mais sur la
            # tranche d'arbitrage aucune courbe nouvelle n'a battu celle déjà
            # en place. Ne rien publier est ici la bonne décision.
            rapport['raison'] = (
                f'{len(brutes)} courbe(s) apprise(s), mais aucune ne bat la '
                'version en place sur la tranche d’arbitrage : rien n’est publié'
            )
        return rapport

    actuelle = charger_tables_actives()
    sans = brier(test, {})
    avec_actuelle = brier(test, actuelle)
    avec_candidate = brier(test, candidate)
    rapport.update({
        'brier_sans_correction': round(sans, 5),
        'brier_version_actuelle': round(avec_actuelle, 5),
        'brier_candidate': round(avec_candidate, 5),
        'logloss_version_actuelle': round(log_loss(test, actuelle), 5),
        'logloss_candidate': round(log_loss(test, candidate), 5),
        'marches': sorted(candidate),
    })
    reference = min(avec_actuelle, sans)
    if avec_candidate < reference - GAIN_MINIMAL:
        rapport['retenue'] = True
        rapport['raison'] = (
            f'Brier {avec_candidate:.5f} contre {reference:.5f} pour la meilleure '
            'version en place'
        )
        rapport['tables'] = candidate
    else:
        rapport['raison'] = (
            f'aucun gain hors échantillon ({avec_candidate:.5f} contre '
            f'{reference:.5f}) : la version en place est conservée'
        )
    return rapport


def charger_tables_actives() -> dict:
    from engine.calibration_store import charger_tables
    return charger_tables()


def recalibrer(conn: sqlite3.Connection, *, publier: bool = True) -> dict[str, Any]:
    """Cycle complet : mesurer, apprendre, valider, publier si meilleur."""
    observations = collecter_observations(conn)
    rapport = evaluer_candidate(observations)
    rapport['publie'] = False
    if rapport.get('retenue') and publier:
        from engine.calibration_store import sauver_tables
        tables = rapport.pop('tables')
        echantillons = {'total': len(observations)}
        chemin = sauver_tables(tables, echantillons=echantillons)
        rapport['publie'] = True
        rapport['fichier'] = str(chemin)
    else:
        rapport.pop('tables', None)
    rapport['date'] = datetime.now(timezone.utc).isoformat()
    return rapport


# --------------------------------------------------------------------------
# Surveillance
# --------------------------------------------------------------------------

def bilan_par_marche(observations: Sequence[Observation]) -> list[dict[str, Any]]:
    """Écart annoncé / observé par marché, avec sa marge d'erreur.

    L'intervalle évite de conclure sur du bruit : c'est l'erreur qu'avait
    failli commettre le bilan partiel du 10 septembre.
    """
    par_marche: dict[str, list[Observation]] = defaultdict(list)
    for ob in observations:
        par_marche[ob.marche].append(ob)
    out = []
    for marche, lot in sorted(par_marche.items()):
        n = len(lot)
        annonce = sum(ob.p_directe for ob in lot) / n
        observe = sum(ob.y_direct for ob in lot) / n
        # Écart-type d'une proportion, doublé : deux écarts-types ≈ 95 %.
        marge = 2 * math.sqrt(max(observe * (1 - observe), 1e-9) / n)
        ecart = observe - annonce
        out.append({
            'marche': marche,
            'observations': n,
            'annonce': round(100 * annonce, 1),
            'observe': round(100 * observe, 1),
            'ecart_points': round(100 * ecart, 1),
            'marge_points': round(100 * marge, 1),
            'significatif': abs(ecart) > marge,
        })
    return out


def derives(conn: sqlite3.Connection, *, fenetre: int = 600) -> list[dict[str, Any]]:
    """Marchés qui ont dérivé sur la période récente."""
    observations = collecter_observations(conn)
    if len(observations) < fenetre // 2:
        return []
    recentes = observations[-fenetre:]
    return [b for b in bilan_par_marche(recentes) if b['significatif']]


def rapport_lisible(rapport: dict[str, Any]) -> str:
    """Le rapport de recalibration, en français, sans jargon."""
    lignes = [f"Observations réglées : {rapport.get('observations', 0)}"]
    if rapport.get('test'):
        lignes.append(
            f"Apprentissage sur {rapport['apprentissage']} options, "
            f"test sur {rapport['test']} options postérieures."
        )
    if 'brier_candidate' in rapport:
        lignes.append(
            f"Score de Brier hors échantillon — sans correction : "
            f"{rapport['brier_sans_correction']} ; version en place : "
            f"{rapport['brier_version_actuelle']} ; candidate : "
            f"{rapport['brier_candidate']}."
        )
    lignes.append(
        ('Nouvelle calibration publiée. ' if rapport.get('publie')
         else 'Calibration inchangée. ') + rapport.get('raison', '')
    )
    return '\n'.join(lignes)
