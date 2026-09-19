"""
Moteur de probabilités ZanalyZ — fonctions pures, sans Django ni base.

Chaîne v4
---------
1. Cotes 1X2 → probabilités sans marge (de-vig par puissance).
2. Totaux → probabilité de dépassement **sur la ligne réellement cotée**.
3. Ajustement de λ domicile / extérieur (Poisson + Dixon–Coles).
4. Matrice de scores → options de marché.
5. Calibration marché par marché, complémentaires cohérents à 100 %.
6. Sélection : une Recommandée d'abord, puis trois niveaux, sous plafonds
   de répétition et d'exposition corrélée.

Changements par rapport à v3.1
------------------------------
* La ligne de totaux n'est plus supposée valoir 2,5 : lignes demi, entières
  (remboursement) et quarts sont traitées explicitement.
* Les codes `HCP_*_-1` et `HCP_*_-2` disparaissent : ils désignaient
  exactement le même événement que `MRG_*_2` et `MRG_*_3`, ce qui permettait
  au même pari de passer deux fois le plafond de répétition.
* Le plafond de répétition suit la taille du lot, et une seconde limite borne
  l'exposition à un même **facteur** (niveau de buts, issue du match).
* La Recommandée est choisie sur son utilité réelle, plus sur sa seule
  probabilité : « moins de 5 buts » sortait 58 % du temps.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

import numpy as np

from engine.calibrage import (
    CALIBRATION_MARCHE_DEFAUT,
    cle_marche_depuis_code,
    corriger as corriger_marche,
    tables_marche,
    verifier_coherence,
)

RHO = -0.06
VERSION_MOTEUR = '4.0.0'
# Part des buts inscrits avant la pause. Mesurée sur 7 571 matchs de huit
# compétitions (5 grands championnats, Portugal, C1, C3) : 0,442. Aucune
# compétition ne s'en écarte de façon significative — la valeur reste donc
# unique, et non déclinée par compétition.
FACTEUR_MI_TEMPS = 0.442
RESIDU_DOUTEUX = 0.02
RESIDU_REFUS = 0.08
COTE_MIN, COTE_MAX = 1.01, 1000.0
MARGE_MAX = 0.35

# Plafonds de répétition : planchers, relevés proportionnellement au lot.
PLAFOND_FORME_MIN = 3
PART_FORME_MAX = 0.15
PART_FACTEUR_MAX = 0.50

CALIBRATION_DEFAUT = CALIBRATION_MARCHE_DEFAUT
CALIBRATION = CALIBRATION_DEFAUT

# Erreur de calibration mesurée, en points de pourcentage. Plus bas = famille
# dont les annonces tiennent le mieux leurs promesses.
#
# Valeurs remesurées sur 212 746 options réglées de trois saisons. Deux
# corrections importantes par rapport à la table héritée de la v3.1 :
#   - « Handicap » était noté 1,80 et mesure 2,94 : la table le favorisait à
#     tort. Le défaut est réel et systématique (sous-estimation de 3,6 points
#     dans la tranche 0,7–0,8).
#   - « BTTS » était noté 4,07 et mesure 1,42 : il était exclu de la sélection
#     sans raison, alors qu'il est mieux calibré que le handicap qui, lui,
#     était éligible.
FIABILITE = {
    'Ecart de buts': 0.80,
    '1X2': 1.02,
    'Double chance': 1.02,
    'Mi-temps': 1.17,
    'Total buts': 1.18,
    'BTTS': 1.42,
    'Handicap': 2.94,
    "Total d'une équipe": 3.06,
    'Une équipe marque': 3.15,
}
FAMILLES_ELIGIBLES = frozenset({
    'Total buts', 'Mi-temps', 'Handicap', 'Ecart de buts', 'Double chance',
    '1X2', 'BTTS',
})
# Les deux familles les moins fiables restent écartées de la sélection.
FAMILLES_PEU_FIABLES = frozenset({'Une équipe marque', "Total d'une équipe"})
CODE_FILET = 'OV_0.5'

# Bonus négatif = famille privilégiée pour ce profil de match.
ROUTAGE = {
    'desequilibre': {
        'Ecart de buts': -0.7, 'Handicap': -0.5, 'Total buts': -0.2,
        'Mi-temps': -0.1, 'BTTS': 0.2, 'Double chance': 0.5, '1X2': 0.2,
    },
    'moyen': {
        'Total buts': -0.3, 'Ecart de buts': -0.2, 'BTTS': -0.1,
        'Mi-temps': -0.1, 'Handicap': 0.0, 'Double chance': 0.0, '1X2': 0.1,
    },
    'equilibre': {
        'Double chance': -0.5, 'Total buts': -0.3, 'BTTS': -0.2,
        'Mi-temps': -0.1, 'Handicap': 0.3, 'Ecart de buts': 0.4, '1X2': 0.25,
    },
}

BANDES = {
    'prudente': (0.70, 0.90),
    'equilibree': (0.55, 0.70),
    'audacieuse': (0.28, 0.505),
}

# Fenêtre de la Recommandée : assez haute pour être sûre, assez basse pour
# rester informative. Au-delà de 0,88 le pari ne dit plus rien du match.
BANDE_RECOMMANDEE = (0.62, 0.88)

_CALIBRATION_CACHE: dict | None = None


def invalider_calibration_cache() -> None:
    global _CALIBRATION_CACHE
    _CALIBRATION_CACHE = None
    try:
        from engine import calibrage
        calibrage.invalider_cache()
    except Exception:  # noqa: BLE001
        pass


def tables_calibration() -> dict:
    global _CALIBRATION_CACHE
    if _CALIBRATION_CACHE is None:
        _CALIBRATION_CACHE = tables_marche()
    return _CALIBRATION_CACHE


class AnalyseInvalide(ValueError):
    """Cotes ou marché incohérents : le moteur refuse de produire des tips."""


def _valider_cote(c: float, label: str) -> float:
    try:
        v = float(c)
    except (TypeError, ValueError) as e:
        raise AnalyseInvalide(f'Cote {label} non numérique.') from e
    if not math.isfinite(v) or v < COTE_MIN or v > COTE_MAX:
        raise AnalyseInvalide(f'Cote {label} hors bornes [{COTE_MIN}, {COTE_MAX}] : {c!r}')
    return v


def devig_puissance(cotes):
    """Retire la marge en supposant p_i ∝ (1/c_i)^k. Conserve l'ordre."""
    inv = [1 / c for c in cotes]
    lo, hi = 1.0, 4.0
    for _ in range(90):
        k = (lo + hi) / 2
        if sum(x ** k for x in inv) > 1:
            lo = k
        else:
            hi = k
    p = [x ** ((lo + hi) / 2) for x in inv]
    s = sum(p)
    return [x / s for x in p]


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _tau(x, y, lh, la, rho):
    if x == 0 and y == 0:
        t = 1 - lh * la * rho
    elif x == 0 and y == 1:
        t = 1 + lh * rho
    elif x == 1 and y == 0:
        t = 1 + la * rho
    elif x == 1 and y == 1:
        t = 1 - rho
    else:
        return 1.0
    return max(t, 1e-6)


def matrice(lh, la, rho=RHO, n=12):
    m = np.array([[_tau(i, j, lh, la, rho) * _pois(i, lh) * _pois(j, la)
                   for j in range(n + 1)] for i in range(n + 1)])
    total = m.sum()
    if total <= 0 or not np.isfinite(total):
        raise AnalyseInvalide('Matrice de scores dégénérée.')
    return m / total


# --------------------------------------------------------------------------
# Lignes de totaux
# --------------------------------------------------------------------------

def decomposer_ligne(ligne: float) -> list[tuple[float, float]]:
    """Décompose une ligne asiatique en lignes élémentaires pondérées.

    * 2,5 → [(2.5, 1.0)]        ligne demi, aucun remboursement possible
    * 3,0 → [(3.0, 1.0)]        ligne entière, remboursement si total = 3
    * 2,75 → [(2.5, .5), (3.0, .5)]   quart : moitié de mise sur chaque voisine
    """
    lg = round(float(ligne) * 4) / 4
    reste = lg - math.floor(lg)
    if abs(reste - 0.25) < 1e-9:
        return [(math.floor(lg), 0.5), (math.floor(lg) + 0.5, 0.5)]
    if abs(reste - 0.75) < 1e-9:
        return [(math.floor(lg) + 0.5, 0.5), (math.floor(lg) + 1.0, 0.5)]
    return [(lg, 1.0)]


def p_over_modele(M: np.ndarray, ligne: float) -> float:
    """P(total > ligne) sous le modèle, en neutralisant les remboursements.

    Sur une ligne entière, la mise est rendue si le total l'égale : le prix
    affiché ne porte alors que sur les issues non remboursées. Comparer ce
    prix à un P(total > ligne) brut fausse l'ajustement.
    """
    n = M.shape[0]
    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    total = i + j
    p = 0.0
    for sous_ligne, poids in decomposer_ligne(ligne):
        au_dessus = float(M[total > sous_ligne].sum())
        if float(sous_ligne).is_integer():
            egal = float(M[total == sous_ligne].sum())
            denom = 1.0 - egal
            au_dessus = au_dessus / denom if denom > 1e-9 else 0.5
        p += poids * au_dessus
    return float(np.clip(p, 1e-9, 1 - 1e-9))


def p_over_marche(cote_over: float, cote_under: float) -> float:
    """Probabilité de dépassement sans marge, marché à deux issues."""
    return (1 / cote_over) / (1 / cote_over + 1 / cote_under)


def ajuster(p1, pn, p2, p_over=None, ligne=None):
    """Trouve (λ_dom, λ_ext) qui reproduisent le mieux le marché observé.

    `p_over` se rapporte à `ligne`, pas à 2,5. C'est la correction centrale
    de la v4 : en v3.1 un prix coté sur 5,5 buts était lu comme un prix sur
    2,5, ce qui écrasait les buts attendus.
    """
    from scipy.optimize import minimize

    utilise_totaux = p_over is not None and ligne is not None

    def erreur(v):
        lh, la = math.exp(v[0]), math.exp(v[1])
        M = matrice(lh, la)
        n = M.shape[0]
        i = np.arange(n)[:, None]
        j = np.arange(n)[None, :]
        q1, qn, q2 = M[i > j].sum(), np.trace(M), M[i < j].sum()
        e = (q1 - p1) ** 2 + (qn - pn) ** 2 + (q2 - p2) ** 2
        if utilise_totaux:
            e += (p_over_modele(M, ligne) - p_over) ** 2
        return e

    r = minimize(
        erreur, [math.log(1.4), math.log(1.2)], method='Nelder-Mead',
        options={'xatol': 1e-7, 'fatol': 1e-13, 'maxiter': 6000},
    )
    lh, la = math.exp(r.x[0]), math.exp(r.x[1])
    residu = math.sqrt(max(float(r.fun), 0.0))
    if not getattr(r, 'success', True):
        residu = max(residu, RESIDU_DOUTEUX * 2)
    return lh, la, residu


def corriger(p, marche_ou_famille):
    """Corrige via une clé de marché ; ignore les anciennes clés « famille »."""
    if isinstance(marche_ou_famille, str) and marche_ou_famille in FIABILITE:
        return float(p)
    return corriger_marche(p, marche_ou_famille, tables_calibration())


def profil_match(p1, p2):
    g = abs(p1 - p2)
    if g < 0.15:
        return 'equilibre'
    if g < 0.42:
        return 'moyen'
    return 'desequilibre'


def score_probable(M):
    i, j = np.unravel_index(int(np.argmax(M)), M.shape)
    return f'{int(i)}-{int(j)}'


def libelle(code, nom_dom='Domicile', nom_ext='Extérieur'):
    table = {
        '1X2_1': f'{nom_dom} gagne',
        '1X2_N': 'Match nul',
        '1X2_2': f'{nom_ext} gagne',
        'DC_1X': f'{nom_dom} ne perd pas',
        'DC_X2': f'{nom_ext} ne perd pas',
        'DC_12': 'Pas de match nul',
        'OV_0.5': 'Au moins 1 but',
        'OV_1.5': 'Au moins 2 buts',
        'OV_2.5': 'Au moins 3 buts',
        'OV_3.5': 'Au moins 4 buts',
        'OV_4.5': 'Au moins 5 buts',
        'UN_0.5': 'Aucun but',
        'UN_1.5': 'Moins de 2 buts',
        'UN_2.5': 'Moins de 3 buts',
        'UN_3.5': 'Moins de 4 buts',
        'UN_4.5': 'Moins de 5 buts',
        'MRG_H_2': f'{nom_dom} gagne par 2 buts ou plus',
        'MRG_H_3': f'{nom_dom} gagne par 3 buts ou plus',
        'MRG_A_2': f'{nom_ext} gagne par 2 buts ou plus',
        'MRG_A_3': f'{nom_ext} gagne par 3 buts ou plus',
        'HCP_H_+1': f'{nom_dom} +1 (ne perd pas de plus d’un but)',
        'HCP_A_+1': f'{nom_ext} +1 (ne perd pas de plus d’un but)',
        'HT_1': f'{nom_dom} mène à la pause',
        'HT_N': 'Nul à la pause',
        'HT_2': f'{nom_ext} mène à la pause',
        'HT_OV_0.5': 'Au moins 1 but avant la pause',
        'HT_OV_1.5': 'Au moins 2 buts avant la pause',
        'HT_UN_0.5': 'Aucun but avant la pause',
        'HT_UN_1.5': 'Moins de 1,5 but avant la pause',
        'BTTS_O': 'Les deux équipes marquent',
        'BTTS_N': 'Au moins une équipe ne marque pas',
        'DOM_MARQUE': f'{nom_dom} marque',
        'EXT_MARQUE': f'{nom_ext} marque',
        'DOM_2PLUS': f'{nom_dom} marque 2 buts ou plus',
        'EXT_2PLUS': f'{nom_ext} marque 2 buts ou plus',
    }
    return table.get(code, code)


_FORMES = {
    'OV_0.5': 'Au moins 1 but', 'OV_1.5': 'Au moins 2 buts',
    'OV_2.5': 'Au moins 3 buts', 'OV_3.5': 'Au moins 4 buts',
    'OV_4.5': 'Au moins 5 buts', 'UN_0.5': 'Aucun but',
    'UN_1.5': 'Moins de 2 buts', 'UN_2.5': 'Moins de 3 buts',
    'UN_3.5': 'Moins de 4 buts', 'UN_4.5': 'Moins de 5 buts',
    'DC_12': 'Pas de match nul', '1X2_N': 'Match nul',
    'HT_OV_0.5': 'Au moins 1 but avant la pause',
    'HT_OV_1.5': 'Au moins 2 buts avant la pause',
    'HT_UN_0.5': 'Aucun but avant la pause',
    'HT_UN_1.5': 'Moins de 1,5 but avant la pause',
    'HT_N': 'Nul à la pause', 'BTTS_O': 'BTTS oui', 'BTTS_N': 'BTTS non',
    'DC_1X': 'ÉQUIPE ne perd pas', 'DC_X2': 'ÉQUIPE ne perd pas',
    '1X2_1': 'ÉQUIPE gagne', '1X2_2': 'ÉQUIPE gagne',
    'HT_1': 'ÉQUIPE mène à la pause', 'HT_2': 'ÉQUIPE mène à la pause',
    'DOM_MARQUE': 'ÉQUIPE marque', 'EXT_MARQUE': 'ÉQUIPE marque',
    'DOM_2PLUS': 'ÉQUIPE marque 2+', 'EXT_2PLUS': 'ÉQUIPE marque 2+',
    'HCP_H_+1': 'ÉQUIPE +1', 'HCP_A_+1': 'ÉQUIPE +1',
    'MRG_H_2': 'ÉQUIPE gagne par 2+', 'MRG_A_2': 'ÉQUIPE gagne par 2+',
    'MRG_H_3': 'ÉQUIPE gagne par 3+', 'MRG_A_3': 'ÉQUIPE gagne par 3+',
}


def forme_pari(code: str, libelle_opt: str = '') -> str:
    """Normalise une tip pour le plafond : même forme ≠ même équipe."""
    if code in _FORMES:
        return _FORMES[code]
    s = libelle_opt or code
    s = re.sub(r'^.+? \+(\d) \(.*\)$', r'ÉQUIPE +\1', s)
    s = re.sub(r'^.+? -(\d) \(.*\)$', r'ÉQUIPE -\1', s)
    return s


def facteur_cache(opt: dict) -> str:
    """Moteur commun derrière un pari : deux options du même facteur gagnent
    ou perdent souvent ensemble, même sur des matchs différents."""
    f, code = opt['famille'], opt['code']
    if f in ('Total buts', 'BTTS', 'Une équipe marque', "Total d'une équipe"):
        return 'buts'
    if f == 'Mi-temps':
        return 'buts' if code.startswith(('HT_OV', 'HT_UN')) else 'issue'
    if code in ('DC_12', '1X2_N'):
        return 'nul'
    return 'issue'


def _option(code, famille, p, origine, nom_dom, nom_ext, corriger_p=True, ligue=None):
    p_brute = float(p)
    if origine == 'calcul' and corriger_p:
        mk = cle_marche_depuis_code(code)
        p = corriger_marche(p_brute, mk, tables_calibration(), ligue)
    p = float(np.clip(p, 0.005, 0.995))
    return {
        'code': code,
        'famille': famille,
        'libelle': libelle(code, nom_dom, nom_ext),
        'probabilite': p,
        'p_brute': p_brute,
        'cote_juste': 1.0 / p,
        'origine': origine,
        'niveau': 'detail',
        'marche': cle_marche_depuis_code(code),
        'fiabilite': FIABILITE.get(famille, 3.0),
    }


def options_depuis_matrice(lh, la, p1, pn, p2, p_over, ligne, nom_dom, nom_ext,
                           ligue=None, origine_base='marche'):
    M = matrice(lh, la)
    n = M.shape[0]
    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    tot, ecart = i + j, i - j

    Mh = matrice(FACTEUR_MI_TEMPS * lh, FACTEUR_MI_TEMPS * la)
    nh = Mh.shape[0]
    ih = np.arange(nh)[:, None]
    jh = np.arange(nh)[None, :]
    htot, hecart = ih + jh, ih - jh

    opts: list[dict] = []
    add = opts.append

    # Sans marché, ces probabilités sont calculées comme les autres : l'origine
    # doit le dire, sinon la boucle d'apprentissage les écarterait à tort.
    ob = origine_base
    corr = ob == 'calcul'
    add(_option('1X2_1', '1X2', p1, ob, nom_dom, nom_ext, corr, ligue))
    add(_option('1X2_N', '1X2', pn, ob, nom_dom, nom_ext, corr, ligue))
    add(_option('1X2_2', '1X2', p2, ob, nom_dom, nom_ext, corr, ligue))

    add(_option('DC_1X', 'Double chance', p1 + pn, ob, nom_dom, nom_ext, corr, ligue))
    add(_option('DC_X2', 'Double chance', pn + p2, ob, nom_dom, nom_ext, corr, ligue))
    add(_option('DC_12', 'Double chance', p1 + p2, ob, nom_dom, nom_ext, corr, ligue))

    # Un seuil ne vient du marché que si c'est **celui** que le marché cote.
    seuil_marche = None
    if p_over is not None and ligne is not None and float(ligne) in (0.5, 1.5, 2.5, 3.5, 4.5):
        seuil_marche = float(ligne)

    for seuil in (0.5, 1.5, 2.5, 3.5, 4.5):
        if seuil == seuil_marche:
            add(_option(f'OV_{seuil}', 'Total buts', p_over, 'marche', nom_dom, nom_ext, False))
            add(_option(f'UN_{seuil}', 'Total buts', 1 - p_over, 'marche', nom_dom, nom_ext,
                        False))
        else:
            add(_option(f'OV_{seuil}', 'Total buts', float(M[tot > seuil].sum()),
                        'calcul', nom_dom, nom_ext, ligue=ligue))
            add(_option(f'UN_{seuil}', 'Total buts', float(M[tot < seuil].sum()),
                        'calcul', nom_dom, nom_ext, ligue=ligue))

    # Écart de buts. `HCP_*_-1` / `-2` ont été retirés : mêmes événements que
    # `MRG_*_2` / `MRG_*_3`, ils dédoublaient artificiellement l'offre.
    for n_mrg in (2, 3):
        pd_ = float(M[ecart >= n_mrg].sum())
        pe = float(M[-ecart >= n_mrg].sum())
        if pd_ > 0.05:
            add(_option(f'MRG_H_{n_mrg}', 'Ecart de buts', pd_, 'calcul', nom_dom, nom_ext, ligue=ligue))
        if pe > 0.05:
            add(_option(f'MRG_A_{n_mrg}', 'Ecart de buts', pe, 'calcul', nom_dom, nom_ext, ligue=ligue))

    add(_option('HCP_H_+1', 'Handicap', float(M[ecart + 1 >= 0].sum()),
                'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HCP_A_+1', 'Handicap', float(M[-ecart + 1 >= 0].sum()),
                'calcul', nom_dom, nom_ext, ligue=ligue))

    add(_option('HT_1', 'Mi-temps', float(Mh[hecart > 0].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HT_N', 'Mi-temps', float(Mh[hecart == 0].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HT_2', 'Mi-temps', float(Mh[hecart < 0].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HT_OV_0.5', 'Mi-temps', float(Mh[htot > 0.5].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HT_OV_1.5', 'Mi-temps', float(Mh[htot > 1.5].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HT_UN_0.5', 'Mi-temps', float(Mh[htot < 0.5].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('HT_UN_1.5', 'Mi-temps', float(Mh[htot < 1.5].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))

    add(_option('BTTS_O', 'BTTS', float(M[(i > 0) & (j > 0)].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('BTTS_N', 'BTTS', float(M[~((i > 0) & (j > 0))].sum()), 'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('DOM_MARQUE', 'Une équipe marque', float(M[1:, :].sum()),
                'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('EXT_MARQUE', 'Une équipe marque', float(M[:, 1:].sum()),
                'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('DOM_2PLUS', "Total d'une équipe", float(M[2:, :].sum()),
                'calcul', nom_dom, nom_ext, ligue=ligue))
    add(_option('EXT_2PLUS', "Total d'une équipe", float(M[:, 2:].sum()),
                'calcul', nom_dom, nom_ext, ligue=ligue))
    return opts, M


def analyser(cotes_1x2, totaux=None, nom_dom='Domicile', nom_ext='Extérieur',
             mouvement=None, ligue=None):
    """Analyse d'une rencontre.

    `totaux` : `(cote_over, cote_under, ligne)`, ou None. Passer un couple
    `(over, under)` sans ligne est refusé : c'est exactement l'erreur que la
    v4 corrige.
    """
    c1 = _valider_cote(cotes_1x2[0], '1')
    cn = _valider_cote(cotes_1x2[1], 'N')
    c2 = _valider_cote(cotes_1x2[2], '2')
    p1, pn, p2 = devig_puissance([c1, cn, c2])
    marge = 1 / c1 + 1 / cn + 1 / c2 - 1
    if marge < -1e-9 or marge > MARGE_MAX:
        raise AnalyseInvalide(f'Marge 1X2 hors bornes : {marge:.3f}')

    p_over = ligne = None
    if totaux:
        if len(totaux) != 3:
            raise AnalyseInvalide(
                'Totaux attendus sous la forme (over, under, ligne) : '
                'une cote de totaux sans sa ligne est inexploitable.'
            )
        o = _valider_cote(totaux[0], 'totaux over')
        u = _valider_cote(totaux[1], 'totaux under')
        try:
            ligne = float(totaux[2])
        except (TypeError, ValueError) as e:
            raise AnalyseInvalide(f'Ligne de totaux illisible : {totaux[2]!r}') from e
        if not (0.5 <= ligne <= 8.5):
            raise AnalyseInvalide(f'Ligne de totaux hors bornes : {ligne}')
        p_over = p_over_marche(o, u)

    lh, la, residu = ajuster(p1, pn, p2, p_over, ligne)
    if residu > RESIDU_REFUS:
        raise AnalyseInvalide(
            f'Marché incohérent (résidu {residu:.3f}) : 1X2 et totaux se contredisent.'
        )

    opts, M = options_depuis_matrice(
        lh, la, p1, pn, p2, p_over, ligne, nom_dom, nom_ext, ligue,
    )
    return {
        'buts_dom_attendus': lh,
        'buts_ext_attendus': la,
        'p1': p1,
        'pn': pn,
        'p2': p2,
        'p_over': p_over,
        'ligne_totaux': ligne,
        'ligue': ligue,
        'score_probable': score_probable(M),
        'profil': profil_match(p1, p2),
        'marge_marche': marge,
        'residu': residu,
        'douteuse': residu > RESIDU_DOUTEUX,
        'mouvement': mouvement or {},
        'version_moteur': VERSION_MOTEUR,
        'incoherence': verifier_coherence(opts),
        'options': opts,
    }


def analyser_sans_marche(lh, la, nom_dom='Domicile', nom_ext='Extérieur',
                         ligue=None, source='forces'):
    """Analyse d'un match dont aucune cote n'est disponible.

    Les buts attendus viennent alors des forces d'équipe (`engine.force`),
    estimées sur les seuls résultats passés. C'est moins bon que le marché —
    log-perte 1,004 contre 0,962, mesuré sur 4 247 matchs — mais nettement
    mieux que rien : la v3.1 n'affichait tout simplement aucune analyse,
    ce qui concernait 17 % des rencontres du dernier instantané.

    Les options portent `origine = 'calcul'` et la confiance est abaissée.
    """
    lh = float(np.clip(lh, 0.05, 6.0))
    la = float(np.clip(la, 0.05, 6.0))
    M = matrice(lh, la)
    n = M.shape[0]
    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    p1 = float(M[i > j].sum())
    pn = float(np.trace(M))
    p2 = float(M[i < j].sum())
    opts, M = options_depuis_matrice(
        lh, la, p1, pn, p2, None, None, nom_dom, nom_ext, ligue,
        origine_base='calcul',
    )
    return {
        'buts_dom_attendus': lh,
        'buts_ext_attendus': la,
        'p1': p1, 'pn': pn, 'p2': p2,
        'p_over': None,
        'ligne_totaux': None,
        'ligue': ligue,
        'score_probable': score_probable(M),
        'profil': profil_match(p1, p2),
        'marge_marche': 0.0,
        'residu': 0.0,
        'douteuse': False,
        'sans_marche': True,
        'source_estimation': source,
        'mouvement': {},
        'version_moteur': VERSION_MOTEUR,
        'incoherence': verifier_coherence(opts),
        'options': opts,
    }


# --------------------------------------------------------------------------
# Sélection
# --------------------------------------------------------------------------

def est_eligible(opt):
    if opt['code'] == CODE_FILET:
        return False
    if opt['famille'] in FAMILLES_PEU_FIABLES:
        return False
    return opt['famille'] in FAMILLES_ELIGIBLES


def _dans_bande(p, niveau):
    lo, hi = BANDES[niveau]
    return lo <= p <= hi if niveau == 'audacieuse' else lo <= p < hi


def specificite(opt, moyennes):
    """Écart entre ce match et le match moyen du lot, pour ce pari.

    Un pari qui vaut la même chose partout n'apprend rien sur la rencontre :
    c'est lui qui produisait onze fois « moins de 4 buts » en une journée.
    """
    ref = (moyennes or {}).get(opt['code'])
    if ref is None:
        return 0.0
    return abs(float(opt['probabilite']) - float(ref))


def utilite(opt, profil, moyennes, contexte=None):
    """Score d'utilité d'une option. Plus haut = meilleure proposition.

    Quatre termes, tous en points de probabilité pour rester comparables :
    fiabilité du marché, adéquation au profil du match, spécificité, et
    pénalité d'exposition déjà prise sur le même facteur.
    """
    ctx = contexte or {}
    score = 0.0
    score -= 1.6 * FIABILITE.get(opt['famille'], 3.0)
    score -= 1.4 * ROUTAGE.get(profil, {}).get(opt['famille'], 0.3)
    score += 9.0 * specificite(opt, moyennes)
    # Une probabilité issue directement du marché est plus sûre qu'une dérivée.
    if opt.get('origine') == 'marche':
        score += 0.6
    # Le résidu dit à quel point 1X2 et totaux se contredisent.
    score -= 12.0 * float(ctx.get('residu') or 0.0)
    saturation = ctx.get('saturation_facteur') or {}
    score -= 3.0 * float(saturation.get(facteur_cache(opt), 0.0))
    return score


# Retenue maximale appliquée à la probabilité pour obtenir la confiance.
# Bornée : une estimation fragile est dégradée, jamais anéantie.
RETENUE_MAX = 18.0


def confiance(opt, analyse_ctx) -> int:
    """Indice 0–100 affiché à l'utilisateur.

    Se lit comme « la probabilité, une fois retirée la fragilité de
    l'estimation ». Une Recommandée à 73 % sur un marché bien calibré et des
    cotes cohérentes affiche environ 70 ; la même sur un marché mal calibré
    ou des cotes qui se contredisent tombe vers 55.

    Ce n'est pas une probabilité : deux paris de même probabilité n'ont pas
    la même solidité selon le marché dont ils sortent.
    """
    p = float(opt['probabilite'])
    retenue = 2.5 * FIABILITE.get(opt['famille'], 3.0)
    retenue += 120.0 * float(analyse_ctx.get('residu') or 0.0)
    if analyse_ctx.get('douteuse'):
        retenue += 4.0
    if analyse_ctx.get('sans_marche'):
        # Estimée sur les seuls résultats passés : mesurée moins bonne que le
        # marché (log-perte 1,004 contre 0,962 sur 4 247 matchs).
        retenue += 7.0
    if opt.get('origine') == 'marche':
        retenue -= 3.0
    retenue = max(0.0, min(RETENUE_MAX, retenue))
    return int(max(1, min(99, round(100.0 * p - retenue))))


def expliquer(opt, analyse_ctx, moyennes) -> str:
    """Une phrase, en français clair, sur ce qui porte ce choix."""
    bouts: list[str] = []
    spec = specificite(opt, moyennes)
    if spec >= 0.08:
        bouts.append('nettement plus probable ici que sur les autres matchs du lot')
    elif spec <= 0.02 and moyennes:
        bouts.append('proche du niveau habituel : peu spécifique à cette rencontre')
    if opt.get('origine') == 'marche':
        bouts.append('probabilité lue directement sur le marché')
    else:
        bouts.append('probabilité dérivée du modèle puis recalibrée')
    lg = analyse_ctx.get('ligne_totaux')
    if lg is not None and opt['famille'] == 'Total buts':
        bouts.append(f'totaux cotés sur la ligne {lg:g}')
    if analyse_ctx.get('douteuse'):
        bouts.append('cotes d’entrée peu cohérentes : prudence')
    return ' ; '.join(bouts).capitalize() + '.'


def _plafonds(nb_matchs: int) -> dict[str, int]:
    """Plafonds de répétition, proportionnels à la taille du lot.

    Trois limites complémentaires :

    * `forme` — combien de fois le même pari peut sortir dans tout le lot.
    * `facteur` — combien de paris peuvent dépendre du même moteur sous-jacent.
    * `niveau` — combien de fois le même pari peut occuper **le même niveau**.
      C'est celle-ci qui empêche « moins de 4 buts » d'être la prudente de
      onze matchs sur dix-huit.
    """
    options = max(nb_matchs, 1) * 3
    return {
        'forme': max(PLAFOND_FORME_MIN, math.ceil(PART_FORME_MAX * options)),
        'facteur': max(PLAFOND_FORME_MIN, math.ceil(PART_FACTEUR_MAX * options)),
        'niveau': max(1, math.floor(max(nb_matchs, 1) / 3)),
    }


def choisir_options(
    options, profil, moyennes, *, analyse_ctx=None,
    compteur_formes=None, compteur_facteurs=None, compteur_niveaux=None,
    plafonds=None,
):
    """Choisit la Recommandée puis les trois niveaux, sous plafonds.

    La Recommandée est désignée **en premier** : c'est la proposition phare,
    elle ne doit pas être le résidu de ce que les trois niveaux ont laissé.

    Les plafonds se relâchent par degrés plutôt que de laisser un niveau vide :
    mieux vaut une répétition assumée qu'une case manquante dans l'interface.
    """
    ctx = dict(analyse_ctx or {})
    formes = compteur_formes if compteur_formes is not None else Counter()
    facteurs = compteur_facteurs if compteur_facteurs is not None else Counter()
    niveaux = compteur_niveaux if compteur_niveaux is not None else Counter()
    lim = plafonds or _plafonds(1)

    out = [dict(o) for o in options]
    for o in out:
        o['niveau'] = 'filet' if o['code'] == CODE_FILET else 'detail'
        o['forme'] = forme_pari(o['code'], o.get('libelle', ''))
        o['facteur'] = facteur_cache(o)

    total_facteurs = max(sum(facteurs.values()), 1)
    ctx['saturation_facteur'] = {
        f: facteurs.get(f, 0) / total_facteurs for f in ('buts', 'issue', 'nul')
    }

    pris_codes: set[str] = set()
    pris_familles: set[str] = set()
    pris_formes: set[str] = set()

    def disponible(o, niveau, rigueur=2):
        """rigueur 2 = tous les plafonds, 1 = sans celui du niveau, 0 = aucun."""
        if not est_eligible(o):
            return False
        if o['code'] in pris_codes or o['famille'] in pris_familles:
            return False
        if o['forme'] in pris_formes:
            return False
        if rigueur >= 1:
            if niveaux[(niveau, o['forme'])] >= lim['niveau'] and rigueur >= 2:
                return False
            if formes[o['forme']] >= lim['forme']:
                return False
            if facteurs[o['facteur']] >= lim['facteur']:
                return False
        return True

    def retenir(o, niveau):
        o['niveau'] = niveau
        o['confiance'] = confiance(o, ctx)
        o['explication'] = expliquer(o, ctx, moyennes)
        pris_codes.add(o['code'])
        pris_familles.add(o['famille'])
        pris_formes.add(o['forme'])
        formes[o['forme']] += 1
        facteurs[o['facteur']] += 1
        niveaux[(niveau, o['forme'])] += 1

    def meilleur(niveau, filtre_bande):
        """Cherche en relâchant : bande puis plafonds, jamais l'éligibilité."""
        for rigueur in (2, 1, 0):
            for exiger_bande in (True, False):
                lot = [
                    o for o in out
                    if disponible(o, niveau, rigueur)
                    and (filtre_bande(o) if exiger_bande else True)
                ]
                if lot:
                    lot.sort(key=lambda o: (
                        -utilite(o, profil, moyennes, ctx), -o['probabilite'],
                    ))
                    return lot[0]
        return None

    # 1. La Recommandée : la meilleure option de la fenêtre « sûre ».
    lo, hi = BANDE_RECOMMANDEE
    choix = meilleur('recommandee', lambda o: lo <= o['probabilite'] <= hi)
    if choix is not None:
        retenir(choix, 'recommandee')

    # 2. Les trois niveaux, familles distinctes, dans leur bande.
    for niveau in ('prudente', 'equilibree', 'audacieuse'):
        choix = meilleur(niveau, lambda o, n=niveau: _dans_bande(o['probabilite'], n))
        if choix is not None:
            retenir(choix, niveau)

    # 3. Le filet reçoit aussi sa confiance, pour un affichage homogène.
    for o in out:
        if o['niveau'] == 'filet':
            o['confiance'] = confiance(o, ctx)
            o['explication'] = 'Filet de sécurité : issue la plus probable du match.'
    return out


def moyennes_par_code(listes_options):
    acc = defaultdict(list)
    for opts in listes_options:
        for o in opts:
            acc[o['code']].append(o['probabilite'])
    return {k: sum(v) / len(v) for k, v in acc.items()}


def lisibilite_match(analyse) -> float:
    """Plus l'entropie 1X2 est basse, plus le match est lisible."""
    p = [analyse['p1'], analyse['pn'], analyse['p2']]
    ent = -sum(x * np.log(max(x, 1e-9)) for x in p)
    return float(-ent)


def classer_journee(analyses):
    """Construit le portefeuille d'un lot de matchs.

    Les rencontres les plus lisibles servent en premier : elles méritent les
    paris les plus spécifiques, les autres prennent ce qui reste sous plafond.
    """
    analyses = list(analyses)
    if not analyses:
        return analyses
    moy = moyennes_par_code(a['options'] for a in analyses)
    formes: Counter = Counter()
    facteurs: Counter = Counter()
    niveaux: Counter = Counter()
    plafonds = _plafonds(len(analyses))
    ordre = sorted(range(len(analyses)), key=lambda i: -lisibilite_match(analyses[i]))
    for i in ordre:
        a = analyses[i]
        a['options'] = choisir_options(
            a['options'], a['profil'], moy,
            analyse_ctx=a,
            compteur_formes=formes, compteur_facteurs=facteurs,
            compteur_niveaux=niveaux, plafonds=plafonds,
        )
    exposition = dict(facteurs)
    total = max(sum(exposition.values()), 1)
    for a in analyses:
        a['exposition_lot'] = {
            'formes_distinctes': len({
                o['forme'] for x in analyses for o in x['options']
                if o['niveau'] in BANDES
            }),
            'facteurs': {k: round(v / total, 3) for k, v in exposition.items()},
            'matchs': len(analyses),
        }
    return analyses


def selections_niveaux(options):
    return {o['niveau']: o['code'] for o in options if o['niveau'] in BANDES}


def uniformite_excessive(selections, seuil=1 / 3):
    """Détecteur de régression : un même code monopolise-t-il un niveau ?"""
    if not selections:
        return False
    n = len(selections)
    for niveau in ('prudente', 'equilibree', 'audacieuse'):
        codes = [s.get(niveau) for s in selections if s.get(niveau)]
        if not codes:
            continue
        _, count = Counter(codes).most_common(1)[0]
        if count > n * seuil:
            return True
    return False
