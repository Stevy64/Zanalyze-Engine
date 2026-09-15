# Activer GitHub Actions

L’écran **« Choose a workflow »** (Docker / Django / Pylint…) apparaît quand GitHub
**ne voit aucun fichier** sous `.github/workflows/` sur la branche `main` **déjà poussée**,
ou quand Actions est désactivé.

Ce dépôt contient déjà :

- `.github/workflows/refresh-snapshot.yml` — sync SofaScore + snapshot JSON (~2 h)
- `.github/workflows/ci.yml` — `pytest` à chaque push

## 0. Pousser le code (si Actions est encore vide)

Sur ta machine, dans le dossier du moteur :

```bash
cd zanalyze-engine   # ou Zanalyze-Engine
git push -u origin main
```

Recharge l’onglet **Actions**. Si tu vois encore uniquement les modèles, passe à l’étape 1.

## 1. Autoriser les workflows (une fois)

1. Repo **Zanalyze-Engine** → **Settings** (engrenage, pas Code).
2. Gauche : **Actions** → **General**.
3. **Actions permissions** → **Allow all actions and reusable workflows**.
4. **Workflow permissions** → **Read and write permissions**  
   (nécessaire pour que le job committe `exports/matchs.json`).
5. Coche **Allow GitHub Actions to create and approve pull requests** si proposé.
6. **Save**.

## 2. Onglet Actions

Recharge **Actions** (barre du haut). Colonne de gauche :

| Workflow | Rôle |
|----------|------|
| **CI** | tests Python (sans SofaScore) |
| **Refresh snapshot** | ingest + analyses + push JSON |

**Ne clique pas** sur « Configure » des suggestions Docker / Django / Pylint : ce n’est pas notre stack.

## 3. Premier run manuel

1. Clique **Refresh snapshot**.
2. **Run workflow** → branche `main` → **Run workflow**.
3. Ouvre le run : étapes Install → Sync → Commit snapshot.

**Important :** SofaScore **bloque souvent les IP GitHub Actions**. Dans ce cas le job
échoue avec « Snapshot vide » et **ne pousse pas** un JSON vide.

Contournements (recommandés) :

```bash
# Sur ton PC (egress libre) :
python -m engine refresh --pages 1 --passes 1
git add exports/matchs.json
git commit -m "chore: refresh match snapshot (local)"
git push
```

Ou VM Always Free : [oracle.md](oracle.md).

## 4. Côté PWA Zanalyze (PythonAnywhere)

Scheduled task (~2 h) :

```bash
python manage.py importer_snapshot --url https://raw.githubusercontent.com/Stevy64/Zanalyze-Engine/main/exports/matchs.json
```

`raw.githubusercontent.com` est en général sur la whitelist PA.
