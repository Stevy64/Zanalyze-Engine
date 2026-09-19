# CONTRIBUTING / maintenance rapide

## Structure

```
engine/                  code métier (pas de Django)
docs/                    architecture, apprentissage, Actions, maintenance
.github/workflows/
  refresh-snapshot.yml   cycle complet, cron ~2 h + manuel
  ci.yml                 pytest
exports/matchs.json      artefact consommé par la PWA
data/archive/            mémoire longue, versionnée
data/calibration.json    courbes apprises, versionnées
tests/                   sans réseau
```

## Avant de merger

```bash
pip install -r requirements-dev.txt
pytest
```

## Après un changement du snapshot

1. Vérifier les clés dans `engine/snapshot.py`.
2. `tests/test_snapshot.py` encode les contraintes des modèles Django de la
   PWA : longueurs de champs, listes de choix, codes d'options reconnus. Le
   faire passer **avant** de déployer.
3. Tester `importer_snapshot` côté PWA.
4. Documenter dans `docs/architecture.md` si le contrat bouge.

## Deux règles à ne pas contourner

- Une prédiction n'est jamais réécrite, et un match commencé n'est jamais
  analysé (`store.matchs_a_analyser`).
- Une calibration ne se publie que si elle gagne hors échantillon
  (`apprentissage.evaluer_candidate`).

## Liens

- Activer Actions : [docs/actions.md](docs/actions.md)
- Comment le moteur apprend : [docs/apprentissage.md](docs/apprentissage.md)
- PWA : https://github.com/Stevy64/Zanalyze
