"""
Force des équipes, estimée sur les seuls résultats.

Pourquoi ce module existe
-------------------------
Tout le reste du moteur part des cotes. C'est une bonne base — le marché est
le meilleur prédicteur unique connu — mais cela condamne le moteur à ne
jamais faire mieux que lui, seulement à le rendre lisible. Pour espérer
apporter quelque chose, il faut une information que le prix ne contient pas
déjà, ou qu'il contient mal.

Ce module estime, pour chaque club, une **force d'attaque** et une **force de
défense**, à partir des scores passés et de rien d'autre. Aucune cote n'entre
dans l'ajustement : c'est ce qui rend le signal indépendant.

Méthode : Dixon–Coles. Deux lois de Poisson couplées, une correction sur les
petits scores (les 0-0 et 1-1 sont plus fréquents que ne le dit Poisson), un
avantage du terrain, et une pondération temporelle qui fait décroître le poids
des vieux matchs.

Le mélange avec le marché est **mesuré, jamais supposé**. `poids_optimal()`
cherche la pondération qui minimise la log-perte sur la période
d'apprentissage ; si le mélange n'apporte rien, elle vaut zéro et le moteur
s'en tient au marché.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence

import numpy as np

# Demi-vie de la pondération temporelle. Un match d'il y a quatre mois pèse
# moitié moins qu'un match d'hier : assez long pour lisser, assez court pour
# suivre un changement d'entraîneur ou de recrutement.
DEMI_VIE_JOURS = 120.0
# Régularisation vers la moyenne. Elle protège les promus et les équipes peu
# vues : sans elle, trois bons résultats suffiraient à fabriquer un cador.
L2_DEFAUT = 0.08
# En dessous, on ne publie pas de forces : l'échantillon ne dit rien.
MIN_MATCHS = 60
MIN_MATCHS_EQUIPE = 4
# Bornes de λ, pour qu'aucune estimation aberrante ne se propage.
LAMBDA_MIN, LAMBDA_MAX = 0.15, 5.0


def _jour(valeur: Any) -> date:
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    return datetime.fromisoformat(str(valeur)[:10]).date()


@dataclass
class Forces:
    """Forces ajustées pour une compétition, à une date donnée."""

    ligue: str
    mu: float
    avantage_terrain: float
    rho: float
    attaque: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)
    matchs_par_equipe: dict[str, int] = field(default_factory=dict)
    n_matchs: int = 0
    arretee_le: str = ''

    def connait(self, equipe: str) -> bool:
        return self.matchs_par_equipe.get(equipe, 0) >= MIN_MATCHS_EQUIPE

    def lambdas(self, dom: str, ext: str) -> tuple[float, float] | None:
        """Buts attendus (domicile, extérieur), ou None si l'un des deux
        clubs est trop peu connu pour qu'on avance quoi que ce soit."""
        if not (self.connait(dom) and self.connait(ext)):
            return None
        lh = math.exp(self.mu + self.attaque.get(dom, 0.0)
                      - self.defense.get(ext, 0.0) + self.avantage_terrain)
        la = math.exp(self.mu + self.attaque.get(ext, 0.0)
                      - self.defense.get(dom, 0.0))
        return (
            float(np.clip(lh, LAMBDA_MIN, LAMBDA_MAX)),
            float(np.clip(la, LAMBDA_MIN, LAMBDA_MAX)),
        )

    def classement(self, n: int = 10) -> list[tuple[str, float]]:
        """Clubs par force nette (attaque moins défense encaissée)."""
        scores = {
            e: self.attaque.get(e, 0.0) + self.defense.get(e, 0.0)
            for e in self.matchs_par_equipe if self.connait(e)
        }
        return sorted(scores.items(), key=lambda kv: -kv[1])[:n]


def _tau_vect(x, y, lh, la, rho):
    """Correction Dixon–Coles sur les quatre scores les plus fréquents."""
    t = np.ones_like(lh, dtype=float)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    t[m00] = 1 - lh[m00] * la[m00] * rho
    t[m01] = 1 + lh[m01] * rho
    t[m10] = 1 + la[m10] * rho
    t[m11] = 1 - rho
    return np.maximum(t, 1e-9)


def ajuster_forces(
    resultats: Sequence[dict[str, Any]],
    *,
    ligue: str = '',
    date_reference: Any = None,
    demi_vie_jours: float = DEMI_VIE_JOURS,
    l2: float = L2_DEFAUT,
) -> Forces | None:
    """Ajuste les forces sur une liste de matchs joués.

    Chaque élément attend `date`, `dom`, `ext`, `bd`, `be`. **Aucune cote
    n'est lue** : c'est la condition pour que le signal soit indépendant du
    marché.

    Renvoie None si l'échantillon est trop maigre.
    """
    from scipy.optimize import minimize

    lignes = [r for r in resultats if r.get('bd') is not None and r.get('be') is not None]
    if len(lignes) < MIN_MATCHS:
        return None

    fin = _jour(date_reference) if date_reference else max(_jour(r['date']) for r in lignes)
    equipes = sorted({r['dom'] for r in lignes} | {r['ext'] for r in lignes})
    index = {e: i for i, e in enumerate(equipes)}
    n = len(equipes)
    if n < 4:
        return None

    ih = np.array([index[r['dom']] for r in lignes])
    ia = np.array([index[r['ext']] for r in lignes])
    x = np.array([int(r['bd']) for r in lignes], dtype=float)
    y = np.array([int(r['be']) for r in lignes], dtype=float)
    age = np.array([(fin - _jour(r['date'])).days for r in lignes], dtype=float)
    poids = np.exp(-math.log(2) * np.maximum(age, 0) / max(demi_vie_jours, 1.0))

    compte: dict[str, int] = defaultdict(int)
    for r in lignes:
        compte[r['dom']] += 1
        compte[r['ext']] += 1

    # Paramètres : [attaque (n), défense (n), mu, avantage terrain, rho].
    def decouper(p):
        atk = p[:n]
        dfn = p[n:2 * n]
        return atk, dfn, p[2 * n], p[2 * n + 1], p[2 * n + 2]

    def perte(p):
        atk, dfn, mu, gamma, rho = decouper(p)
        rho = float(np.clip(rho, -0.25, 0.25))
        lh = np.exp(np.clip(mu + atk[ih] - dfn[ia] + gamma, -3, 2))
        la = np.exp(np.clip(mu + atk[ia] - dfn[ih], -3, 2))
        tau = _tau_vect(x, y, lh, la, rho)
        ll = (np.log(tau) + x * np.log(lh) - lh + y * np.log(la) - la)
        # Régularisation : sans elle, un promu vu quatre fois deviendrait
        # aussi extrême que ses quatre résultats.
        penalite = l2 * (np.sum(atk ** 2) + np.sum(dfn ** 2))
        # Somme des attaques nulle : sinon mu et atk sont interchangeables.
        penalite += 10.0 * (atk.mean() ** 2 + dfn.mean() ** 2)
        return -float(np.sum(poids * ll)) / float(np.sum(poids)) + penalite

    depart = np.zeros(2 * n + 3)
    depart[2 * n] = math.log(1.35)      # mu
    depart[2 * n + 1] = 0.25            # avantage du terrain
    depart[2 * n + 2] = -0.06           # rho
    res = minimize(perte, depart, method='L-BFGS-B',
                   options={'maxiter': 800, 'ftol': 1e-10})
    atk, dfn, mu, gamma, rho = decouper(res.x)
    return Forces(
        ligue=ligue,
        mu=float(mu),
        avantage_terrain=float(gamma),
        rho=float(np.clip(rho, -0.25, 0.25)),
        attaque={e: float(atk[i]) for e, i in index.items()},
        defense={e: float(dfn[i]) for e, i in index.items()},
        matchs_par_equipe=dict(compte),
        n_matchs=len(lignes),
        arretee_le=fin.isoformat(),
    )


def ajuster_par_ligue(
    resultats: Iterable[dict[str, Any]], **kwargs,
) -> dict[str, Forces]:
    """Un jeu de forces par compétition : les clubs ne s'y croisent pas."""
    par_ligue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in resultats:
        par_ligue[r.get('ligue') or ''].append(r)
    out: dict[str, Forces] = {}
    for ligue, lignes in par_ligue.items():
        f = ajuster_forces(lignes, ligue=ligue, **kwargs)
        if f is not None:
            out[ligue] = f
    return out


def melanger(
    lam_marche: tuple[float, float],
    lam_force: tuple[float, float] | None,
    poids: float,
) -> tuple[float, float]:
    """Mélange géométrique des buts attendus.

    Géométrique et non arithmétique : λ est une échelle multiplicative, et
    c'est dans les logs que les deux estimations se combinent sans biais.
    Un poids nul rend exactement le marché.
    """
    if not lam_force or poids <= 0:
        return lam_marche
    w = float(np.clip(poids, 0.0, 1.0))
    lh = math.exp((1 - w) * math.log(lam_marche[0]) + w * math.log(lam_force[0]))
    la = math.exp((1 - w) * math.log(lam_marche[1]) + w * math.log(lam_force[1]))
    return (
        float(np.clip(lh, LAMBDA_MIN, LAMBDA_MAX)),
        float(np.clip(la, LAMBDA_MIN, LAMBDA_MAX)),
    )


def _logloss_1x2(echantillon: Sequence[tuple[tuple[float, float], int]], rho: float) -> float:
    """Log-perte 1X2 d'un jeu de (λ, issue observée) — 0 dom, 1 nul, 2 ext."""
    from engine.moteur import matrice

    total = 0.0
    for (lh, la), issue in echantillon:
        M = matrice(lh, la, rho=rho)
        k = M.shape[0]
        i = np.arange(k)[:, None]
        j = np.arange(k)[None, :]
        p = (float(M[i > j].sum()), float(np.trace(M)), float(M[i < j].sum()))
        total -= math.log(max(p[issue], 1e-9))
    return total / max(len(echantillon), 1)


def poids_optimal(
    echantillon: Sequence[dict[str, Any]],
    *,
    rho: float = -0.06,
    gain_minimal: float = 0.002,
    candidats: Sequence[float] = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50),
) -> tuple[float, dict[str, float]]:
    """Cherche le poids du mélange qui minimise la log-perte 1X2.

    `echantillon` : dicts avec `lam_marche`, `lam_force` et `issue`
    (0 domicile, 1 nul, 2 extérieur), tous **antérieurs** à la période de test.

    Le poids n'est retenu que s'il bat le marché seul d'au moins
    `gain_minimal`. C'est le portillon : un mélange qui n'apporte rien reste
    à zéro, et le moteur continue de suivre le marché.
    """
    utiles = [e for e in echantillon if e.get('lam_force')]
    detail: dict[str, float] = {'echantillon': float(len(utiles))}
    if len(utiles) < 200:
        detail['raison_refus'] = 1.0
        return 0.0, detail

    scores: dict[float, float] = {}
    for w in candidats:
        paires = [
            (melanger(e['lam_marche'], e['lam_force'], w), int(e['issue']))
            for e in utiles
        ]
        scores[w] = _logloss_1x2(paires, rho)
    reference = scores[0.0]
    meilleur = min(scores, key=lambda w: scores[w])
    detail.update({f'logloss_w{w:.2f}': round(scores[w], 5) for w in candidats})
    detail['logloss_marche_seul'] = round(reference, 5)
    detail['meilleur_brut'] = meilleur
    if scores[meilleur] < reference - gain_minimal:
        detail['retenu'] = float(meilleur)
        return float(meilleur), detail
    detail['retenu'] = 0.0
    return 0.0, detail
