# Zanalyze Engine

Moteur d’analyse football **indépendant** de la PWA **[Zanalyze](https://github.com/Stevy64/Zanalyze)**.

- Ingest **SofaScore** (calendrier, cotes, scores)
- Modèles **v3.1** (de-vig, Poisson / Dixon–Coles, calibration, tips)
- Sortie : **snapshot JSON v1** → `python manage.py importer_snapshot` côté PWA

PythonAnywhere **blackliste** SofaScore. Ici on tourne sur **GitHub Actions** (gratuit) ou un VPS, et on publie `exports/matchs.json`.

| Doc | Contenu |
|-----|---------|
| [docs/actions.md](docs/actions.md) | **Activer Actions** (l’écran « Choose a workflow ») |
| [docs/architecture.md](docs/architecture.md) | Modules, contrat snapshot |
| [docs/maintenance.md](docs/maintenance.md) | Routine, 403, compétitions |
| [docs/oracle.md](docs/oracle.md) | Always Free si Actions est bloqué |

## Prise en main locale

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
python -m engine refresh --pages 1 --passes 1
python -m engine serve   # http://127.0.0.1:8001/docs
```

## GitHub Actions

Le YAML est déjà dans `.github/workflows/`. Si tu vois les **modèles** Docker/Django au lieu de « Refresh snapshot », suis **[docs/actions.md](docs/actions.md)**.

**SofaScore bloque souvent les IP Actions** → le job échoue volontairement (pas de JSON vide).  
Refresh fiable depuis ton PC :

```bash
python -m engine refresh --pages 1 --passes 1
git add exports/matchs.json && git commit -m "chore: refresh match snapshot" && git push
```

URL du JSON pour la PWA :

```text
https://raw.githubusercontent.com/Stevy64/Zanalyze-Engine/main/exports/matchs.json
```

## API

| Méthode | Chemin | Rôle |
|---------|--------|------|
| GET | `/health` | liveness |
| POST | `/v1/analyser` | analyses + classement journée |
| POST | `/v1/analyser-un` | un match |
| POST | `/v1/sync` | ingest (+ calcul si `calculer`) |
| GET | `/v1/snapshot` | contrat v1 |

`ENGINE_TOKEN` défini ⇒ `/v1/sync` et `/v1/snapshot` exigent `X-Engine-Token`.

## Docker

```bash
docker compose up --build
docker compose --profile worker up   # boucle refresh ~2 h
```
