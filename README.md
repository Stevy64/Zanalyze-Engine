# Zanalyze Engine

Moteur d’analyse football **indépendant** de la PWA **[Zanalyze](https://github.com/Stevy64/Zanalyze)**.

- Ingest **ESPN** (défaut, OK GitHub Actions) — SofaScore en option local
- Modèles **v3.1** (de-vig, Poisson / Dixon–Coles, calibration, tips)
- Sortie : **snapshot JSON v1** → `importer_snapshot` côté PWA

| Doc | Contenu |
|-----|---------|
| [docs/actions.md](docs/actions.md) | Activer Actions + provider ESPN |
| [docs/architecture.md](docs/architecture.md) | Modules, contrat snapshot |
| [docs/maintenance.md](docs/maintenance.md) | Routine, providers |
| [docs/oracle.md](docs/oracle.md) | Always Free (optionnel) |

## Prise en main locale

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
python -m engine refresh --provider espn --jours 21
python -m engine serve   # http://127.0.0.1:8001/docs
```

## GitHub Actions

Workflow **Refresh snapshot** : `ENGINE_PROVIDER=espn`, toutes les ~2 h.

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
