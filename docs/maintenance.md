# Maintenance

## Commandes du quotidien

```bash
python -m engine refresh --jours 21   # cycle complet
python -m engine audit                # contrôle qualité seul
python -m engine bilan                # où en est le moteur ?
python -m engine serve                # API :8001 /docs
pytest                                # sans réseau
```

Logs Actions : onglet **Actions** → dernier **Refresh snapshot**. La ligne de
résumé donne l'essentiel :

```text
ingérés=148 cotes_1x2=52 ligne_totaux=52 analysés=48 refusés=1 réglés=31
snapshot=148 écartés=0 UCL=18 PL=10
calibration : aucun gain hors échantillon (0.20411 contre 0.20355) :
la version en place est conservée
```

## Ajouter une compétition

1. `engine/espn.py` → dictionnaire `TOURNOIS` : slug ESPN + `espn_league_id`.
2. PWA : drapeaux dans `static/js/app.js` (`FLAG_BY_CODE` / `FLAG_BY_PAYS`).
3. Relancer `refresh`. Les `code` doivent rester alignés entre moteur et PWA.

## Changer le moteur

Modifier `moteur.py` / `calibrage.py`, incrémenter `VERSION_MOTEUR`, lancer
`pytest`. Ne pas importer Django. `VERSION_MOTEUR` tient dans 12 caractères :
c'est la longueur du champ côté PWA.

## Incidents courants

### Un club porte le nom d'un autre

C'était le symptôme du bug d'identité v3.1 (« Real Madrid – Le Mans »). Si cela
réapparaît :

```bash
python -m engine audit
```

Un `chevauchement_calendrier` sur une équipe signale deux clubs confondus :
la même équipe ne peut pas jouer deux fois en cinq heures. Ajouter alors
l'alias manquant dans `identite.ALIAS`, puis relancer un `refresh`.

### Le snapshot est vide ou périmé

1. Vérifier le dernier **Refresh snapshot** : `ingérés` > 0 ?
2. Côté PythonAnywhere : `importer_snapshot --url …`
3. Le filtre de date de l'interface suit la fenêtre `--jours`.

### Beaucoup de matchs « refusés »

`refusés` compte les rencontres dont le 1X2 et les totaux se contredisent au
point d'être insolubles (résidu au-delà de 0,08). Quelques-unes par journée est
normal ; une majorité signale un changement de format côté ESPN. Vérifier
alors `avec_ligne_totaux` : s'il est tombé à zéro, le champ `overUnder` a
changé de place.

### Des matchs à venir sans ligne de totaux

Le workflow le signale en avertissement. Le moteur continue : il ajuste sur le
seul 1X2, ce qui est moins précis mais correct. Mieux vaut cela qu'un prix
rattaché à la mauvaise ligne.

## Ne pas faire

- Committer `data/engine.sqlite3` ou `.env`.
- **Ajouter `data/calibration.json` ou `data/archive/` au `.gitignore`** :
  c'est la mémoire du moteur, elle doit survivre au cache Actions.
- Changer les clés du snapshot sans migrer la PWA en même temps.
- Régler un seuil à la main sur un écart non significatif. La colonne
  `significatif` du bilan existe pour cela.
- Analyser un match commencé. Le code l'interdit ; ne pas le contourner.
