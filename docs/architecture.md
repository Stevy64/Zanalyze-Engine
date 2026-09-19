# Architecture Zanalyze Engine v4

Deux dépôts :

| Repo | Rôle |
|---|---|
| [Zanalyze-Engine](https://github.com/Stevy64/Zanalyze-Engine) | ingestion, modèles, apprentissage, snapshot |
| [Zanalyze](https://github.com/Stevy64/Zanalyze) | PWA : affichage, VIP, admin |

```text
ESPN ──► espn.py ──► identite.py ──► store.py (SQLite v2)
                                        │
                        qualite.py ─────┤  audit : doublons, chevauchements
                                        │
                                   moteur.py   probabilités + sélection
                                        │
                              predictions (ajout seul)
                                        │
                     ┌──────────────────┼──────────────────┐
                     ▼                  ▼                  ▼
              archive.py          apprentissage.py     snapshot.py
           mémoire longue CSV   recalibration validée   contrat v1 → PWA
```

## Modules

| Fichier | Responsabilité | Toucher quand |
|---|---|---|
| `identite.py` | clé canonique des clubs et des rencontres | un club change de libellé chez ESPN |
| `qualite.py` | plausibilité avant calcul | nouvelle forme d'anomalie |
| `espn.py` | ingestion : calendrier, scores, cotes **et leur ligne** | nouveau championnat, changement d'API |
| `store.py` | SQLite v2 | nouveau champ persisté |
| `moteur.py` | de-vig, Poisson / Dixon–Coles, calibration, sélection | mathématiques, version du moteur |
| `calibrage.py` + `calibration_par_marche.json` | courbes par marché, et par ligue si le volume suit | socle de repli |
| `calibration_store.py` | lecture / écriture de `data/calibration.json` | — |
| `force.py` | force d'attaque et de défense, estimée sur les seuls résultats | matchs sans cotes |
| `apprentissage.py` | mesurer, apprendre, valider, publier si meilleur | seuils, méthode de validation |
| `archive.py` | mémoire longue versionnée | nouveau format d'archive |
| `pipeline.py` | orchestration | nouveaux indicateurs |
| `snapshot.py` | contrat v1 | **coordonner avec** `paris/snapshot.py` de la PWA |
| `evaluation.py` | verdict gagné / perdu | nouveau marché |
| `app.py`, `cli.py` | API et ligne de commande | nouvelles entrées |

## Les quatre décisions structurantes

### 1. L'identité vient du nom, jamais de l'identifiant du fournisseur

En v3.1, `equipes.sofascore_id` était unique et recevait aussi bien des
identifiants ESPN que SofaScore. Les deux espaces se recouvrent : l'id ESPN
de Le Mans vaut l'id SofaScore de l'Inter. L'Inter héritait donc du slug
`le-mans`, et la Ligue des champions affichait « Real Madrid – Le Mans ».

En v4, `identite.cle_equipe()` dérive une clé du nom normalisé (accents,
lettres nordiques, préfixes `FC`/`AS`/`1.`, variantes linguistiques), et les
identifiants de fournisseurs vivent chacun dans leur colonne, à titre
documentaire. Ils ne décident plus de rien.

Corollaire volontaire : **on ne fusionne jamais deux clubs sur une simple
ressemblance de nom.** « Arsenal » et « Arsenal Tula » sont deux clubs, comme
« Sporting CP » et « Sporting Gijón ». Les rapprochements sûrs passent par la
table `ALIAS`, tenue à la main. `apparier()` existe pour *signaler* un doute,
pas pour trancher.

### 2. La ligne de totaux se lit, elle ne se suppose pas

ESPN publie `overUnder` à côté des prix over/under. La v3.1 ignorait ce champ
et rangeait tout sous « OU25 ». Or DraftKings cote Bayern – Bodø/Glimt sur
5,5 buts et PSG – Slovan sur 4,5.

Mesure sur 327 rencontres de l'export du 18 septembre : avec de vraies lignes
2,5, le résidu d'ajustement médian vaut **0,0067** et 3 % dépassent le seuil
de vigilance ; avec les lignes ESPN supposées valoir 2,5, il monte à **0,0154**
et 41 % dépassent le seuil. Le modèle signalait la contradiction, personne ne
l'écoutait.

`moteur.p_over_modele()` traite les trois familles de lignes : demi (2,5),
entière (3,0 — la mise est rendue si le total l'égale, donc le prix ne porte
que sur les issues non remboursées) et quart (2,75 — moitié de mise sur
chaque ligne voisine).

### 3. Une prédiction est figée, jamais réécrite

`predictions` est en ajout seul. `matchs_a_analyser()` exclut tout match dont
le coup d'envoi est passé : analyser un match commencé reviendrait à prédire
le passé avec des cotes qui voient déjà le terrain.

Le bilan porte sur `prediction_officielle()` : la dernière prédiction
**antérieure** au coup d'envoi. Le reste est de la documentation.

Conséquence assumée : au redémarrage, les matchs déjà joués n'ont pas de
prédiction, donc pas de bilan. Le bilan repart de zéro — mais il est honnête.

### 4. La force d'équipe sert là où le marché est absent, pas à côté de lui

`force.py` estime des forces d'attaque et de défense sur les seuls résultats,
sans jamais lire une cote. Mesuré sur 4 247 matchs, le mélange avec le marché
**dégrade** la prévision à tous les poids testés : les cotes de clôture
contiennent déjà cette information. Le poids reste donc à zéro.

Sa place est ailleurs : les rencontres sans cotes, que la v3.1 n'analysait pas
du tout. Ces analyses portent `source_cotes = "forces"` et une confiance
abaissée. `force.poids_optimal()` remesure le mélange à chaque cycle, au cas
où il finirait par apporter quelque chose.

### 5. Les constantes sont mesurées, et une seule fois pour tout le monde

Mesuré sur 7 668 matchs de huit compétitions, coupes d'Europe comprises :
la part des buts inscrits avant la pause va de 0,433 (Serie A) à 0,460
(Bundesliga), pour une marge de ±3,2 points par compétition. **Aucune ne
s'écarte significativement de l'ensemble.** `FACTEUR_MI_TEMPS` vaut donc
0,442 partout, et non 0,45 supposé. Même conclusion pour le ρ de
Dixon–Coles (−0,0505 groupé, −0,06 retenu) et pour les courbes de
calibration par ligue, qui n'apportent rien.

L'environnement européen est pourtant bien différent — 3,22 buts par match en
Ligue des champions contre 2,81 en championnat. Cette différence est déjà
portée par les cotes du match ; la dupliquer dans une constante reviendrait
à la compter deux fois.

À l'inverse, la fiabilité par famille (`moteur.FIABILITE`), qui hiérarchise
les propositions, était fausse sur deux entrées : le handicap, réputé fiable
et qui ne l'est pas (1,80 → 2,94), et « les deux équipes marquent », réputé
le pire et qui est le troisième meilleur (4,07 → 1,42, désormais éligible).
Tout écart aux valeurs mesurées doit venir d'une nouvelle mesure, pas d'une
intuition.

### 6. Ce que le moteur apprend vit dans le dépôt, pas dans le cache

La base SQLite est un cache Actions, évincible à tout moment. L'archive
(`data/archive/*.csv`) et les courbes apprises (`data/calibration.json`) sont
commitées. Voir [apprentissage.md](apprentissage.md).

## Contrat snapshot v1

Clés lues par `paris/snapshot.py` : `version`, `competitions`, `equipes`,
`matchs[]` avec `sofascore_id`, `cotes`, `contexte`, `analyse.options`. Les
renommer casse `importer_snapshot`.

La PWA ignore les clés qu'elle ne connaît pas, donc la v4 en ajoute sans
migration : `ligne_totaux` et `cle_moteur` sur le match, `confiance` et
`explication` sur chaque option, `douteuse` et `exposition_lot` sur l'analyse.

Deux points côté PWA méritent attention :

- `equipes[].sofascore_id` ne reçoit plus que de **vrais** identifiants
  SofaScore, donc `null` tant que seul ESPN alimente le moteur. C'est ce qui
  arrête la cascade de renommages dans `_liberer_equipe_uniques`.
- Les cotes de totaux sortent sous `OU25` quand la ligne vaut 2,5 (nom
  historique, conservé pour les écrans existants) et sous `OU3.5`, `OU5.5`…
  sinon. Un prix n'est plus jamais pris pour un prix sur 2,5.

Les contraintes des modèles Django (longueurs, listes de choix, unicités,
codes reconnus par `paris.evaluation.evaluer`) sont vérifiées par
`tests/test_snapshot.py`. Les casser échoue au test, pas en production.

## Fournisseur

ESPN est le seul fournisseur. Le client SofaScore a été retiré : il renvoyait
403 depuis les IP GitHub, doublait chaque rencontre, et sa colonne
d'identifiants partagée est la cause du bug d'identité. La couche
`identite.py` permettrait de le réintroduire sans risque de collision si le
besoin revient ; ce serait un nouveau module d'ingestion, pas un retour en
arrière.

## Variables d'environnement

Voir `.env.example`. Les plus utiles : `ENGINE_TOKEN`, `ENGINE_DB_PATH`,
`ENGINE_SNAPSHOT_PATH`, `ENGINE_ARCHIVE_DIR`, `ENGINE_CALIBRATION_FILE`.
