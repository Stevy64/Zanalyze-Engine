# Zanalyze Engine

Moteur d’analyse football **indépendant** de la PWA [ZanalyZe](https://github.com/Stevy64/ZanalyZ).

- Ingest **SofaScore** (calendrier, cotes, scores, H2H optionnel)
- Modèles **v3.1** (de-vig, Poisson / Dixon–Coles, calibration marché, tips journée)
- Sortie : **snapshot JSON v1** consommé par ZanalyZe (`importer_snapshot`)

PythonAnywhere **ne peut pas** appeler SofaScore (whitelist). Ce repo tourne ailleurs (Actions, VPS, PC) et **pousse** `exports/matchs.json`.

## Local

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate
pip install -r requirements.txt
python -m engine refresh --pages 1 --passes 1
python -m engine serve   # http://127.0.0.1:8001/docs
```

API :

| Méthode | Chemin | Rôle |
|---------|--------|------|
| GET | `/health` | liveness |
| POST | `/v1/analyser` | analyses + classement journée |
| POST | `/v1/analyser-un` | un match |
| POST | `/v1/sync` | ingest (+ refresh si `calculer`) |
| GET | `/v1/snapshot` | contrat v1 ZanalyZe |

Si `ENGINE_TOKEN` est défini, `/v1/sync` et `/v1/snapshot` exigent `X-Engine-Token`.

## GitHub Actions (gratuit)

Le workflow `.github/workflows/refresh-snapshot.yml` tourne toutes les ~2 h, écrit `exports/matchs.json` et commit.

Sur **PythonAnywhere** (ZanalyZe) :

```bash
python manage.py importer_snapshot --url https://raw.githubusercontent.com/Stevy64/zanalyze-engine/main/exports/matchs.json
```

(`github.com` / `raw.githubusercontent.com` sont en général sur la whitelist PA.)

Si SofaScore répond 403 depuis les IP GitHub : voir [docs/oracle.md](docs/oracle.md) (Always Free, egress libre).

## Docker

```bash
docker build -t zanalyze-engine .
docker run --rm -p 8001:8001 zanalyze-engine
# worker :
docker run --rm zanalyze-engine sh /app/deploy/worker-loop.sh
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
