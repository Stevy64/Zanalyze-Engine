# -*- coding: utf-8 -*-
"""Calibration MARCHE PAR MARCHE (moteur v3.1), avec cohérence des complémentaires.

Validé hors échantillon sur 224 163 observations :
  aucune correction .............. Brier 0,18999
  correction par famille (v3.0) .. Brier 0,19138  (pire que ne rien faire)
  correction par marché (v3.1) ... Brier 0,18782
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

_ICI = Path(__file__).resolve().parent
_FICHIER_DEFAUT = _ICI / 'calibration_par_marche.json'

COMPLEMENT = '~'

# Clé de marché par code d'option ZanalyZ (stable, indépendant du libellé).
CODE_VERS_MARCHE: dict[str, Any] = {
    'OV_0.5': '+0.5',
    'OV_1.5': '+1.5',
    'OV_2.5': '+2.5',
    'OV_3.5': '+3.5',
    'OV_4.5': '+4.5',
    'UN_0.5': (COMPLEMENT, '+0.5'),
    'UN_1.5': (COMPLEMENT, '+1.5'),
    'UN_2.5': (COMPLEMENT, '+2.5'),
    'UN_3.5': (COMPLEMENT, '+3.5'),
    'UN_4.5': (COMPLEMENT, '+4.5'),
    '1X2_1': '1',
    '1X2_N': 'N',
    '1X2_2': '2',
    'DC_1X': '1X',
    'DC_X2': 'X2',
    'DC_12': '12',
    'HT_OV_0.5': 'MT +0.5',
    'HT_OV_1.5': 'MT +1.5',
    'HT_UN_0.5': (COMPLEMENT, 'MT +0.5'),
    'HT_UN_1.5': (COMPLEMENT, 'MT +1.5'),
    'HT_1': 'MT 1',
    'HT_2': 'MT 2',
    'HT_N': 'MT N',
    'BTTS_O': 'BTTS oui',
    'BTTS_N': (COMPLEMENT, 'BTTS oui'),
    'DOM_MARQUE': 'dom marque',
    'EXT_MARQUE': 'ext marque',
    'DOM_2PLUS': 'dom +1.5',
    'EXT_2PLUS': 'ext +1.5',
    'HCP_H_+1': 'HC dom +1',
    'HCP_A_+1': 'HC dom +1',
    'HCP_H_-1': 'HC dom -1',
    'HCP_A_-1': 'HC dom -1',
    'HCP_H_-2': 'HC dom -2',
    'HCP_A_-2': 'HC dom -2',
    'MRG_H_2': 'HC dom -1',
    'MRG_A_2': 'HC dom -1',
    'MRG_H_3': 'HC dom -2',
    'MRG_A_3': 'HC dom -2',
}


def _charger_json(path: Path) -> dict[str, list[tuple[float, float]]]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    out: dict[str, list[tuple[float, float]]] = {}
    for cle, points in raw.items():
        try:
            out[cle] = [(float(a), float(b)) for a, b in points]
        except (TypeError, ValueError):
            continue
    return out


CALIBRATION_MARCHE_DEFAUT = _charger_json(_FICHIER_DEFAUT)

_CACHE: dict[str, list[tuple[float, float]]] | None = None


def invalider_cache() -> None:
    global _CACHE
    _CACHE = None


def tables_marche() -> dict[str, list[tuple[float, float]]]:
    """Tables actives = défaut v3.1 fusionné avec data/calibration.json si présent."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    tables = deepcopy(CALIBRATION_MARCHE_DEFAUT)
    try:
        from engine.calibration_store import charger_overrides_marche
        overrides = charger_overrides_marche()
        for cle, pts in overrides.items():
            if isinstance(pts, list) and len(pts) >= 2:
                tables[cle] = pts
    except Exception:  # noqa: BLE001 — hors Django / fichier absent
        pass
    _CACHE = tables
    return _CACHE


def _interp(p: float, table: list[tuple[float, float]]) -> float:
    xs = [a for a, _ in table]
    ys = [b for _, b in table]
    return float(np.clip(np.interp(p, xs, ys), 0.005, 0.995))


def cle_marche_depuis_code(code: str | None) -> Any:
    if not code:
        return None
    return CODE_VERS_MARCHE.get(code)


def cle_marche(libelle: str, dom: str = '', ext: str = '') -> Any:
    """Traduit un libellé français en clé (repli si pas de code)."""
    L = libelle
    OV = {
        'Au moins 1 but': '+0.5',
        'Au moins 2 buts': '+1.5',
        'Au moins 3 buts': '+2.5',
        'Plus de 2,5 buts': '+2.5',
        'Au moins 4 buts': '+3.5',
        'Plus de 3,5 buts': '+3.5',
        'Au moins 5 buts': '+4.5',
        'Plus de 4,5 buts': '+4.5',
    }
    UN = {
        'Aucun but': '+0.5',
        'Moins de 2 buts': '+1.5',
        'Moins de 2,5 buts': '+2.5',
        'Moins de 3 buts': '+2.5',
        'Moins de 3,5 buts': '+3.5',
        'Moins de 4 buts': '+3.5',
        'Moins de 4,5 buts': '+4.5',
        'Moins de 5 buts': '+4.5',
    }
    if L in OV:
        return OV[L]
    if L in UN:
        return (COMPLEMENT, UN[L])
    if L in ('Pas de match nul', 'Pas de nul'):
        return '12'
    if L == 'Match nul':
        return 'N'
    if dom and L == f'{dom} ne perd pas':
        return '1X'
    if ext and L == f'{ext} ne perd pas':
        return 'X2'
    if dom and L == f'{dom} gagne':
        return '1'
    if ext and L == f'{ext} gagne':
        return '2'
    if L == 'Au moins 1 but avant la pause':
        return 'MT +0.5'
    if L == 'Au moins 2 buts avant la pause':
        return 'MT +1.5'
    if L in ('0-0 a la pause', '0-0 à la pause', 'Aucun but avant la pause'):
        return (COMPLEMENT, 'MT +0.5')
    if L == 'Moins de 1,5 but avant la pause':
        return (COMPLEMENT, 'MT +1.5')
    if dom and L in (f'{dom} mene a la pause', f'{dom} mène à la pause'):
        return 'MT 1'
    if ext and L in (f'{ext} mene a la pause', f'{ext} mène à la pause'):
        return 'MT 2'
    if L in ('Egalite a la pause', 'Égalité à la pause', 'Nul à la pause'):
        return 'MT N'
    if L == 'Les deux equipes marquent' or L == 'Les deux équipes marquent':
        return 'BTTS oui'
    if L in (
        'Une equipe au moins ne marque pas',
        'Au moins une équipe ne marque pas',
    ):
        return (COMPLEMENT, 'BTTS oui')
    if dom and L == f'{dom} marque':
        return 'dom marque'
    if ext and L == f'{ext} marque':
        return 'ext marque'
    if dom and L == f'{dom} marque 2 buts ou plus':
        return 'dom +1.5'
    if ext and L == f'{ext} marque 2 buts ou plus':
        return 'ext +1.5'
    if 'ne perd pas de plus' in L or '+1' in L:
        return 'HC dom +1'
    if 'gagne par 2 buts ou plus' in L or ' -1 ' in L:
        return 'HC dom -1'
    if 'gagne par 3 buts ou plus' in L or ' -2 ' in L:
        return 'HC dom -2'
    return None


SEP_LIGUE = '|'


def _table(tmap: dict, marche: str, ligue: str | None):
    """Courbe applicable : celle de la ligue si elle existe, sinon la générale.

    Une courbe par ligue n'est publiée par l'apprentissage que lorsqu'elle
    repose sur assez d'observations ; ici on se contente de la préférer.
    """
    if ligue:
        t = tmap.get(f'{ligue}{SEP_LIGUE}{marche}')
        if t:
            return t
    return tmap.get(marche)


def corriger(p: float, marche: Any, tables: dict | None = None,
             ligue: str | None = None) -> float:
    """Corrige une proba CALCULÉE. Ne jamais appliquer aux cotes marché."""
    if marche is None:
        return float(p)
    tmap = tables if tables is not None else tables_marche()
    if isinstance(marche, tuple) and marche and marche[0] == COMPLEMENT:
        t = _table(tmap, marche[1], ligue)
        return 1.0 - _interp(1.0 - float(p), t) if t else float(p)
    t = _table(tmap, marche, ligue) if isinstance(marche, str) else None
    return _interp(float(p), t) if t else float(p)


def verifier_coherence(options: list[dict]) -> float:
    """Écart max d'une paire complémentaire à 1.0 (doit rester ~0)."""
    idx = {o.get('libelle'): o.get('probabilite', o.get('p')) for o in options}
    paires = [
        ('Au moins 2 buts', 'Moins de 2 buts'),
        ('Au moins 3 buts', 'Moins de 3 buts'),
        ('Plus de 2,5 buts', 'Moins de 2,5 buts'),
        ('Au moins 4 buts', 'Moins de 4 buts'),
        ('Plus de 3,5 buts', 'Moins de 3,5 buts'),
        ('Au moins 5 buts', 'Moins de 5 buts'),
        ('Plus de 4,5 buts', 'Moins de 4,5 buts'),
        ('Au moins 1 but avant la pause', 'Aucun but avant la pause'),
        ('Les deux équipes marquent', 'Au moins une équipe ne marque pas'),
    ]
    pires = []
    for a, b in paires:
        if a in idx and b in idx and idx[a] is not None and idx[b] is not None:
            pires.append(abs(float(idx[a]) + float(idx[b]) - 1))
    return max(pires, default=0.0)
