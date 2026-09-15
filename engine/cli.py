"""CLI : python -m engine refresh | serve | snapshot | sync"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='zanalyze-engine')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_sync = sub.add_parser('sync', help='Ingest SofaScore → SQLite')
    p_sync.add_argument('--pages', type=int, default=1)
    p_sync.add_argument('--passes', type=int, default=1)
    p_sync.add_argument('--contexte', action='store_true')

    p_ana = sub.add_parser('analyser', help='Calcule les analyses des matchs à venir')
    p_ana.add_argument('--journee', default='', help='AAAA-MM-JJ (défaut : tous les jours ouverts)')

    p_snap = sub.add_parser('snapshot', help='Écrit exports/matchs.json (contrat ZanalyZe v1)')
    p_snap.add_argument('--jours', type=int, default=21)
    p_snap.add_argument('--out', default='')

    p_ref = sub.add_parser('refresh', help='sync + analyser + snapshot')
    p_ref.add_argument('--pages', type=int, default=int(os.environ.get('ENGINE_SYNC_PAGES', '1')))
    p_ref.add_argument('--passes', type=int, default=int(os.environ.get('ENGINE_SYNC_PASSES', '1')))
    p_ref.add_argument('--contexte', action='store_true')
    p_ref.add_argument('--jours', type=int, default=21)

    p_serve = sub.add_parser('serve', help='API FastAPI')
    p_serve.add_argument('--host', default='0.0.0.0')
    p_serve.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8001')))

    args = parser.parse_args(argv)

    if args.cmd == 'sync':
        from engine.pipeline import sync
        print(json.dumps(sync(pages=args.pages, passes=args.passes, avec_contexte=args.contexte), indent=2))
        return 0
    if args.cmd == 'analyser':
        from engine.pipeline import analyser_jours
        jours = [args.journee] if args.journee else None
        print(json.dumps(analyser_jours(jours), indent=2))
        return 0
    if args.cmd == 'snapshot':
        from pathlib import Path
        from engine.snapshot import ecrire_snapshot
        path = Path(args.out) if args.out else None
        dest = ecrire_snapshot(jours=args.jours, path=path)
        print(dest)
        return 0
    if args.cmd == 'refresh':
        from engine.pipeline import refresh
        print(json.dumps(
            refresh(
                pages=args.pages,
                passes=args.passes,
                avec_contexte=args.contexte,
                jours_snapshot=args.jours,
            ),
            indent=2,
        ))
        return 0
    if args.cmd == 'serve':
        import uvicorn
        uvicorn.run('engine.app:app', host=args.host, port=args.port, reload=False)
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
