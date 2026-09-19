# Zanalyze Engine

Moteur d'analyse football **indépendant** de la PWA **[Zanalyze](https://github.com/Stevy64/Zanalyze)**.

Il ingère le calendrier et les cotes, en déduit des probabilités marché par
marché, fige ses prédictions avant le coup d'envoi, mesure ce qu'elles valent
une fois les matchs joués, et se recalibre tout seul — mais seulement quand la
nouvelle calibration fait mieux que l'ancienne hors échantillon.

Sortie : **snapshot JSON v1** → `importer_snapshot` côté PWA. Le contrat n'a
pas changé depuis la v3.

## Ce que fait la v4 que la v3.1 ne faisait pas

| | v3.1 | v4 |
|---|---|---|
| Ligne de totaux | supposée valoir 2,5 | **lue** sur le flux, et le modèle s'y ajuste |
| Identité des clubs | un id partagé par deux fournisseurs, avec collisions | clé canonique dérivée du nom |
| Prédictions | réécrites en boucle, même pendant le match | figées, horodatées, en ajout seul |
| Mémoire | cache CI volatil | archive CSV versionnée avec le dépôt |
| Calibration | fichier figé, non reproductible | apprise, validée hors échantillon, publiée si meilleure |
| Recommandée | la probabilité la plus haute | la meilleure utilité dans une fenêtre sûre |
| Matchs douteux | affichés tels quels | audités et écartés |
| Matchs sans cotes | aucune analyse | estimés par les forces d'équipe |
| Constantes du modèle | supposées | mesurées sur 7 668 matchs, 8 compétitions |
| Arbitrage des courbes | score de Brier, aveugle à la dérive | erreur de calibration, Brier en garde-fou |

Le détail de chaque point est dans [docs/architecture.md](docs/architecture.md).

**Vérifiée avant migration** sur 6 305 matchs réels de trois saisons, puis sur
1 363 matchs de Ligue des champions et de Ligue Europa sur cinq saisons : sur
les lignes 2,5 les deux moteurs sont identiques au bit près, et la v4 gagne
3,3 % de log-perte hors calibration dès que le book cote une autre ligne —
jusqu'à 13 % sur les marchés de buts de ces rencontres. Son écart entre
annoncé et réalisé sur la Recommandée tombe de +2,1 à +0,1 point.
Chiffres et protocole dans [docs/verification-v4.md](docs/verification-v4.md).

## Prise en main

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest

python -m engine refresh --jours 21   # cycle complet
python -m engine serve                # http://127.0.0.1:8001/docs
```

## Commandes

| Commande | Rôle |
|---|---|
| `refresh` | ingestion → analyse → règlement → archive → calibration → snapshot |
| `sync` | ingestion seule |
| `analyser` | analyse des matchs à venir, sans réseau |
| `snapshot` | réécrit `exports/matchs.json` depuis la base |
| `audit` | doublons, chevauchements de calendrier, scores aberrants |
| `calibrer` | apprend une calibration, la publie **si** elle gagne (`--essai` pour mesurer sans publier) |
| `bilan` | écart annoncé / observé par marché, avec sa marge d'erreur |
| `archive` | `exporter`, `stats`, `importer` (championnats), `importer-coupes` (C1/C3) |
| `serve` | API FastAPI |

## GitHub Actions

Workflow **Refresh snapshot**, toutes les 2 h. Il commite trois choses :

```text
exports/matchs.json      ce que lit la PWA
data/archive/*.csv       la mémoire longue (prédictions réglées, résultats)
data/calibration.json    ce que le moteur a appris
```

La base SQLite reste dans le cache Actions : elle est reconstructible, les
trois fichiers ci-dessus ne le sont pas. **Ne les ajoutez pas au `.gitignore`.**

URL du JSON pour la PWA :

```text
https://raw.githubusercontent.com/Stevy64/Zanalyze-Engine/main/exports/matchs.json
```

## API

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/health` | liveness |
| POST | `/v1/analyser` | analyses + classement d'un lot |
| POST | `/v1/analyser-un` | un match |
| POST | `/v1/refresh` | cycle complet |
| GET | `/v1/snapshot` | contrat v1 |
| GET | `/v1/bilan` | écart annoncé / observé par marché |

Sur `/v1/analyser`, une cote de totaux **doit** être accompagnée de sa ligne :
`{"over": 1.83, "under": 1.95, "ligne": 3.5}`. Sans ligne, la requête est
refusée — c'est exactement l'erreur que la v4 corrige.

`ENGINE_TOKEN` défini ⇒ `/v1/refresh`, `/v1/snapshot` et `/v1/bilan` exigent
`X-Engine-Token`.

## Docker

```bash
docker compose up --build
docker compose --profile worker up   # boucle refresh ~2 h
```

## Documentation

| Doc | Contenu |
|---|---|
| [docs/architecture.md](docs/architecture.md) | modules, contrat snapshot, décisions |
| [docs/apprentissage.md](docs/apprentissage.md) | comment le moteur s'améliore, et ce qui l'en empêche |
| [docs/verification-v4.md](docs/verification-v4.md) | v3.1 contre v4 sur 3 saisons : protocole et résultats |
| [docs/actions.md](docs/actions.md) | activer GitHub Actions |
| [docs/maintenance.md](docs/maintenance.md) | routine, incidents courants |
| [docs/oracle.md](docs/oracle.md) | API 24/7 sur un VPS (optionnel) |
