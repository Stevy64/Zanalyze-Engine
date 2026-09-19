"""
Zanalyze Engine — ingestion ESPN, modèles v4, apprentissage continu.

Cartographie
------------
identite.py       identité canonique des clubs et des rencontres
qualite.py        contrôle de plausibilité avant calcul
paths.py          chemins data/ exports/ archive/
store.py          SQLite v2 : ids par fournisseur, cotes historisées, prédictions
espn.py           ingestion ESPN : calendrier, scores, cotes avec leur ligne
moteur.py         probabilités, calibration, sélection (fonctions pures)
calibrage.py      courbes de correction par marché, et par ligue si le volume suit
calibration_store.py   lecture / écriture des courbes apprises
apprentissage.py  mesure, apprend, valide hors échantillon, publie si meilleur
archive.py        mémoire longue versionnée avec le dépôt
pipeline.py       ingestion → analyse figée → règlement → archive → snapshot
snapshot.py       contrat v1 consommé par la PWA Zanalyze
evaluation.py     verdict gagné / perdu d'un code d'option
app.py            API FastAPI
cli.py            `python -m engine refresh|calibrer|bilan|archive|…`

La PWA n'appelle pas le moteur : elle importe exports/matchs.json.
"""

from engine.moteur import VERSION_MOTEUR

__version__ = VERSION_MOTEUR
