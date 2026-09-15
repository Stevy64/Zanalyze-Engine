# Maintenance

## Commandes du quotidien

```bash
python -m engine refresh --pages 1 --passes 1   # sync + analyses + JSON
python -m engine serve                            # API :8001 /docs
pytest                                            # sans réseau
```

Logs Actions : onglet **Actions** → dernier **Refresh snapshot**.

## Ajouter une compétition

1. **ESPN (défaut)** : `engine/espn.py` → dict `TOURNOIS` (slug ESPN + `espn_league_id`).
2. **SofaScore (option)** : `engine/sofascore.py` → même codes (`PL`, `LP`, `FAC`, …).
3. PWA : drapeaux dans `static/js/app.js` (`FLAG_BY_CODE` / `FLAG_BY_PAYS`) si besoin.
4. Relancer `refresh`. Les `code` doivent rester alignés entre engine et PWA.

## Changer le moteur (v3.1)

Modifier `moteur.py` / `calibrage.py`, bump `VERSION_MOTEUR`, lancer `pytest`.  
Ne pas importer Django.

## Providers de données

| Provider | Env | GitHub Actions | Notes |
|----------|-----|----------------|-------|
| **espn** (défaut) | `ENGINE_PROVIDER=espn` | OK | Calendrier + cotes DraftKings via ESPN |
| sofascore | `ENGINE_PROVIDER=sofascore` | souvent 403 | Meilleur en local avec `curl_cffi` |

Module : `engine/espn.py` · legacy : `engine/sofascore.py`.

## Le snapshot PWA est vide / périmé

1. Vérifier le dernier **Refresh snapshot** (provider=espn, `crees_ou_maj` > 0).
2. Sur PA : `importer_snapshot --url …`.
3. Filtre date UI = fenêtre `--jours 21`.

## Ne pas faire

- Committer `data/engine.sqlite3` ou `.env`.
- Appeler SofaScore depuis PythonAnywhere.
- Changer les clés du snapshot sans migrer la PWA.
