# Maintenance

## Commandes du quotidien

```bash
python -m engine refresh --pages 1 --passes 1   # sync + analyses + JSON
python -m engine serve                            # API :8001 /docs
pytest                                            # sans réseau
```

Logs Actions : onglet **Actions** → dernier **Refresh snapshot**.

## Ajouter une compétition

1. `engine/sofascore.py` → dict `TOURNOIS` (id SofaScore `uniqueTournament`).
2. Relancer `refresh`. Le code (`PL`, `LIGA`, …) doit rester aligné avec la PWA.

## Changer le moteur (v3.1)

Modifier `moteur.py` / `calibrage.py`, bump `VERSION_MOTEUR`, lancer `pytest`.  
Ne pas importer Django.

## Le snapshot PWA est vide / périmé

1. Ouvre le dernier **Refresh snapshot** : si `crees_ou_maj=0` / `erreurs=10`,
   SofaScore a refusé les IP Actions.
2. **Ne compte pas sur Actions** pour l’ingest : lance en local :

```bash
python -m engine refresh --pages 1 --passes 1
git add exports/matchs.json && git commit -m "chore: refresh match snapshot" && git push
```

3. Sur PA : `importer_snapshot --url …` (raw GitHub).
4. Filtre date de l’UI = matchs dans la fenêtre `--jours 21`.

## SofaScore 403 / snapshot vide sur Actions

Confirmé : le runner GitHub reçoit souvent des erreurs sur *tous* les tournois
(∼10 erreurs = 5 ligues × next/last). Le workflow échoue volontairement pour
ne pas écraser un bon JSON.

- En local / Oracle : egress libre → OK.
- Sur Actions : bascule [oracle.md](oracle.md) ou refresh manuel depuis ton PC.

## Ne pas faire

- Committer `data/engine.sqlite3` ou `.env`.
- Appeler SofaScore depuis PythonAnywhere.
- Changer les noms de clés du snapshot sans migrer la PWA.
