"""Chemins data/ et exports/ — surcharge par ENGINE_DATA_DIR, ENGINE_DB_PATH, …"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get('ENGINE_DATA_DIR', ROOT / 'data'))
EXPORTS_DIR = Path(os.environ.get('ENGINE_EXPORTS_DIR', ROOT / 'exports'))
DB_PATH = Path(os.environ.get('ENGINE_DB_PATH', DATA_DIR / 'engine.sqlite3'))
CALIBRATION_FILE = Path(
    os.environ.get('ENGINE_CALIBRATION_FILE', DATA_DIR / 'calibration.json')
)
SNAPSHOT_PATH = Path(
    os.environ.get('ENGINE_SNAPSHOT_PATH', EXPORTS_DIR / 'matchs.json')
)
# Mémoire longue du moteur : versionnée avec le dépôt, pas dans le cache CI.
ARCHIVE_DIR = Path(os.environ.get('ENGINE_ARCHIVE_DIR', DATA_DIR / 'archive'))


def ensure_dirs() -> None:
    for d in (DATA_DIR, EXPORTS_DIR, ARCHIVE_DIR, SNAPSHOT_PATH.parent):
        d.mkdir(parents=True, exist_ok=True)
