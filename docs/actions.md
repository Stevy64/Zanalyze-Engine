# Activer GitHub Actions

L’écran **« Choose a workflow »** apparaît si aucun YAML n’est sur `main`, ou si Actions est désactivé.

## Source de données (ESPN)

Par défaut le moteur utilise **ESPN** (JSON public, sans clé) :

- calendrier PL / LIGA / L1 / SA / UCL
- scores
- cotes 1X2 + OU 2.5

SofaScore est **bloqué** sur les IP GitHub ; ESPN fonctionne.  
Forcer SofaScore en local : `ENGINE_PROVIDER=sofascore` (nécessite `curl_cffi`).

## 1. Autoriser les workflows (une fois)

1. Repo **Zanalyze-Engine** → **Settings** → **Actions** → **General**.
2. **Allow all actions** + **Read and write** → **Save**.

## 2. Lancer Refresh snapshot

Actions → **Refresh snapshot** → **Run workflow**.

Le job publie `exports/matchs.json` non vide (~toutes les 2 h ensuite).

## 3. Côté PWA Zanalyze (PythonAnywhere)

```bash
python manage.py importer_snapshot --url https://raw.githubusercontent.com/Stevy64/Zanalyze-Engine/main/exports/matchs.json
```
