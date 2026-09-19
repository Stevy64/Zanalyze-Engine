"""
Pipeline métier (sans Django).

    ingestion → contrôle qualité → analyse figée → règlement → archive → snapshot

Deux règles non négociables
---------------------------
1. **Un match commencé n'est jamais analysé.** Ses cotes contiennent déjà ce
   qui se passe sur le terrain ; s'en servir reviendrait à prédire le passé.
2. **Une prédiction n'est jamais réécrite.** Chaque calcul est ajouté à
   l'historique avec son horodatage. Le bilan porte sur la dernière
   prédiction antérieure au coup d'envoi, et sur elle seule.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from engine import espn
from engine.evaluation import evaluer
from engine.moteur import (
    AnalyseInvalide, analyser, analyser_sans_marche, classer_journee,
)
from engine.qualite import auditer, est_bloquant, verifier_cotes
from engine.snapshot import ecrire_snapshot
from engine.store import (
    connect,
    cotes_utilisables,
    enregistrer_prediction,
    init_db,
    marquer_suspect,
    matchs_a_analyser,
    reinitialiser_suspects,
    resoudre_equipe,
    save_analyse,
    upsert_competition,
    upsert_cotes,
    upsert_match,
)


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _iso(valeur: Any) -> str:
    if isinstance(valeur, datetime):
        return valeur.astimezone(timezone.utc).isoformat()
    return str(valeur)


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

def _persister(conn, row: dict[str, Any]) -> str | None:
    """Écrit une rencontre normalisée et ses cotes. Renvoie le jour concerné."""
    meta = row['meta']
    upsert_competition(
        conn, code=meta['code'], nom=meta['nom'], pays=meta['pays'],
        ordre=meta['ordre'], sofascore_id=None,
    )
    provider = row.get('provider') or 'espn'
    cle_dom = resoudre_equipe(
        conn, nom=row['home']['nom'], provider=provider,
        provider_id=row['home'].get('id'), logo=row['home'].get('logo') or '',
        nom_court_fourni=row['home'].get('nom_court') or '',
    )
    cle_ext = resoudre_equipe(
        conn, nom=row['away']['nom'], provider=provider,
        provider_id=row['away'].get('id'), logo=row['away'].get('logo') or '',
        nom_court_fourni=row['away'].get('nom_court') or '',
    )
    if cle_dom == cle_ext:
        return None  # identité non résolue : on n'écrit rien plutôt qu'une absurdité

    cle = row['cle']
    coup_iso = _iso(row['coup_denvoi'])
    upsert_match(conn, {
        'cle': cle,
        'competition_code': meta['code'],
        'domicile_cle': cle_dom,
        'exterieur_cle': cle_ext,
        'coup_denvoi': coup_iso,
        'journee': row.get('journee') or '',
        'statut': row['statut'],
        'buts_dom': row.get('buts_dom'),
        'buts_ext': row.get('buts_ext'),
        'buts_dom_mt': row.get('buts_dom_mt'),
        'buts_ext_mt': row.get('buts_ext_mt'),
        'espn_id': row.get('event_id') if provider == 'espn' else None,
        'sofascore_id': row.get('event_id') if provider == 'sofascore' else None,
    })

    cotes = row.get('cotes') or {}
    book = cotes.get('book') or provider
    for champ, phase in (('1x2', 'courante'), ('ouverture_1x2', 'ouverture')):
        trio = cotes.get(champ)
        if not trio:
            continue
        if verifier_cotes(trio):
            continue
        upsert_cotes(
            conn, cle, book, '1X2', list(zip(('1', 'N', '2'), trio)), phase=phase,
        )
    for champ, phase in (('totaux', 'courante'), ('ouverture_totaux', 'ouverture')):
        tot = cotes.get(champ)
        if not tot:
            continue
        over, under, ligne = tot
        if verifier_cotes(None, ligne, (over, under)):
            continue
        upsert_cotes(
            conn, cle, book, 'TOTAUX', [('over', over), ('under', under)],
            ligne=float(ligne), phase=phase,
        )
    return coup_iso[:10]


def sync(*, jours_passes: int = 14, jours_futurs: int = 21) -> dict[str, Any]:
    """Ingestion ESPN complète, suivie d'un audit qualité."""
    init_db()
    stats: dict[str, Any] = {
        'provider': 'espn', 'crees_ou_maj': 0, 'erreurs': 0, 'jours': [],
        'detail_erreurs': [], 'avec_cotes_1x2': 0, 'avec_ligne_totaux': 0,
        'identites_non_resolues': 0,
    }
    try:
        rows = espn.collecter_matchs(
            jours_passes=jours_passes, jours_futurs=jours_futurs, avec_cotes=True,
        )
    except Exception as e:  # noqa: BLE001
        stats['erreurs'] += 1
        stats['detail_erreurs'].append(f'collecte : {e}')
        return stats

    jours: set[str] = set()
    with connect() as conn:
        for row in rows:
            try:
                jour = _persister(conn, row)
            except Exception as e:  # noqa: BLE001
                stats['erreurs'] += 1
                if len(stats['detail_erreurs']) < 8:
                    stats['detail_erreurs'].append(f"{row.get('cle')} : {e}")
                continue
            if jour is None:
                stats['identites_non_resolues'] += 1
                continue
            jours.add(jour)
            stats['crees_ou_maj'] += 1
            cotes = row.get('cotes') or {}
            if cotes.get('1x2'):
                stats['avec_cotes_1x2'] += 1
            if cotes.get('totaux'):
                stats['avec_ligne_totaux'] += 1
    stats['jours'] = sorted(jours)
    stats['qualite'] = controler_qualite()
    return stats


def controler_qualite() -> dict[str, Any]:
    """Audite la base et met de côté les rencontres invraisemblables."""
    init_db()
    resume: dict[str, int] = defaultdict(int)
    ecartes = 0
    with connect() as conn:
        reinitialiser_suspects(conn)
        matchs = [
            {
                'cle': r['cle'], 'competition_code': r['competition_code'],
                'domicile_slug': r['domicile_cle'], 'exterieur_slug': r['exterieur_cle'],
                'coup_denvoi': r['coup_denvoi'], 'statut': r['statut'],
                'buts_dom': r['buts_dom'], 'buts_ext': r['buts_ext'],
                'buts_dom_mt': r['buts_dom_mt'], 'buts_ext_mt': r['buts_ext_mt'],
            }
            for r in conn.execute('SELECT * FROM matchs')
        ]
        anomalies = auditer(matchs)
        for cle, liste in anomalies.items():
            for a in liste:
                resume[a.code] += 1
            if est_bloquant(liste):
                marquer_suspect(conn, cle, ' | '.join(a.texte() for a in liste))
                ecartes += 1
    return {'anomalies': dict(resume), 'matchs_ecartes': ecartes}


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------

def _regler(conn, cle: str) -> bool:
    """Marque gagné / perdu les options d'une analyse dont le score est connu.

    Renvoie True si quelque chose a changé. Idempotent : une option déjà
    réglée n'est jamais revisitée.
    """
    m = conn.execute(
        """SELECT buts_dom, buts_ext, buts_dom_mt, buts_ext_mt
             FROM matchs WHERE cle = ?""", (cle,),
    ).fetchone()
    if not m or m['buts_dom'] is None or m['buts_ext'] is None:
        return False
    row = conn.execute('SELECT payload FROM analyses WHERE cle = ?', (cle,)).fetchone()
    if not row:
        return False
    try:
        payload = json.loads(row['payload'])
    except json.JSONDecodeError:
        return False
    change = False
    for o in payload.get('options') or []:
        if o.get('resultat') not in (None, '', 'attente'):
            continue
        try:
            gagne = evaluer(
                o.get('code'), m['buts_dom'], m['buts_ext'],
                m['buts_dom_mt'], m['buts_ext_mt'],
            )
        except (ValueError, TypeError):
            continue
        if gagne is True:
            o['resultat'] = 'gagne'
            change = True
        elif gagne is False:
            o['resultat'] = 'perdu'
            change = True
    if change:
        save_analyse(conn, cle, payload)
    return change


def regler_termines() -> int:
    """Règle les analyses dont le match est désormais terminé.

    Renvoie le nombre d'analyses effectivement mises à jour, pas le nombre
    de matchs examinés : rejouer la commande ne gonfle donc pas le compte.
    """
    init_db()
    n = 0
    with connect() as conn:
        cles = [
            r['cle'] for r in conn.execute(
                """SELECT a.cle FROM analyses a
                     JOIN matchs m ON m.cle = a.cle
                    WHERE m.statut = 'termine'
                      AND m.buts_dom IS NOT NULL AND m.buts_ext IS NOT NULL"""
            )
        ]
        for cle in cles:
            if _regler(conn, cle):
                n += 1
    return n


def _forces_par_ligue(conn) -> dict:
    """Forces d'équipe ajustées sur l'historique durable, une fois par cycle.

    Elles ne servent qu'aux rencontres sans cotes. Mesuré sur 4 247 matchs,
    le mélange forces + marché ne bat jamais le marché seul : la place des
    forces est là où il n'y a pas de marché, pas à côté de lui.
    """
    from engine.force import ajuster_par_ligue

    lignes = [
        {'ligue': r['competition_code'], 'date': r['coup_denvoi'][:10],
         'dom': r['domicile_cle'], 'ext': r['exterieur_cle'],
         'bd': r['buts_dom'], 'be': r['buts_ext']}
        for r in conn.execute(
            """SELECT competition_code, coup_denvoi, domicile_cle, exterieur_cle,
                      buts_dom, buts_ext
                 FROM resultats
                WHERE buts_dom IS NOT NULL AND buts_ext IS NOT NULL
                ORDER BY coup_denvoi"""
        )
    ]
    if not lignes:
        return {}
    try:
        return ajuster_par_ligue(lignes)
    except Exception:  # noqa: BLE001 — une estimation absente n'arrête rien
        return {}


def analyser_jours(jours: list[str] | None = None) -> dict[str, Any]:
    """Analyse les rencontres à venir, par lot d'une journée.

    Le lot est la bonne maille : c'est à l'échelle d'une journée que se joue
    la diversification. Analyser match par match ramènerait la répétition
    que la v3.1 produisait.
    """
    init_db()
    stats = {'analyses': 0, 'ignores': 0, 'refuses': 0, 'sans_ligne': 0,
             'lots': 0, 'sans_cotes_estimes': 0}
    maintenant = _maintenant()

    with connect() as conn:
        forces = _forces_par_ligue(conn)
        if jours:
            candidats = []
            for jour in jours:
                candidats.extend(matchs_a_analyser(conn, jour))
        else:
            candidats = matchs_a_analyser(conn)

        par_jour: dict[str, list] = defaultdict(list)
        for m in candidats:
            par_jour[m['coup_denvoi'][:10]].append(m)

        for jour, matchs in sorted(par_jour.items()):
            lot: list[dict[str, Any]] = []
            for m in matchs:
                cotes = cotes_utilisables(conn, m['cle'])
                noms = {}
                for role, cle_eq in (('dom', m['domicile_cle']), ('ext', m['exterieur_cle'])):
                    r = conn.execute(
                        'SELECT nom_court FROM equipes WHERE cle = ?', (cle_eq,)
                    ).fetchone()
                    noms[role] = r['nom_court'] if r else cle_eq

                if not cotes.get('1x2'):
                    # Sans cotes, la v3.1 n'affichait rien du tout. Les forces
                    # d'équipe donnent une estimation plus faible que le
                    # marché, mais bien meilleure que le silence.
                    f = forces.get(m['competition_code'])
                    lam = f.lambdas(m['domicile_cle'], m['exterieur_cle']) if f else None
                    if not lam:
                        stats['ignores'] += 1
                        continue
                    payload = analyser_sans_marche(
                        lam[0], lam[1], noms['dom'], noms['ext'],
                        ligue=m['competition_code'],
                    )
                    payload['cle'] = m['cle']
                    payload['source_cotes'] = 'forces'
                    stats['sans_cotes_estimes'] += 1
                    lot.append(payload)
                    continue

                if not cotes.get('totaux'):
                    stats['sans_ligne'] += 1
                try:
                    payload = analyser(
                        cotes['1x2'], cotes.get('totaux'),
                        noms['dom'], noms['ext'],
                        mouvement=cotes.get('mouvement'),
                        ligue=m['competition_code'],
                    )
                except AnalyseInvalide:
                    stats['refuses'] += 1
                    continue
                payload['cle'] = m['cle']
                payload['source_cotes'] = cotes.get('source')
                lot.append(payload)

            if not lot:
                continue
            classer_journee(lot)
            stats['lots'] += 1

            for payload in lot:
                cle = payload['cle']
                coup = conn.execute(
                    'SELECT coup_denvoi FROM matchs WHERE cle = ?', (cle,)
                ).fetchone()
                minutes = None
                if coup:
                    try:
                        depart = datetime.fromisoformat(
                            str(coup['coup_denvoi']).replace('Z', '+00:00')
                        )
                        minutes = int((depart - maintenant).total_seconds() // 60)
                    except ValueError:
                        minutes = None
                enregistrer_prediction(
                    conn, cle, payload,
                    minutes_avant=minutes,
                    avant_match=(minutes is None or minutes > 0),
                )
                save_analyse(conn, cle, payload)
                stats['analyses'] += 1
    return stats


# --------------------------------------------------------------------------
# Cycle complet
# --------------------------------------------------------------------------

def refresh(
    *, jours_snapshot: int = 21, recalibrer: bool = True, archiver: bool = True,
) -> dict[str, Any]:
    """Ingestion, analyse, règlement, archive, recalibration, snapshot."""
    sync_stats = sync(
        jours_passes=min(14, max(3, jours_snapshot)),
        jours_futurs=min(21, max(3, jours_snapshot)),
    )
    ana = analyser_jours()
    regles = regler_termines()

    archive_stats: dict[str, Any] = {}
    calib: dict[str, Any] = {}
    with connect() as conn:
        if archiver:
            from engine import archive
            archive_stats = {
                'observations': archive.exporter(conn),
                'resultats': archive.exporter_resultats(conn),
            }
        if recalibrer:
            from engine.apprentissage import recalibrer as lancer_recalibration
            calib = lancer_recalibration(conn)

    path = ecrire_snapshot(jours=jours_snapshot)
    return {
        'sync': sync_stats,
        'analyses': ana,
        'options_reglees': regles,
        'archive': archive_stats,
        'calibration': calib,
        'snapshot': str(path),
        'provider': 'espn',
    }
