# Passage de la v3.1 à la v4

À faire une fois, dans l'ordre. Compter une vingtaine de minutes.

## 1. Supprimer les fichiers devenus inutiles

Ces fichiers n'ont pas pu être supprimés à distance : à faire dans
l'explorateur ou en ligne de commande.

```powershell
cd C:\Users\Steevi\Documents\GitHub\zanalyze-engine
git rm engine/sofascore.py   # déjà fait si vous partez de cette version
Remove-Item refresh-bilan.log, refresh-espn.log, refresh-sept.log -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force engine\__pycache__, .pytest_cache -ErrorAction SilentlyContinue
```

`engine/sofascore.py` a été retiré du dépôt. Il renvoyait 403 depuis les IP
GitHub, doublait chaque rencontre, et sa colonne d'identifiants partagée est
la cause du bug d'identité. La couche `identite.py` permettrait de le
réintroduire proprement si le besoin revient.

## 2. Workflow GitHub Actions

`.github/workflows/refresh-snapshot.yml` est à jour dans le dépôt (cycle
complet + garde-fous d'identité).

Ce qui change : le job lance le cycle complet (avec archive et
recalibration), commite `data/archive/` et `data/calibration.json` en plus du
snapshot, et **échoue** si le snapshot contient deux fois la même rencontre ou
deux équipes partageant un identifiant.

Vérifier ensuite dans **Settings → Actions → General** que les permissions
sont bien sur **Read and write**.

## 3. Lancer les tests

```powershell
pip install -r requirements-dev.txt
pytest
```

121 tests doivent passer. S'ils passent, le moteur est cohérent.

## 4. Repartir sur une base propre

La base v3.1 contient des identités corrompues. Au premier démarrage, le
moteur détecte l'ancien schéma, met les tables de côté sous
`matchs_v1_<horodatage>` et récupère uniquement les **scores** exploitables —
les cotes et analyses v1 reposent sur une ligne de totaux erronée, elles ne
sont pas reprises.

```powershell
python -m engine refresh --jours 21
python -m engine audit
```

`audit` doit renvoyer `matchs_ecartes: 0`. S'il signale un
`chevauchement_calendrier`, deux clubs ont été confondus : ajouter l'alias
manquant dans `identite.ALIAS` et relancer.

## 5. Vérifier le snapshot avant de le servir à la PWA

```powershell
python -c "import json; d=json.load(open('exports/matchs.json', encoding='utf-8')); print(len(d['matchs']), 'matchs,', len(d['equipes']), 'équipes'); print([e['sofascore_id'] for e in d['equipes'][:5]])"
```

Les `sofascore_id` des équipes doivent tous valoir `None` : c'est ce qui
arrête la cascade de renommages côté PWA. Le nombre d'équipes doit être
cohérent avec le nombre de matchs (jamais plus du double).

## 6. Côté PWA

Rien à déployer : le contrat v1 est inchangé. Le premier import corrige les
identités de lui-même, `_upsert_equipe` retrouvant les lignes par slug puis
par nom.

Après le premier import, vérifier dans l'admin Django qu'aucune équipe ne
porte encore un nom en « (ancien … ) » ni un slug en « -old- ». Ce sont les
séquelles de l'ancien bug ; elles peuvent être renommées ou supprimées sans
risque une fois les matchs rattachés aux bonnes équipes.

## Ce à quoi il faut s'attendre

**Le bilan repart de zéro.** Un match déjà joué n'a pas de prédiction figée
avant son coup d'envoi, donc pas de bilan. C'est voulu : les analyses v3.1 des
matchs passés avaient pu être calculées après coup, elles ne prouvaient rien.
Le nouveau bilan ne contiendra que de vraies prédictions.

**La calibration n'évolue pas tout de suite.** Il faut environ 400
observations par marché, soit à peu près 300 matchs couverts, avant que la
première courbe ne soit publiée. `python -m engine calibrer --essai` indique
où en est chaque marché. En attendant, le moteur utilise les courbes fournies
dans `engine/calibration_par_marche.json`.

**Les buts attendus vont changer, parfois beaucoup.** Sur Bayern – Bodø/Glimt,
coté sur une ligne 5,5, la v3.1 annonçait 2,4 buts attendus ; la v4 en annonce
5,2. Ce n'est pas une dérive, c'est la correction.
