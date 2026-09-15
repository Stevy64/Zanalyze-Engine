"""Chemins data/ et exports/ — surcharge ENGINE_DATA_DIR, ENGINE_DB_PATH, ENGINE_SNAPSHOT_PATH."""
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


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
