# Architecture Zanalyze Engine

Deux dépôts GitHub :

| Repo | Rôle |
|------|------|
| [Zanalyze-Engine](https://github.com/Stevy64/Zanalyze-Engine) | Ingest + modèles + snapshot |
| [Zanalyze](https://github.com/Stevy64/Zanalyze) | PWA (affichage, VIP, admin) |

```text
ESPN (défaut) ──► pipeline.py ──► SQLite ──► snapshot v1 JSON
SofaScore (opt) ─►      │                              │
                        └── moteur.py v3.1             └── PWA importer_snapshot
```

PythonAnywhere ne joint pas SofaScore. L’engine tourne sur **Actions avec ESPN** et pousse le JSON.

## Modules `engine/`

| Fichier | Responsabilité | Toucher quand |
|---------|----------------|---------------|
| `sofascore.py` | Legacy ingest SofaScore | debug local `ENGINE_PROVIDER=sofascore` |
| `espn.py` | Ingest ESPN (Actions) | calendrier, cotes, nouveau championnat |
| `store.py` | Schéma SQLite | nouveau champ persisté |
| `moteur.py` | De-vig, Poisson/Dixon–Coles, tips journée | maths / version moteur |
| `calibrage.py` + `calibration_par_marche.json` | Courbes par marché | recalibrage |
| `calibration_store.py` | Overrides `data/calibration.json` | apprentissage hors ligne |
| `pipeline.py` | Orchestre sync + analyse + export | flags CLI, règlement scores |
| `snapshot.py` | Contrat v1 (ne pas casser les clés) | **coordonner avec** `paris/snapshot.py` de la PWA |
| `evaluation.py` | Gagné/perdu d’un code d’option | nouveaux marchés |
| `app.py` | FastAPI | nouveaux endpoints |
| `cli.py` | Entrée `python -m engine` | nouvelles commandes |

## Contrat snapshot v1

Clés stables attendues par la PWA : `version`, `competitions`, `equipes`, `matchs[]` avec `sofascore_id`, `cotes`, `analyse.options`.  
Toute renommage casse `importer_snapshot`.

## Variables d’environnement

Voir `.env.example`. Les plus utiles : `ENGINE_TOKEN`, `ENGINE_SYNC_PAGES`, `ENGINE_DB_PATH`, `ENGINE_SNAPSHOT_PATH`.
