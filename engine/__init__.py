"""
Zanalyze Engine — ingest SofaScore + modèles v3.1, sans Django.

Cartographie
------------
paths.py              chemins data/ / exports/ (surchargeables par env)
store.py              SQLite (compétitions, équipes, matchs, cotes, analyses)
espn.py               ingest ESPN (défaut Actions) — calendrier + cotes
sofascore.py          ingest SofaScore (optionnel, souvent 403 sur cloud)
moteur.py + calibrage.py   probabilités v3.1 (pures)
pipeline.py           sync → analyser → snapshot
snapshot.py           JSON v1 consommé par la PWA Zanalyze
app.py                FastAPI
cli.py                `python -m engine refresh|serve|sync|snapshot`

La PWA n’appelle pas SofaScore (PythonAnywhere). Elle importe exports/matchs.json.
"""

from engine.moteur import VERSION_MOTEUR

__version__ = VERSION_MOTEUR
