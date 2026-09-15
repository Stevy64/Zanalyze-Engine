# Activer GitHub Actions

Le fichier [`.github/workflows/refresh-snapshot.yml`](../.github/workflows/refresh-snapshot.yml) est **déjà dans le dépôt**. Si l’onglet Actions affiche « Choose a workflow » (modèles Docker / Django / Pylint), GitHub **n’exécute pas encore** tes YAML : Actions est désactivé ou jamais autorisé sur ce repo.

## 1. Autoriser les workflows (obligatoire une fois)

1. Ouvre le repo **Zanalyze-Engine** → **Settings** (pas Code).
2. Menu de gauche : **Actions** → **General**.
3. **Actions permissions** : choisis **Allow all actions and reusable workflows**.
4. Plus bas, **Workflow permissions** : **Read and write** (pour que le job puisse `git push` `exports/matchs.json`).
5. **Save**.

## 2. Revenir à l’onglet Actions

Recharge **Actions** (en haut). Tu dois voir deux workflows :

| Workflow | Quand |
|----------|--------|
| **CI** | à chaque push (pytest, sans réseau SofaScore) |
| **Refresh snapshot** | toutes les ~2 h + bouton manuel |

Ne clique **pas** sur « Configure » des modèles Docker / Django : ce n’est pas notre stack.

## 3. Premier run manuel

1. Actions → **Refresh snapshot** (colonne de gauche).
2. **Run workflow** → branche `main` → **Run workflow**.
3. Le job installe Python, appelle SofaScore, écrit `exports/matchs.json`, commit si ça a changé.

Si le job est **gris / skipped** : tu n’as pas encore fait l’étape 1.

Si SofaScore répond **403** : les IP GitHub sont filtrées → [oracle.md](oracle.md).

## 4. Côté PWA Zanalyze (PythonAnywhere)

```bash
python manage.py importer_snapshot --url https://raw.githubusercontent.com/Stevy64/Zanalyze-Engine/main/exports/matchs.json
```
