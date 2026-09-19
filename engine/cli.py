"""
Entrée unique : `python -m engine <commande>`

  refresh    ingestion + analyse + règlement + archive + calibration + snapshot
  sync       ingestion seule
  analyser   analyse des matchs à venir, sans réseau
  snapshot   réécrit exports/matchs.json depuis la base
  audit      contrôle qualité : doublons, chevauchements, scores aberrants
  calibrer   apprend une calibration et ne la publie que si elle gagne
  bilan      écart annoncé / observé par marché, avec marge d'erreur
  archive    exporte, importe et inventorie la mémoire longue
  serve      API FastAPI
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _afficher(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='zanalyze-engine')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_sync = sub.add_parser('sync', help='Ingestion calendrier / cotes → SQLite')
    p_sync.add_argument('--jours-passes', type=int, default=14)
    p_sync.add_argument('--jours-futurs', type=int, default=21)

    p_ana = sub.add_parser('analyser', help='Analyse les matchs à venir')
    p_ana.add_argument('--journee', default='', help='AAAA-MM-JJ (défaut : tous)')

    p_snap = sub.add_parser('snapshot', help='Écrit exports/matchs.json (contrat v1)')
    p_snap.add_argument('--jours', type=int, default=21)
    p_snap.add_argument('--out', default='')

    p_ref = sub.add_parser('refresh', help='Cycle complet')
    p_ref.add_argument('--jours', type=int, default=21)
    p_ref.add_argument('--sans-calibration', action='store_true')
    p_ref.add_argument('--sans-archive', action='store_true')

    sub.add_parser('audit', help='Contrôle qualité de la base')

    p_cal = sub.add_parser('calibrer', help='Apprend et publie si meilleur')
    p_cal.add_argument('--essai', action='store_true',
                       help='Mesure sans rien publier')
    p_cal.add_argument('--depuis-archive', action='store_true',
                       help="Apprend sur l'archive complète plutôt que sur la base")

    p_bil = sub.add_parser('bilan', help='Écart annoncé / observé par marché')
    p_bil.add_argument('--depuis-archive', action='store_true')

    p_arc = sub.add_parser('archive', help='Mémoire longue du moteur')
    p_arc.add_argument('action',
                       choices=('exporter', 'stats', 'importer', 'importer-coupes'))
    p_arc.add_argument('--saisons', default='',
                       help="Championnats : années de début (2023,2024). "
                            "Coupes d'Europe : saisons pleines (2023-24,2024-25)")
    p_arc.add_argument('--ligues', default='',
                       help='Codes internes : PL,LIGA,BL,SA,L1,LP')

    p_serve = sub.add_parser('serve', help='API FastAPI')
    p_serve.add_argument('--host', default='0.0.0.0')
    p_serve.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8001')))

    args = parser.parse_args(argv)

    if args.cmd == 'sync':
        from engine.pipeline import sync
        _afficher(sync(jours_passes=args.jours_passes, jours_futurs=args.jours_futurs))
        return 0

    if args.cmd == 'analyser':
        from engine.pipeline import analyser_jours
        _afficher(analyser_jours([args.journee] if args.journee else None))
        return 0

    if args.cmd == 'snapshot':
        from pathlib import Path
        from engine.snapshot import ecrire_snapshot
        dest = ecrire_snapshot(
            jours=args.jours, path=Path(args.out) if args.out else None,
        )
        print(dest)
        return 0

    if args.cmd == 'refresh':
        from engine.pipeline import refresh
        _afficher(refresh(
            jours_snapshot=args.jours,
            recalibrer=not args.sans_calibration,
            archiver=not args.sans_archive,
        ))
        return 0

    if args.cmd == 'audit':
        from engine.pipeline import controler_qualite
        _afficher(controler_qualite())
        return 0

    if args.cmd == 'calibrer':
        from engine.apprentissage import (
            evaluer_candidate, rapport_lisible, recalibrer,
        )
        from engine.store import connect, init_db
        init_db()
        if args.depuis_archive:
            from engine import archive
            rapport = evaluer_candidate(archive.charger_observations())
            if rapport.get('retenue') and not args.essai:
                from engine.calibration_store import sauver_tables
                chemin = sauver_tables(
                    rapport.pop('tables'),
                    echantillons={'total': rapport['observations']},
                )
                rapport['publie'] = True
                rapport['fichier'] = str(chemin)
            else:
                rapport.pop('tables', None)
                rapport['publie'] = False
        else:
            with connect() as conn:
                rapport = recalibrer(conn, publier=not args.essai)
        print(rapport_lisible(rapport))
        print()
        _afficher({k: v for k, v in rapport.items() if k != 'tables'})
        return 0

    if args.cmd == 'bilan':
        from engine.apprentissage import bilan_par_marche, collecter_observations
        from engine.store import connect, init_db
        init_db()
        if args.depuis_archive:
            from engine import archive
            observations = archive.charger_observations()
        else:
            with connect() as conn:
                observations = collecter_observations(conn)
        lignes = bilan_par_marche(observations)
        if not lignes:
            print('Aucune option réglée pour le moment.')
            return 0
        print(f'{"Marché":<14}{"n":>7}{"annoncé":>10}{"observé":>10}'
              f'{"écart":>9}{"marge":>8}  significatif')
        for b in lignes:
            print(
                f"{b['marche']:<14}{b['observations']:>7}{b['annonce']:>9.1f}%"
                f"{b['observe']:>9.1f}%{b['ecart_points']:>+9.1f}"
                f"{b['marge_points']:>8.1f}  {'oui' if b['significatif'] else 'non'}"
            )
        return 0

    if args.cmd == 'archive':
        from engine import archive
        from engine.store import connect, init_db
        init_db()
        if args.action == 'stats':
            _afficher(archive.statistiques())
            return 0
        with connect() as conn:
            if args.action == 'exporter':
                _afficher({
                    'observations': archive.exporter(conn),
                    'resultats': archive.exporter_resultats(conn),
                })
                return 0
            if args.action == 'importer-coupes':
                libelles = [s.strip() for s in args.saisons.split(',') if s.strip()]
                if not libelles:
                    print('Préciser --saisons, par exemple : '
                          '--saisons 2021-22,2022-23,2023-24')
                    return 2
                _afficher(archive.importer_coupes_europe(conn, saisons=libelles))
                return 0
            saisons = [int(s) for s in args.saisons.split(',') if s.strip().isdigit()]
            if not saisons:
                print('Préciser --saisons, par exemple : --saisons 2022,2023,2024')
                return 2
            ligues = [c.strip().upper() for c in args.ligues.split(',') if c.strip()]
            _afficher(archive.importer_football_data(
                conn, saisons=saisons, ligues=ligues or None,
            ))
        return 0

    if args.cmd == 'serve':
        import uvicorn
        uvicorn.run('engine.app:app', host=args.host, port=args.port, reload=False)
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
