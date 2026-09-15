"""
Moteur de probabilités ZanalyZ — fonctions pures, sans Django.

Pipeline (v3.1)
---------------
1. Cotes 1X2 (+ OU 2.5) → probabilités sans marge (de-vig).
2. Ajustement de λ domicile / extérieur (Poisson + Dixon–Coles).
3. Matrice de scores → options de marchés.
4. Calibration **marché par marché** (complémentaires cohérents à 100 %).
5. Sélection journée : 3 niveaux + recommandée + filet, plafond de formes, routage profil.

Les tips 1X2 / DC / OU2.5 issus du marché ne passent PAS par la correction.
Le contexte (forme, Elo…) n’entre pas dans le calcul (mesuré sans gain).
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
VERSION_MOTEUR = '3.1.0'
FACTEUR_MI_TEMPS = 0.45
RESIDU_DOUTEUX = 0.02
P_1X2_MAX_RECO = 0.75  # au-delà ≈ « lock » trop court ; favoris 55–74 % OK
COTE_MIN, COTE_MAX = 1.01, 100.0
MARGE_MAX = 0.35
PLAFOND_FORME = 3

# Alias rétrocompat : tables = courbes par clé de marché (plus par famille).
CALIBRATION_DEFAUT = CALIBRATION_MARCHE_DEFAUT
CALIBRATION = CALIBRATION_DEFAUT

# Erreur de calibration mesurée (points). Sous 2,0 = famille fiable.
FIABILITE = {
    'Total buts': 1.08,
    'Mi-temps': 1.70,
    'Handicap': 1.80,
    'Ecart de buts': 1.80,
    'Double chance': 1.83,
    '1X2': 1.83,
    "Total d'une équipe": 3.85,
    'Une équipe marque': 3.88,
    'BTTS': 4.07,
}
FIABLES = frozenset({
    'Total buts', 'Mi-temps', 'Handicap', 'Ecart de buts', 'Double chance',
})
FAMILLES_ELIGIBLES = frozenset({
    'Total buts', 'Mi-temps', 'Handicap', 'Ecart de buts', 'Double chance', '1X2',
})
FAMILLES_PEU_FIABLES = frozenset({
    'BTTS', 'Une équipe marque', "Total d'une équipe",
})
CODE_FILET = 'OV_0.5'

# Bonus négatif = famille privilégiée.
# 1X2 : léger frein (pas d’interdiction) pour laisser place aux favoris nets.
ROUTAGE = {
    'desequilibre': {
        'Handicap': -0.7, 'Ecart de buts': -0.7, 'Total buts': -0.2,
        'Mi-temps': -0.1, 'Double chance': 0.5, '1X2': 0.2,
    },
    'moyen': {
        'Total buts': -0.3, 'Handicap': -0.2, 'Ecart de buts': -0.2,
        'Mi-temps': -0.1, 'Double chance': 0.0, '1X2': 0.1,
    },
    'equilibre': {
        'Double chance': -0.5, 'Total buts': -0.3, 'Mi-temps': -0.1,
        'Handicap': 0.3, 'Ecart de buts': 0.4, '1X2': 0.25,
    },
}

BANDES = {
    'prudente': (0.70, 0.90),
    'equilibree': (0.55, 0.70),
    'audacieuse': (0.28, 0.505),
}

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
    """Tables actives (clés de marché)."""
    global _CALIBRATION_CACHE
    if _CALIBRATION_CACHE is not None:
        return _CALIBRATION_CACHE
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
        raise AnalyseInvalide(
            f'Cote {label} hors bornes [{COTE_MIN}, {COTE_MAX}] : {c!r}'
        )
    return v


def devig_puissance(cotes):
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


def ajuster(p1, pn, p2, p_over25=None):
    from scipy.optimize import minimize

    def erreur(v):
        lh, la = math.exp(v[0]), math.exp(v[1])
        M = matrice(lh, la)
        n = M.shape[0]
        i = np.arange(n)[:, None]
        j = np.arange(n)[None, :]
        q1, qn, q2 = M[i > j].sum(), np.trace(M), M[i < j].sum()
        e = (q1 - p1) ** 2 + (qn - pn) ** 2 + (q2 - p2) ** 2
        if p_over25 is not None:
            e += (M[(i + j) > 2.5].sum() - p_over25) ** 2
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
    """Corrige via clé de marché ; ignore les anciennes clés « famille » orphelines."""
    # Compat tests / anciens appels : si on passe une famille connue sans table
    # marché, ne rien faire (sauf si c'est déjà une clé marché).
    if isinstance(marche_ou_famille, str) and marche_ou_famille in (
        'Total buts', 'Mi-temps', 'Handicap', 'BTTS', 'Une équipe marque',
        'Double chance', '1X2', 'Ecart de buts',
    ):
        return float(p)
    return corriger_marche(p, marche_ou_famille, tables_calibration())


def _p_over_two_way(over, under):
    return (1 / over) / (1 / over + 1 / under)


def profil_match(p1, p2):
    """Profil v3.1 : écart |p1−p2| (pas le max favori)."""
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
        'HCP_H_-1': f'{nom_dom} -1 (gagne par 2 buts ou plus)',
        'HCP_A_-1': f'{nom_ext} -1 (gagne par 2 buts ou plus)',
        'HCP_H_-2': f'{nom_dom} -2 (gagne par 3 buts ou plus)',
        'HCP_A_-2': f'{nom_ext} -2 (gagne par 3 buts ou plus)',
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


def forme_pari(code: str, libelle_opt: str = '') -> str:
    """Normalise une tip pour le plafond journée (même forme ≠ même équipe)."""
    fixes = {
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
        'DC_12': 'Pas de match nul',
        '1X2_N': 'Match nul',
        'HT_OV_0.5': 'Au moins 1 but avant la pause',
        'HT_OV_1.5': 'Au moins 2 buts avant la pause',
        'HT_UN_0.5': 'Aucun but avant la pause',
        'HT_UN_1.5': 'Moins de 1,5 but avant la pause',
        'HT_N': 'Nul à la pause',
        'BTTS_O': 'BTTS oui',
        'BTTS_N': 'BTTS non',
        'DC_1X': 'ÉQUIPE ne perd pas',
        'DC_X2': 'ÉQUIPE ne perd pas',
        '1X2_1': 'ÉQUIPE gagne',
        '1X2_2': 'ÉQUIPE gagne',
        'HT_1': 'ÉQUIPE mène à la pause',
        'HT_2': 'ÉQUIPE mène à la pause',
        'DOM_MARQUE': 'ÉQUIPE marque',
        'EXT_MARQUE': 'ÉQUIPE marque',
        'DOM_2PLUS': 'ÉQUIPE marque 2+',
        'EXT_2PLUS': 'ÉQUIPE marque 2+',
        'HCP_H_+1': 'ÉQUIPE +1',
        'HCP_A_+1': 'ÉQUIPE +1',
        'HCP_H_-1': 'ÉQUIPE -1',
        'HCP_A_-1': 'ÉQUIPE -1',
        'HCP_H_-2': 'ÉQUIPE -2',
        'HCP_A_-2': 'ÉQUIPE -2',
        'MRG_H_2': 'ÉQUIPE gagne par 2+',
        'MRG_A_2': 'ÉQUIPE gagne par 2+',
        'MRG_H_3': 'ÉQUIPE gagne par 3+',
        'MRG_A_3': 'ÉQUIPE gagne par 3+',
    }
    if code in fixes:
        return fixes[code]
    s = libelle_opt or code
    s = re.sub(r'^.+? \+(\d) \(.*\)$', r'ÉQUIPE +\1', s)
    s = re.sub(r'^.+? -(\d) \(.*\)$', r'ÉQUIPE -\1', s)
    return s


def facteur_cache(opt: dict) -> str:
    f, code = opt['famille'], opt['code']
    if f == 'Total buts':
        return 'buts'
    if f == 'Mi-temps':
        return 'mitemps' if code.startswith(('HT_OV', 'HT_UN')) else 'ecart'
    if f in ('Handicap', 'Ecart de buts'):
        return 'ecart'
    if code in ('DC_12', '1X2_N'):
        return 'nul'
    return 'ecart'


def _option(code, famille, p, origine, nom_dom, nom_ext, corriger_p=True):
    p_brute = float(p)
    if origine == 'calcul' and corriger_p:
        mk = cle_marche_depuis_code(code)
        p = corriger_marche(p_brute, mk, tables_calibration())
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


def options_depuis_matrice(lh, la, p1, pn, p2, p_over25, nom_dom, nom_ext):
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

    opts = []
    add = opts.append

    add(_option('1X2_1', '1X2', p1, 'marche', nom_dom, nom_ext, corriger_p=False))
    add(_option('1X2_N', '1X2', pn, 'marche', nom_dom, nom_ext, corriger_p=False))
    add(_option('1X2_2', '1X2', p2, 'marche', nom_dom, nom_ext, corriger_p=False))

    add(_option('DC_1X', 'Double chance', p1 + pn, 'marche', nom_dom, nom_ext, False))
    add(_option('DC_X2', 'Double chance', pn + p2, 'marche', nom_dom, nom_ext, False))
    add(_option('DC_12', 'Double chance', p1 + p2, 'marche', nom_dom, nom_ext, False))

    if p_over25 is not None:
        add(_option('OV_2.5', 'Total buts', p_over25, 'marche', nom_dom, nom_ext, False))
        add(_option('UN_2.5', 'Total buts', 1 - p_over25, 'marche', nom_dom, nom_ext, False))
    else:
        add(_option('OV_2.5', 'Total buts', float(M[tot > 2.5].sum()), 'calcul', nom_dom, nom_ext))
        add(_option('UN_2.5', 'Total buts', float(M[tot < 2.5].sum()), 'calcul', nom_dom, nom_ext))

    for seuil in (0.5, 1.5, 3.5, 4.5):
        add(_option(f'OV_{seuil}', 'Total buts', float(M[tot > seuil].sum()),
                    'calcul', nom_dom, nom_ext))
        add(_option(f'UN_{seuil}', 'Total buts', float(M[tot < seuil].sum()),
                    'calcul', nom_dom, nom_ext))

    for n_mrg in (2, 3):
        pd = float(M[ecart >= n_mrg].sum())
        pe = float(M[-ecart >= n_mrg].sum())
        if pd > 0.05:
            add(_option(f'MRG_H_{n_mrg}', 'Ecart de buts', pd, 'calcul', nom_dom, nom_ext))
        if pe > 0.05:
            add(_option(f'MRG_A_{n_mrg}', 'Ecart de buts', pe, 'calcul', nom_dom, nom_ext))

    for k in (1, 2):
        g = float(M[ecart > k].sum())
        if g > 0.15:
            add(_option(f'HCP_H_-{k}', 'Handicap', g, 'calcul', nom_dom, nom_ext))
        g2 = float(M[-ecart > k].sum())
        if g2 > 0.15:
            add(_option(f'HCP_A_-{k}', 'Handicap', g2, 'calcul', nom_dom, nom_ext))

    add(_option('HCP_H_+1', 'Handicap', float(M[ecart + 1 >= 0].sum()),
                'calcul', nom_dom, nom_ext))
    add(_option('HCP_A_+1', 'Handicap', float(M[-ecart + 1 >= 0].sum()),
                'calcul', nom_dom, nom_ext))

    add(_option('HT_1', 'Mi-temps', float(Mh[hecart > 0].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('HT_N', 'Mi-temps', float(Mh[hecart == 0].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('HT_2', 'Mi-temps', float(Mh[hecart < 0].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('HT_OV_0.5', 'Mi-temps', float(Mh[htot > 0.5].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('HT_OV_1.5', 'Mi-temps', float(Mh[htot > 1.5].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('HT_UN_0.5', 'Mi-temps', float(Mh[htot < 0.5].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('HT_UN_1.5', 'Mi-temps', float(Mh[htot < 1.5].sum()), 'calcul', nom_dom, nom_ext))

    add(_option('BTTS_O', 'BTTS', float(M[(i > 0) & (j > 0)].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('BTTS_N', 'BTTS', float(M[~((i > 0) & (j > 0))].sum()), 'calcul', nom_dom, nom_ext))
    add(_option('DOM_MARQUE', 'Une équipe marque', float(M[1:, :].sum()),
                'calcul', nom_dom, nom_ext))
    add(_option('EXT_MARQUE', 'Une équipe marque', float(M[:, 1:].sum()),
                'calcul', nom_dom, nom_ext))
    add(_option('DOM_2PLUS', "Total d'une équipe", float(M[2:, :].sum()),
                'calcul', nom_dom, nom_ext))
    add(_option('EXT_2PLUS', "Total d'une équipe", float(M[:, 2:].sum()),
                'calcul', nom_dom, nom_ext))

    return opts, M


def analyser(cotes_1x2, cotes_ou25=None, nom_dom='Domicile', nom_ext='Extérieur'):
    c1 = _valider_cote(cotes_1x2[0], '1')
    cn = _valider_cote(cotes_1x2[1], 'N')
    c2 = _valider_cote(cotes_1x2[2], '2')
    p1, pn, p2 = devig_puissance([c1, cn, c2])
    marge = 1 / c1 + 1 / cn + 1 / c2 - 1
    if marge < 0 or marge > MARGE_MAX:
        raise AnalyseInvalide(f'Marge 1X2 hors bornes : {marge:.3f}')

    p_over25 = None
    if cotes_ou25:
        o = _valider_cote(cotes_ou25[0], 'OU over')
        u = _valider_cote(cotes_ou25[1], 'OU under')
        p_over25 = _p_over_two_way(o, u)

    lh, la, residu = ajuster(p1, pn, p2, p_over25)
    opts, M = options_depuis_matrice(lh, la, p1, pn, p2, p_over25, nom_dom, nom_ext)
    return {
        'buts_dom_attendus': lh,
        'buts_ext_attendus': la,
        'p1': p1,
        'pn': pn,
        'p2': p2,
        'p_over25': p_over25,
        'score_probable': score_probable(M),
        'profil': profil_match(p1, p2),
        'marge_marche': marge,
        'residu': residu,
        'douteuse': residu > RESIDU_DOUTEUX,
        'version_moteur': VERSION_MOTEUR,
        'incoherence': verifier_coherence(opts),
        'options': opts,
    }


def est_eligible(opt):
    if opt['code'] == CODE_FILET:
        return False
    if opt['famille'] in FAMILLES_PEU_FIABLES:
        return False
    if opt['famille'] in FIABLES:
        return True
    if opt['famille'] == '1X2' and opt['probabilite'] < P_1X2_MAX_RECO:
        return True
    if opt['famille'] not in FAMILLES_ELIGIBLES:
        return False
    if opt['famille'] == '1X2' and opt['probabilite'] >= P_1X2_MAX_RECO:
        return False
    return True


def _dans_bande(p, niveau):
    lo, hi = BANDES[niveau]
    return lo <= p < hi if niveau != 'audacieuse' else lo <= p <= hi


def _cout_choix(opt, profil, moyennes):
    d = 0.0
    key = opt['code']
    if moyennes and key in moyennes:
        d = -2.2 * abs(opt['probabilite'] - moyennes[key])
    return (
        FIABILITE.get(opt['famille'], 3.0)
        + ROUTAGE.get(profil, {}).get(opt['famille'], 0.3)
        + d
    )


def choisir_trois(options, profil, moyennes, codes_eviter=None, compteur_formes=None):
    """Classe 3 tips (familles distinctes) + recommandée + filet ; plafond de formes."""
    eviter = set(codes_eviter or ())
    compteur = compteur_formes if compteur_formes is not None else Counter()
    out = [dict(o) for o in options]
    for o in out:
        o['niveau'] = 'filet' if o['code'] == CODE_FILET else 'detail'
        o['forme'] = forme_pari(o['code'], o.get('libelle', ''))
        o['facteur'] = facteur_cache(o)

    familles_prises = set()
    codes_pris = set()
    formes_prises = set()

    def _candidats(niveau, ignorer_eviter=False):
        bande = []
        for o in out:
            if not est_eligible(o):
                continue
            if o['code'] in codes_pris or o['famille'] in familles_prises:
                continue
            if o['forme'] in formes_prises:
                continue
            if compteur[o['forme']] >= PLAFOND_FORME:
                continue
            if not _dans_bande(o['probabilite'], niveau):
                continue
            if not ignorer_eviter and o['code'] in eviter:
                continue
            bande.append(o)
        if bande:
            bande.sort(key=lambda o: (_cout_choix(o, profil, moyennes), -o['probabilite']))
            return bande
        centre = (BANDES[niveau][0] + BANDES[niveau][1]) / 2
        repli = []
        for o in out:
            if not est_eligible(o):
                continue
            if o['code'] in codes_pris or o['famille'] in familles_prises:
                continue
            if o['forme'] in formes_prises:
                continue
            if compteur[o['forme']] >= PLAFOND_FORME:
                continue
            if not ignorer_eviter and o['code'] in eviter:
                continue
            repli.append(o)
        repli.sort(
            key=lambda o: (
                abs(o['probabilite'] - centre) + _cout_choix(o, profil, moyennes),
            )
        )
        return repli

    for niveau in ('prudente', 'equilibree', 'audacieuse'):
        candidats = _candidats(niveau, ignorer_eviter=False)
        if not candidats and eviter:
            candidats = _candidats(niveau, ignorer_eviter=True)
        if not candidats:
            continue
        choisi = candidats[0]
        choisi['niveau'] = niveau
        familles_prises.add(choisi['famille'])
        codes_pris.add(choisi['code'])
        formes_prises.add(choisi['forme'])
        compteur[choisi['forme']] += 1

    # Recommandée : même famille que la prudente, probabilité max hors filet.
    prudente = next((o for o in out if o.get('niveau') == 'prudente'), None)
    if prudente is not None:
        famille_p = prudente.get('famille')
        candidats_reco = [
            o for o in out
            if o.get('niveau') == 'detail'
            and o.get('famille') == famille_p
            and o.get('code') != CODE_FILET
        ]
        if candidats_reco:
            best = max(candidats_reco, key=lambda o: float(o.get('probabilite') or 0))
            best['niveau'] = 'recommandee'

    return out


def moyennes_par_code(listes_options):
    acc = defaultdict(list)
    for opts in listes_options:
        for o in opts:
            acc[o['code']].append(o['probabilite'])
    return {k: sum(v) / len(v) for k, v in acc.items()}


def lisibilite_match(analyse) -> float:
    """Plus l’entropie 1X2 est basse, plus le match est lisible (prioritaire)."""
    p = [analyse['p1'], analyse['pn'], analyse['p2']]
    ent = -sum(x * np.log(max(x, 1e-9)) for x in p)
    return float(-ent)


def classer_journee(analyses):
    """Portefeuille journée : matchs lisibles d’abord + plafond 3 formes."""
    moy = moyennes_par_code(a['options'] for a in analyses)
    compteur = Counter()
    ordre = sorted(range(len(analyses)), key=lambda i: -lisibilite_match(analyses[i]))
    for i in ordre:
        a = analyses[i]
        a['options'] = choisir_trois(a['options'], a['profil'], moy, compteur_formes=compteur)

    sels = [selections_niveaux(a['options']) for a in analyses]
    exclus = set()
    if len(sels) >= 3 and uniformite_excessive(sels):
        for niveau in ('prudente', 'equilibree', 'audacieuse'):
            codes = [s.get(niveau) for s in sels if s.get(niveau)]
            if not codes:
                continue
            code_dom, count = Counter(codes).most_common(1)[0]
            if count > len(sels) / 3:
                exclus.add(code_dom)
    if exclus:
        compteur2 = Counter()
        for i in ordre:
            a = analyses[i]
            a['options'] = choisir_trois(
                a['options'], a['profil'], moy,
                codes_eviter=exclus, compteur_formes=compteur2,
            )
            a['uniformite_corrigee'] = True
    else:
        for a in analyses:
            a['uniformite_corrigee'] = False
    return analyses


def uniformite_excessive(selections, seuil=1 / 3):
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


def selections_niveaux(options):
    return {o['niveau']: o['code'] for o in options if o['niveau'] in BANDES}
