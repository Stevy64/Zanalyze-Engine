"""
Export snapshot v1 — contrat consommé par `paris/snapshot.py` de la PWA.

Le contrat ne bouge pas
-----------------------
Les clés lues par `importer_snapshot` restent identiques, avec les mêmes
types : `version`, `competitions`, `equipes`, `matchs[]`, et dans chaque match
`sofascore_id`, `cotes`, `contexte`, `analyse.options`. La PWA ignore les clés
qu'elle ne connaît pas : les ajouts de la v4 (ligne de totaux, confiance,
explication) passent donc sans migration.

Deux corrections importantes pour la PWA
----------------------------------------
* `equipes[].sofascore_id` ne contient plus que de **vrais** identifiants
  SofaScore. Le moteur y plaçait aussi des identifiants ESPN ; comme la
  colonne est unique côté Django, un club en écrasait un autre et le
  renommait (« Real Madrid – Le Mans » en Ligue des champions).
* Une rencontre n'apparaît **qu'une fois**. Les mêmes matchs arrivaient en
  double lorsqu'ils étaient vus par deux fournisseurs sous des noms
  d'équipes différents.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.identite import id_export
from engine.paths import SNAPSHOT_PATH, ensure_dirs
from engine.store import connect, fiche_equipe_locale, init_db

SNAPSHOT_VERSION = 1


def _marche_totaux(ligne: float | None) -> str:
    """Nom de marché exporté pour les totaux.

    La ligne 2,5 conserve son nom historique « OU25 » : c'est celui que la
    PWA et ses écrans connaissent. Les autres lignes portent le leur, pour
    qu'un prix ne soit plus jamais pris pour un prix sur 2,5.
    """
    if ligne is None:
        return 'OU'
    if abs(float(ligne) - 2.5) < 1e-9:
        return 'OU25'
    return f'OU{float(ligne):g}'[:20]


def _id_public(m) -> int:
    """Identifiant entier positif unique, attendu par le contrat v1."""
    return int(m['espn_id'] or m['sofascore_id'] or id_export(m['cle']))


def exporter_snapshot(*, jours: int | None = 21) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        sql = 'SELECT * FROM matchs WHERE suspect = 0'
        params: list[Any] = []
        if jours:
            debut = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
            fin = (datetime.now(timezone.utc) + timedelta(days=jours)).isoformat()
            sql += ' AND coup_denvoi >= ? AND coup_denvoi <= ?'
            params.extend([debut, fin])
        sql += ' ORDER BY coup_denvoi'
        matchs = list(conn.execute(sql, params).fetchall())

        # Filet : si deux lignes désignaient la même affiche, une seule sort.
        vues: set[tuple[str, str, str, str]] = set()
        retenus = []
        for m in matchs:
            signature = (
                m['competition_code'], m['coup_denvoi'][:10],
                m['domicile_cle'], m['exterieur_cle'],
            )
            if signature in vues:
                continue
            vues.add(signature)
            retenus.append(m)
        matchs = retenus

        codes = {m['competition_code'] for m in matchs}
        cles_equipes = (
            {m['domicile_cle'] for m in matchs} | {m['exterieur_cle'] for m in matchs}
        )

        competitions = []
        if codes:
            q = ','.join('?' * len(codes))
            for c in conn.execute(
                f'SELECT * FROM competitions WHERE code IN ({q}) ORDER BY ordre, code',
                tuple(codes),
            ):
                competitions.append({
                    'code': c['code'], 'nom': c['nom'], 'pays': c['pays'],
                    'ordre': c['ordre'], 'actif': bool(c['actif']),
                    'sofascore_id': c['sofascore_id'],
                })

        equipes = []
        if cles_equipes:
            q = ','.join('?' * len(cles_equipes))
            for e in conn.execute(
                f'SELECT * FROM equipes WHERE cle IN ({q}) ORDER BY slug',
                tuple(cles_equipes),
            ):
                equipes.append({
                    'nom': e['nom'],
                    'nom_court': e['nom_court'],
                    'slug': e['slug'],
                    # Uniquement un identifiant SofaScore authentique : la
                    # colonne est unique côté PWA, un id ESPN y écraserait
                    # un club sans rapport.
                    'sofascore_id': e['sofascore_id'],
                    'thesportsdb_id': e['thesportsdb_id'],
                    'logo_externe': e['logo_externe'] or '',
                    'fiche_club': fiche_equipe_locale(conn, e['cle']),
                })

        out_matchs = []
        for m in matchs:
            cotes = []
            ligne_totaux = None
            for c in conn.execute(
                """SELECT * FROM cotes WHERE cle = ? AND phase IN ('courante', 'ouverture')
                    ORDER BY marche, selection, phase""",
                (m['cle'],),
            ):
                if c['marche'] == 'TOTAUX':
                    nom_marche = _marche_totaux(c['ligne'])
                    if c['phase'] == 'courante' and c['ligne'] is not None:
                        ligne_totaux = float(c['ligne'])
                else:
                    nom_marche = c['marche']
                if c['phase'] == 'ouverture':
                    nom_marche = f'{nom_marche}_OUV'[:20]
                cotes.append({
                    'bookmaker': (c['bookmaker'] or 'espn')[:40],
                    'marche': nom_marche,
                    'selection': (c['selection'] or '')[:20],
                    'valeur': float(c['valeur']),
                    'nb_sources': 1,
                    'releve_le': c['releve_le'],
                })

            ctx_row = conn.execute(
                'SELECT * FROM contextes WHERE cle = ?', (m['cle'],)
            ).fetchone()
            contexte = None
            if ctx_row:
                contexte = {
                    'forme_dom': ctx_row['forme_dom'], 'forme_ext': ctx_row['forme_ext'],
                    'absents_dom': ctx_row['absents_dom'],
                    'absents_ext': ctx_row['absents_ext'],
                    'tendance_buts': ctx_row['tendance_buts'],
                    'a_savoir': ctx_row['a_savoir'],
                    'confrontations': ctx_row['confrontations'],
                    'fiabilite': ctx_row['fiabilite'],
                }

            analyse = None
            ana_row = conn.execute(
                'SELECT payload FROM analyses WHERE cle = ?', (m['cle'],)
            ).fetchone()
            if ana_row:
                try:
                    payload = json.loads(ana_row['payload'])
                except json.JSONDecodeError:
                    payload = None
                if payload:
                    analyse = _bloc_analyse(payload)

            out_matchs.append({
                'sofascore_id': _id_public(m),
                'competition_code': m['competition_code'],
                'domicile_slug': _slug(conn, m['domicile_cle']),
                'exterieur_slug': _slug(conn, m['exterieur_cle']),
                'coup_denvoi': m['coup_denvoi'],
                'journee': m['journee'] or '',
                'statut': m['statut'],
                'buts_dom': m['buts_dom'],
                'buts_ext': m['buts_ext'],
                'buts_dom_mt': m['buts_dom_mt'],
                'buts_ext_mt': m['buts_ext_mt'],
                'cotes': cotes,
                'contexte': contexte,
                'analyse': analyse,
                # Ajouts v4, ignorés par la PWA actuelle.
                'ligne_totaux': ligne_totaux,
                'cle_moteur': m['cle'],
            })

    return {
        'version': SNAPSHOT_VERSION,
        'exporte_le': datetime.now(timezone.utc).isoformat(),
        'competitions': competitions,
        'equipes': equipes,
        'matchs': out_matchs,
    }


def _slug(conn, cle_equipe: str) -> str:
    row = conn.execute('SELECT slug FROM equipes WHERE cle = ?', (cle_equipe,)).fetchone()
    return row['slug'] if row else cle_equipe


def _bloc_analyse(payload: dict[str, Any]) -> dict[str, Any]:
    """Bloc analyse conforme au contrat : aucune valeur requise ne peut être nulle."""
    return {
        'buts_dom_attendus': float(payload.get('buts_dom_attendus') or 0.0),
        'buts_ext_attendus': float(payload.get('buts_ext_attendus') or 0.0),
        'p1': float(payload.get('p1') or 0.0),
        'pn': float(payload.get('pn') or 0.0),
        'p2': float(payload.get('p2') or 0.0),
        'score_probable': (payload.get('score_probable') or '')[:8],
        'profil': (payload.get('profil') or 'moyen')[:16],
        'marge_marche': float(payload.get('marge_marche') or 0.0),
        'residu': float(payload.get('residu') or 0.0),
        'version_moteur': (payload.get('version_moteur') or '4.0.0')[:12],
        'options': [
            {
                'famille': (o.get('famille') or '')[:32],
                'code': (o.get('code') or '')[:32],
                'libelle': (o.get('libelle') or '')[:120],
                'probabilite': float(o.get('probabilite') or 0.0),
                'cote_juste': float(o.get('cote_juste') or 0.0),
                'niveau': o.get('niveau') or 'detail',
                'origine': o.get('origine') or 'calcul',
                'resultat': o.get('resultat') or 'attente',
                # Ajouts v4.
                'confiance': o.get('confiance'),
                'explication': o.get('explication'),
            }
            for o in payload.get('options') or []
        ],
        # Ajouts v4.
        'ligne_totaux': payload.get('ligne_totaux'),
        'douteuse': bool(payload.get('douteuse')),
        'source_cotes': payload.get('source_cotes'),
        'exposition_lot': payload.get('exposition_lot'),
    }


def ecrire_snapshot(*, jours: int | None = 21, path=None) -> Any:
    data = exporter_snapshot(jours=jours)
    dest = path or SNAPSHOT_PATH
    ensure_dirs()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return dest
