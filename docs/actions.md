# Activer GitHub Actions

L'écran **« Choose a workflow »** apparaît si aucun YAML n'est sur `main`, ou
si Actions est désactivé sur le dépôt.

## Source de données

Le moteur utilise **ESPN** (JSON public, sans clé) : calendrier, scores, cotes
1X2 et totaux **avec leur ligne**. C'est le seul fournisseur depuis la v4 ;
voir [architecture.md](architecture.md) pour la raison.

## 1. Autoriser les workflows (une fois)

1. Dépôt **Zanalyze-Engine** → **Settings** → **Actions** → **General**.
2. **Allow all actions** + **Read and write permissions** → **Save**.

La permission d'écriture est indispensable : le workflow commite le snapshot,
l'archive et la calibration.

## 2. Lancer Refresh snapshot

Actions → **Refresh snapshot** → **Run workflow**.

Le job publie `exports/matchs.json`, met à jour `data/archive/` et, si la
recalibration gagne hors échantillon, `data/calibration.json`. Cron : toutes
les 2 h, à `15 */2` UTC.

Il échoue volontairement si le snapshot est vide, s'il contient deux fois la
même rencontre, ou si deux équipes partagent un identifiant : ce sont les
régressions que la v4 a supprimées.

### Quotas GitHub Actions (offre gratuite)

| Type de dépôt | Minutes |
|---|---|
| **Public** | illimité (runners standard) |
| **Privé** | ~2 000 min/mois |

Ce workflow : ~12 exécutions/jour, 1 à 2 min chacune, soit environ
**500 min/mois** — largement sous le plafond privé. `timeout-minutes: 20`,
cache SQLite et `concurrency` limitent les dérives.

## 3. Côté PWA Zanalyze (PythonAnywhere)

Les scores et bilans viennent **uniquement** du snapshot du moteur.

Tâche planifiée PA, toutes les 2 h, décalée du cron du moteur :

```bash
cd ~/Zanalyze
source ~/.virtualenvs/zanalyz/bin/activate
set -a && source .env && set +a
python manage.py importer_snapshot --url "$ZANALYZ_SNAPSHOT_URL"
```

Import manuel :

```bash
python manage.py importer_snapshot --url https://raw.githubusercontent.com/Stevy64/Zanalyze-Engine/main/exports/matchs.json
```

### Au premier import de la v4

Les slugs d'équipes changent pour les clubs dont l'identité était corrompue.
`_upsert_equipe` côté PWA retrouve les lignes par slug puis par nom, et
`_upsert_match` par `(domicile, exterieur, coup_denvoi)` : la reprise se fait
donc toute seule, import après import. Vérifier ensuite dans l'admin qu'aucune
équipe ne porte encore un nom en « (ancien … ) ».
