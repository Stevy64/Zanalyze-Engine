"""
Export snapshot v1 — **même schéma** que paris.snapshot côté PWA Zanalyze.

Ne pas renommer les clés (sofascore_id, competition_code, options, …)
sans déployer la PWA en même temps.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.paths import SNAPSHOT_PATH, ensure_dirs
from engine.store import connect, fiche_equipe_locale, init_db

SNAPSHOT_VERSION = 1


def exporter_snapshot(*, jours: int | None = 21) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        sql = 'SELECT * FROM matchs WHERE 1=1'
        params: list[Any] = []
        if jours:
            debut = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
            fin = (datetime.now(timezone.utc) + timedelta(days=jours)).isoformat()
            sql += ' AND coup_denvoi >= ? AND coup_denvoi <= ?'
            params.extend([debut, fin])
        sql += ' ORDER BY coup_denvoi'
        matchs = list(conn.execute(sql, params).fetchall())
        codes = {m['competition_code'] for m in matchs}
        slugs = {m['domicile_slug'] for m in matchs} | {m['exterieur_slug'] for m in matchs}

        competitions = []
        if codes:
            q = ','.join('?' * len(codes))
            for c in conn.execute(
                f'SELECT * FROM competitions WHERE code IN ({q}) ORDER BY ordre, code',
                tuple(codes),
            ):
                competitions.append({
                    'code': c['code'],
                    'nom': c['nom'],
                    'pays': c['pays'],
                    'ordre': c['ordre'],
                    'actif': bool(c['actif']),
                    'sofascore_id': c['sofascore_id'],
                })

        equipes = []
        if slugs:
            q = ','.join('?' * len(slugs))
            for e in conn.execute(
                f'SELECT * FROM equipes WHERE slug IN ({q}) ORDER BY slug',
                tuple(slugs),
            ):
                fiche = {}
                try:
                    fiche = json.loads(e['fiche_club'] or '{}')
                except json.JSONDecodeError:
                    fiche = {}
                if not fiche.get('recents') and not fiche.get('forme'):
                    fiche = fiche_equipe_locale(conn, e['slug'])
                logo = e['logo_externe'] or ''
                if not logo and e['sofascore_id']:
                    logo = (
                        f"https://img.sofascore.com/api/v1/team/"
                        f"{int(e['sofascore_id'])}/image"
                    )
                equipes.append({
                    'nom': e['nom'],
                    'nom_court': e['nom_court'],
                    'slug': e['slug'],
                    'sofascore_id': e['sofascore_id'],
                    'thesportsdb_id': e['thesportsdb_id'],
                    'logo_externe': logo,
                    'fiche_club': fiche,
                })

        out_matchs = []
        for m in matchs:
            cotes = [
                {
                    'bookmaker': c['bookmaker'],
                    'marche': c['marche'],
                    'selection': c['selection'],
                    'valeur': c['valeur'],
                    'nb_sources': c['nb_sources'],
                    'releve_le': c['releve_le'],
                }
                for c in conn.execute(
                    'SELECT * FROM cotes WHERE sofascore_id=? ORDER BY marche, selection',
                    (m['sofascore_id'],),
                )
            ]
            ctx_row = conn.execute(
                'SELECT * FROM contextes WHERE sofascore_id=?',
                (m['sofascore_id'],),
            ).fetchone()
            contexte = None
            if ctx_row:
                contexte = {
                    'forme_dom': ctx_row['forme_dom'],
                    'forme_ext': ctx_row['forme_ext'],
                    'absents_dom': ctx_row['absents_dom'],
                    'absents_ext': ctx_row['absents_ext'],
                    'tendance_buts': ctx_row['tendance_buts'],
                    'a_savoir': ctx_row['a_savoir'],
                    'confrontations': ctx_row['confrontations'],
                    'fiabilite': ctx_row['fiabilite'],
                }
            analyse = None
            ana_row = conn.execute(
                'SELECT payload FROM analyses WHERE sofascore_id=?',
                (m['sofascore_id'],),
            ).fetchone()
            if ana_row:
                try:
                    payload = json.loads(ana_row['payload'])
                except json.JSONDecodeError:
                    payload = None
                if payload:
                    analyse = {
                        'buts_dom_attendus': payload.get('buts_dom_attendus'),
                        'buts_ext_attendus': payload.get('buts_ext_attendus'),
                        'p1': payload.get('p1'),
                        'pn': payload.get('pn'),
                        'p2': payload.get('p2'),
                        'score_probable': payload.get('score_probable'),
                        'profil': payload.get('profil'),
                        'marge_marche': payload.get('marge_marche'),
                        'residu': payload.get('residu'),
                        'version_moteur': payload.get('version_moteur'),
                        'options': [
                            {
                                'famille': o.get('famille'),
                                'code': o.get('code'),
                                'libelle': o.get('libelle'),
                                'probabilite': o.get('probabilite'),
                                'cote_juste': o.get('cote_juste'),
                                'niveau': o.get('niveau'),
                                'origine': o.get('origine'),
                                'resultat': o.get('resultat') or 'attente',
                            }
                            for o in payload.get('options') or []
                        ],
                    }
            out_matchs.append({
                'sofascore_id': m['sofascore_id'],
                'competition_code': m['competition_code'],
                'domicile_slug': m['domicile_slug'],
                'exterieur_slug': m['exterieur_slug'],
                'coup_denvoi': m['coup_denvoi'],
                'journee': m['journee'],
                'statut': m['statut'],
                'buts_dom': m['buts_dom'],
                'buts_ext': m['buts_ext'],
                'buts_dom_mt': m['buts_dom_mt'],
                'buts_ext_mt': m['buts_ext_mt'],
                'cotes': cotes,
                'contexte': contexte,
                'analyse': analyse,
            })

    return {
        'version': SNAPSHOT_VERSION,
        'exporte_le': datetime.now(timezone.utc).isoformat(),
        'competitions': competitions,
        'equipes': equipes,
        'matchs': out_matchs,
    }


def ecrire_snapshot(*, jours: int | None = 21, path=None) -> Any:
    data = exporter_snapshot(jours=jours)
    dest = path or SNAPSHOT_PATH
    ensure_dirs()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return dest
