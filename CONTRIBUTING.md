# CONTRIBUTING / maintenance rapide

## Structure

```
engine/          code métier (pas de Django)
docs/            prise en main, Actions, Oracle, architecture
.github/workflows/
  refresh-snapshot.yml   cron ~2 h + manuel
  ci.yml                 pytest
exports/matchs.json      artefact consommé par la PWA
tests/                   sans réseau
```

## Avant de merger

```bash
pip install -r requirements-dev.txt
pytest
```

## Après un changement snapshot

1. Vérifier les clés dans `engine/snapshot.py`
2. Tester `importer_snapshot` côté PWA Zanalyze
3. Documenter dans `docs/architecture.md` si le contrat bouge

## Liens

- Activer Actions : [docs/actions.md](docs/actions.md)
- PWA : https://github.com/Stevy64/Zanalyze
