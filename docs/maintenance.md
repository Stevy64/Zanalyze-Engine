# Maintenance

## Commandes du quotidien

```bash
python -m engine refresh --pages 1 --passes 1   # sync + analyses + JSON
python -m engine serve                            # API :8001 /docs
pytest                                            # sans réseau
```

Logs Actions : onglet **Actions** → dernier **Refresh snapshot**.

## Ajouter une compétition

1. `engine/sofascore.py` → dict `TOURNOIS` (id SofaScore `uniqueTournament`).
2. Relancer `refresh`. Le code (`PL`, `LIGA`, …) doit rester aligné avec la PWA.

## Changer le moteur (v3.1)

Modifier `moteur.py` / `calibrage.py`, bump `VERSION_MOTEUR`, lancer `pytest`.  
Ne pas importer Django.

## Le snapshot PWA est vide / périmé

1. Vérifier qu’Actions a bien commité `exports/matchs.json`.
2. Sur PA : `importer_snapshot --url …` (raw GitHub).
3. Filtre date de l’UI = matchs dans la fenêtre `--jours 21`.

## SofaScore 403

- En local : souvent User-Agent / `curl_cffi` (déjà le défaut).
- Sur Actions : IP datacenter bloquée → VM Oracle ([oracle.md](oracle.md)).

## Ne pas faire

- Committer `data/engine.sqlite3` ou `.env`.
- Appeler SofaScore depuis PythonAnywhere.
- Changer les noms de clés du snapshot sans migrer la PWA.
