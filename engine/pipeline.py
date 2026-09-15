"""Sync SofaScore → SQLite → analyses v3.1 → snapshot."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from engine import sofascore as sofa
from engine.evaluation import evaluer
from engine.moteur import AnalyseInvalide, analyser, classer_journee
from engine.snapshot import ecrire_snapshot
from engine.store import (
    cotes_prioritaires,
    init_db,
    connect,
    matchs_a_analyser,
    save_analyse,
    upsert_competition,
    upsert_contexte,
    upsert_cotes,
    upsert_equipe,
    upsert_match,
)


def _fetch_remote(ev: dict, *, avec_contexte: bool) -> dict[str, Any]:
    eid = ev.get('id')
    out: dict[str, Any] = {'odds_1x2': None, 'odds_ou25': None, 'contexte': None}
    if not eid:
        return out
    try:
        out['odds_1x2'] = sofa.cotes_1x2(int(eid))
    except sofa.SofaScoreErreur:
        pass
    try:
        out['odds_ou25'] = sofa.cotes_ou25(int(eid))
    except sofa.SofaScoreErreur:
        pass
    if avec_contexte:
        home = ev.get('homeTeam') or {}
        away = ev.get('awayTeam') or {}
        try:
            out['contexte'] = sofa.collecter_contexte_match(
                int(eid),
                home_team_id=home.get('id'),
                away_team_id=away.get('id'),
                nom_dom=home.get('shortName') or home.get('name') or 'Dom',
                nom_ext=away.get('shortName') or away.get('name') or 'Ext',
                event=ev,
                tournament_id=(ev.get('tournament') or {}).get('uniqueId')
                or (ev.get('uniqueTournament') or {}).get('id'),
            )
        except Exception:  # noqa: BLE001
            pass
    return out


def _persist_event(conn, tid: int, meta: dict, ev: dict, remote: dict) -> str | None:
    eid = ev.get('id')
    ts = ev.get('startTimestamp')
    if not eid or not ts:
        return None
    home = ev.get('homeTeam') or {}
    away = ev.get('awayTeam') or {}
    nom_dom = (home.get('name') or 'Équipe')[:80]
    nom_ext = (away.get('name') or 'Équipe')[:80]
    court_dom = (home.get('shortName') or sofa.nom_court(nom_dom))[:24]
    court_ext = (away.get('shortName') or sofa.nom_court(nom_ext))[:24]
    hid, aid = home.get('id'), away.get('id')
    slug_dom = sofa.slugify_nom(home.get('slug') or nom_dom)
    slug_ext = sofa.slugify_nom(away.get('slug') or nom_ext)
    logo_dom = f'https://img.sofascore.com/api/v1/team/{int(hid)}/image' if hid else ''
    logo_ext = f'https://img.sofascore.com/api/v1/team/{int(aid)}/image' if aid else ''

    upsert_competition(
        conn, code=meta['code'], nom=meta['nom'], pays=meta['pays'],
        ordre=meta['ordre'], sofascore_id=tid,
    )
    dslug = upsert_equipe(
        conn, slug=slug_dom, nom=nom_dom, nom_court=court_dom,
        sofascore_id=int(hid) if hid else None, logo_externe=logo_dom,
    )
    eslug = upsert_equipe(
        conn, slug=slug_ext, nom=nom_ext, nom_court=court_ext,
        sofascore_id=int(aid) if aid else None, logo_externe=logo_ext,
    )
    status_code = (ev.get('status') or {}).get('code')
    statut = sofa.statut_depuis_code(status_code)
    bd = be = bdm = bem = None
    if statut == 'termine':
        bd, be, bdm, bem = sofa.scores_depuis_event(ev)

    coup = sofa.ts_to_aware(ts).isoformat()
    upsert_match(conn, {
        'sofascore_id': int(eid),
        'competition_code': meta['code'],
        'domicile_slug': dslug,
        'exterieur_slug': eslug,
        'coup_denvoi': coup,
        'journee': str((ev.get('roundInfo') or {}).get('round') or ''),
        'statut': statut,
        'buts_dom': bd,
        'buts_ext': be,
        'buts_dom_mt': bdm,
        'buts_ext_mt': bem,
    })
    odds = remote.get('odds_1x2')
    if odds:
        upsert_cotes(conn, int(eid), 'sofascore', '1X2', list(zip(('1', 'N', '2'), odds)))
    ou = remote.get('odds_ou25')
    if ou:
        upsert_cotes(conn, int(eid), 'sofascore', 'OU25', list(zip(('over', 'under'), ou)))
    ctx = remote.get('contexte') or {}
    if ctx:
        upsert_contexte(conn, int(eid), ctx)

    if statut == 'termine' and bd is not None and be is not None:
        _regler_analyse(conn, int(eid), bd, be, bdm, bem)

    return coup[:10]


def _regler_analyse(conn, sid: int, bd: int, be: int, bdm, bem) -> None:
    row = conn.execute('SELECT payload FROM analyses WHERE sofascore_id=?', (sid,)).fetchone()
    if not row:
        return
    try:
        payload = json.loads(row['payload'])
    except json.JSONDecodeError:
        return
    changed = False
    for o in payload.get('options') or []:
        if o.get('resultat') not in (None, '', 'attente'):
            continue
        try:
            won = evaluer(o.get('code'), bd, be, bdm, bem)
        except (ValueError, TypeError):
            continue
        if won is True:
            o['resultat'] = 'gagne'
            changed = True
        elif won is False:
            o['resultat'] = 'perdu'
            changed = True
    if changed:
        save_analyse(conn, sid, payload)


def sync(*, pages: int = 1, passes: int = 1, avec_contexte: bool = False) -> dict[str, Any]:
    init_db()
    stats = {'crees_ou_maj': 0, 'erreurs': 0, 'jours': []}
    jours: set[str] = set()
    with connect() as conn:
        for tid, meta in sofa.TOURNOIS.items():
            events: list[dict] = []
            try:
                events.extend(sofa.evenements_suivants(tid, pages=pages))
            except sofa.SofaScoreErreur:
                stats['erreurs'] += 1
            try:
                events.extend(sofa.evenements_passes(tid, pages=passes))
            except sofa.SofaScoreErreur:
                stats['erreurs'] += 1
            by_id: dict[int, dict] = {}
            for ev in events:
                eid = ev.get('id')
                if eid:
                    by_id[int(eid)] = ev
            for ev in by_id.values():
                try:
                    remote = _fetch_remote(ev, avec_contexte=avec_contexte)
                    jour = _persist_event(conn, tid, meta, ev, remote)
                    if jour:
                        jours.add(jour)
                        stats['crees_ou_maj'] += 1
                except Exception:  # noqa: BLE001
                    stats['erreurs'] += 1
    stats['jours'] = sorted(jours)
    return stats


def analyser_jours(jours: list[str] | None = None) -> dict[str, int]:
    init_db()
    n_ok = n_skip = 0
    with connect() as conn:
        if jours:
            lots_jours = jours
        else:
            today = datetime.now(timezone.utc).date().isoformat()
            lots_jours = [today]
            extra = conn.execute(
                """SELECT DISTINCT substr(coup_denvoi,1,10) AS j FROM matchs
                   WHERE statut NOT IN ('termine','reporte')
                   ORDER BY j"""
            ).fetchall()
            lots_jours = [r['j'] for r in extra] or lots_jours

        grouped: dict[str, list] = defaultdict(list)
        for jour in lots_jours:
            for m in matchs_a_analyser(conn, jour):
                grouped[jour].append(m)

        for jour, matchs in grouped.items():
            items = []
            refs = {}
            for m in matchs:
                by = cotes_prioritaires(conn, m['sofascore_id'])
                try:
                    c1, cn, c2 = by[('1X2', '1')], by[('1X2', 'N')], by[('1X2', '2')]
                except KeyError:
                    n_skip += 1
                    continue
                ou = None
                if ('OU25', 'over') in by and ('OU25', 'under') in by:
                    ou = (by[('OU25', 'over')], by[('OU25', 'under')])
                nom_dom = conn.execute(
                    'SELECT nom_court FROM equipes WHERE slug=?', (m['domicile_slug'],)
                ).fetchone()
                nom_ext = conn.execute(
                    'SELECT nom_court FROM equipes WHERE slug=?', (m['exterieur_slug'],)
                ).fetchone()
                try:
                    payload = analyser(
                        (c1, cn, c2), ou,
                        (nom_dom['nom_court'] if nom_dom else 'Dom'),
                        (nom_ext['nom_court'] if nom_ext else 'Ext'),
                    )
                except AnalyseInvalide:
                    n_skip += 1
                    continue
                payload['ref'] = str(m['sofascore_id'])
                items.append(payload)
                refs[str(m['sofascore_id'])] = m['sofascore_id']
            if not items:
                continue
            classer_journee(items)
            for payload in items:
                sid = refs.get(str(payload.get('ref')))
                if sid:
                    save_analyse(conn, sid, payload)
                    n_ok += 1
    return {'analyses': n_ok, 'ignores': n_skip}


def refresh(
    *,
    pages: int = 1,
    passes: int = 1,
    avec_contexte: bool = False,
    jours_snapshot: int = 21,
) -> dict[str, Any]:
    sync_stats = sync(pages=pages, passes=passes, avec_contexte=avec_contexte)
    ana = analyser_jours(sync_stats.get('jours') or None)
    path = ecrire_snapshot(jours=jours_snapshot)
    return {'sync': sync_stats, 'analyses': ana, 'snapshot': str(path)}
