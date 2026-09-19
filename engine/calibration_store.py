"""Overrides de calibration (JSON local, sans Django)."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.calibrage import CALIBRATION_MARCHE_DEFAUT, SEP_LIGUE
from engine.paths import CALIBRATION_FILE, ensure_dirs


def chemin_calibration() -> Path:
    return CALIBRATION_FILE


# Anciennes clés « famille » du schéma v3.0. Leur correction unique cassait
# la cohérence des complémentaires : on les ignore si elles traînent encore.
_CLES_FAMILLE_OBSOLETES = frozenset({
    'Total buts', 'Mi-temps', 'Handicap', 'BTTS', 'Double chance', '1X2',
    'Ecart de buts', 'Une équipe marque', 'Une equipe marque',
    "Total d'une équipe",
})


def _cle_valide(cle: str) -> bool:
    """Une clé est soit un marché connu, soit « LIGUE|marché »."""
    if cle in _CLES_FAMILLE_OBSOLETES:
        return False
    if cle in CALIBRATION_MARCHE_DEFAUT:
        return True
    if SEP_LIGUE in cle:
        _, _, marche = cle.partition(SEP_LIGUE)
        return marche in CALIBRATION_MARCHE_DEFAUT
    return False


def charger_overrides_marche() -> dict[str, list[tuple[float, float]]]:
    """Courbes apprises, lues depuis `data/calibration.json`."""
    path = chemin_calibration()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[tuple[float, float]]] = {}
    for cle, points in (raw.get('tables') or {}).items():
        if not isinstance(points, list) or len(points) < 2 or not _cle_valide(cle):
            continue
        try:
            out[cle] = [(float(a), float(b)) for a, b in points]
        except (TypeError, ValueError):
            continue
    return out


def charger_tables() -> dict[str, list[tuple[float, float]]]:
    tables = deepcopy(CALIBRATION_MARCHE_DEFAUT)
    tables.update(charger_overrides_marche())
    return tables


def meta_calibration() -> dict[str, Any]:
    path = chemin_calibration()
    if not path.is_file():
        return {'version': 0, 'existe': False, 'schema': 'marche_v31'}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'version': 0, 'existe': False, 'schema': 'marche_v31'}
    return {
        'existe': True,
        'version': int(raw.get('version') or 0),
        'updated_at': raw.get('updated_at'),
        'echantillons': raw.get('echantillons') or {},
        'schema': raw.get('schema') or 'marche_v31',
    }


def sauver_tables(
    tables: dict[str, list[tuple[float, float]]],
    *,
    echantillons: dict[str, int] | None = None,
    version: int | None = None,
) -> Path:
    ensure_dirs()
    path = chemin_calibration()
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = meta_calibration()
    ver = version if version is not None else int(prev.get('version') or 0) + 1
    payload = {
        'version': ver,
        'schema': 'marche_v31',
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'echantillons': echantillons or {},
        'tables': {
            cle: [[round(a, 4), round(b, 4)] for a, b in pts]
            for cle, pts in tables.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    try:
        from engine import calibrage, moteur
        calibrage.invalider_cache()
        moteur.invalider_calibration_cache()
    except Exception:  # noqa: BLE001
        pass
    return path
