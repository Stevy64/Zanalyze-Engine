"""Bout en bout : ingestion → analyse → snapshot, et conformité au contrat PWA.

Le contrat est celui de `paris/snapshot.py` côté Zanalyze. Les contraintes
vérifiées ici viennent directement des modèles Django : longueurs de champs,
listes de choix, unicités. Les casser ferait échouer `importer_snapshot` en
production, pas ici.
"""
from datetime import datetime, timedelta, timezone

import pytest

# Valeurs autorisées par paris.models.Option.NIVEAU et .ORIGINE.
NIVEAUX_PWA = {'prudente', 'recommandee', 'equilibree', 'audacieuse', 'filet', 'detail'}
ORIGINES_PWA = {'marche', 'calcul'}
RESULTATS_PWA = {'attente', 'gagne', 'perdu', 'annule'}
# Codes reconnus par paris.evaluation.evaluer : un code inconnu y lève ValueError.
PREFIXES_CODES = ('OV_', 'UN_', 'MRG_', 'HCP_', 'HT_')
CODES_FIXES = {
    '1X2_1', '1X2_N', '1X2_2', 'DC_1X', 'DC_X2', 'DC_12',
    'BTTS_O', 'BTTS_N', 'DOM_MARQUE', 'EXT_MARQUE', 'DOM_2PLUS', 'EXT_2PLUS',
}


@pytest.fixture()
def moteur(tmp_path, monkeypatch):
    monkeypatch.setenv('ENGINE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ENGINE_DB_PATH', str(tmp_path / 'e.sqlite3'))
    from engine import paths, snapshot, store
    paths.DATA_DIR = tmp_path
    paths.DB_PATH = tmp_path / 'e.sqlite3'
    paths.ARCHIVE_DIR = tmp_path / 'archive'
    paths.SNAPSHOT_PATH = tmp_path / 'matchs.json'
    store.DB_PATH = paths.DB_PATH
    snapshot.SNAPSHOT_PATH = paths.SNAPSHOT_PATH
    store.init_db()
    return store


def _demain(heures=48):
    return (datetime.now(timezone.utc) + timedelta(hours=heures)).replace(microsecond=0)


META = {'code': 'UCL', 'nom': 'Ligue des champions', 'pays': 'Europe', 'ordre': 10}


def _row(nom_dom, nom_ext, quand, *, id_dom, id_ext, event_id,
         cotes=True, ligne=3.5, statut='a_venir'):
    from engine.identite import cle_equipe, cle_match
    bloc = {
        'provider': 'espn', 'event_id': event_id, 'competition_id': str(event_id),
        'league_slug': 'uefa.champions', 'meta': META,
        'cle': cle_match(META['code'], quand, cle_equipe(nom_dom), cle_equipe(nom_ext)),
        'coup_denvoi': quand, 'journee': 'Journée 1', 'statut': statut,
        'home': {'id': id_dom, 'nom': nom_dom, 'nom_court': nom_dom[:24], 'logo': ''},
        'away': {'id': id_ext, 'nom': nom_ext, 'nom_court': nom_ext[:24], 'logo': ''},
        'buts_dom': None, 'buts_ext': None, 'buts_dom_mt': None, 'buts_ext_mt': None,
        'cotes': None,
    }
    if cotes:
        bloc['cotes'] = {
            'book': 'espn',
            '1x2': (1.60, 4.40, 5.00),
            'totaux': (1.90, 1.90, ligne),
            'ouverture_1x2': (1.70, 4.20, 4.60),
            'ouverture_totaux': None,
        }
    return bloc


def _ingerer(rows):
    from engine.pipeline import _persister
    from engine.store import connect
    with connect() as conn:
        for r in rows:
            _persister(conn, r)


def test_bout_en_bout_contrat_v1(moteur):
    from engine.pipeline import analyser_jours, controler_qualite
    from engine.snapshot import SNAPSHOT_VERSION, exporter_snapshot

    quand = _demain()
    _ingerer([
        _row('Real Madrid', 'Internazionale', quand, id_dom=86, id_ext=110,
             event_id=401915451, ligne=2.5),
        _row('Bayern Munich', 'Bodø/Glimt', quand + timedelta(hours=2),
             id_dom=132, id_ext=2697, event_id=401915443, ligne=5.5),
        _row('Barcelona', 'Feyenoord Rotterdam', quand + timedelta(days=1),
             id_dom=83, id_ext=142, event_id=401915424, ligne=3.5),
    ])
    controler_qualite()
    stats = analyser_jours()
    assert stats['analyses'] == 3

    data = exporter_snapshot(jours=30)
    assert data['version'] == SNAPSHOT_VERSION
    assert {'competitions', 'equipes', 'matchs', 'exporte_le'} <= set(data)
    assert len(data['matchs']) == 3
    assert len(data['equipes']) == 6

    ids = [m['sofascore_id'] for m in data['matchs']]
    assert len(ids) == len(set(ids))
    assert all(isinstance(i, int) and 0 < i < 2 ** 31 for i in ids)

    slugs = [e['slug'] for e in data['equipes']]
    assert len(slugs) == len(set(slugs))
    noms = [e['nom'] for e in data['equipes']]
    assert len(noms) == len(set(noms))  # Equipe.nom est unique côté PWA


def test_identifiants_espn_jamais_exportes_comme_sofascore(moteur):
    """La cause de « Real Madrid – Le Mans », vue depuis le snapshot.

    `Equipe.sofascore_id` est unique côté Django. Y placer un identifiant
    ESPN faisait qu'un club en écrasait un autre et le renommait.
    """
    from engine.snapshot import exporter_snapshot
    quand = _demain()
    _ingerer([
        _row('Le Mans', 'Lorient', quand, id_dom=2697, id_ext=273, event_id=401876455),
        _row('Internazionale', 'Napoli', quand + timedelta(hours=3),
             id_dom=110, id_ext=114, event_id=401915999),
    ])
    data = exporter_snapshot(jours=30)
    par_slug = {e['slug']: e for e in data['equipes']}
    assert 'le-mans' in par_slug
    assert all(e['sofascore_id'] is None for e in data['equipes'])
    inter = next(e for e in data['equipes'] if e['slug'] == 'inter')
    assert 'mans' not in inter['nom'].lower()


def test_meme_rencontre_deux_libelles_un_seul_match(moteur):
    """Le doublon venait de deux orthographes, pas de deux rencontres."""
    from engine.snapshot import exporter_snapshot
    quand = _demain()
    _ingerer([
        _row('Barcelona', 'Feyenoord', quand, id_dom=83, id_ext=2959, event_id=1),
        _row('Barcelona', 'Feyenoord Rotterdam', quand + timedelta(minutes=5),
             id_dom=83, id_ext=142, event_id=2),
    ])
    data = exporter_snapshot(jours=30)
    assert len(data['matchs']) == 1
    assert len(data['equipes']) == 2


def test_match_suspect_exclu_du_snapshot(moteur):
    """Une équipe engagée deux fois le même soir n'atteint pas l'utilisateur."""
    from engine.pipeline import controler_qualite
    from engine.snapshot import exporter_snapshot
    quand = _demain()
    _ingerer([
        _row('Real Madrid', 'Internazionale', quand, id_dom=86, id_ext=110, event_id=1),
        _row('Real Madrid', 'Arsenal', quand + timedelta(hours=2),
             id_dom=86, id_ext=359, event_id=2),
    ])
    rapport = controler_qualite()
    assert rapport['matchs_ecartes'] >= 1
    data = exporter_snapshot(jours=30)
    assert len(data['matchs']) == 1


def test_options_conformes_aux_modeles_pwa(moteur):
    from engine.pipeline import analyser_jours
    from engine.snapshot import exporter_snapshot
    quand = _demain()
    _ingerer([
        _row('Real Madrid', 'Internazionale', quand, id_dom=86, id_ext=110,
             event_id=1, ligne=2.5),
        _row('Barcelona', 'Arsenal', quand + timedelta(hours=3),
             id_dom=83, id_ext=359, event_id=2, ligne=3.0),
    ])
    analyser_jours()
    data = exporter_snapshot(jours=30)

    for m in data['matchs']:
        a = m['analyse']
        assert a is not None
        # La PWA lit ces clés sans valeur par défaut : aucune ne peut manquer.
        for champ in ('buts_dom_attendus', 'buts_ext_attendus', 'p1', 'pn', 'p2'):
            assert isinstance(a[champ], float)
        assert len(a['score_probable']) <= 8
        assert len(a['profil']) <= 16
        assert len(a['version_moteur']) <= 12

        niveaux = [o['niveau'] for o in a['options']]
        assert set(niveaux) <= NIVEAUX_PWA
        assert niveaux.count('recommandee') == 1
        for o in a['options']:
            assert len(o['famille']) <= 32
            assert len(o['code']) <= 32
            assert len(o['libelle']) <= 120
            assert o['origine'] in ORIGINES_PWA
            assert o['resultat'] in RESULTATS_PWA
            assert isinstance(o['probabilite'], float)
            assert isinstance(o['cote_juste'], float)
            # Un code inconnu ferait lever paris.evaluation.evaluer.
            assert o['code'] in CODES_FIXES or o['code'].startswith(PREFIXES_CODES)

    for m in data['matchs']:
        for c in m['cotes']:
            assert len(c['marche']) <= 20
            assert len(c['selection']) <= 20
            assert len(c['bookmaker']) <= 40
            assert 0 < c['valeur'] < 10000


def test_ligne_de_totaux_exportee_sans_ambiguite(moteur):
    """Une cote sur 5,5 ne doit plus jamais être rangée sous « OU25 »."""
    from engine.snapshot import exporter_snapshot
    quand = _demain()
    _ingerer([
        _row('Bayern Munich', 'Bodø/Glimt', quand, id_dom=132, id_ext=2697,
             event_id=1, ligne=5.5),
        _row('Arsenal', 'Chelsea', quand + timedelta(hours=3), id_dom=359,
             id_ext=363, event_id=2, ligne=2.5),
    ])
    data = exporter_snapshot(jours=30)
    par_id = {m['ligne_totaux']: m for m in data['matchs']}
    assert set(par_id) == {5.5, 2.5}

    marches_55 = {c['marche'] for c in par_id[5.5]['cotes']}
    assert 'OU5.5' in marches_55 and 'OU25' not in marches_55
    marches_25 = {c['marche'] for c in par_id[2.5]['cotes']}
    assert 'OU25' in marches_25  # nom historique conservé pour la ligne 2,5


def test_analyse_conservee_apres_le_match(moteur):
    """La prédiction reste celle d'avant le coup d'envoi, jamais recalculée."""
    from engine.pipeline import analyser_jours, regler_termines
    from engine.snapshot import exporter_snapshot
    from engine.store import connect, upsert_match

    quand = _demain(heures=2)
    _ingerer([_row('Real Madrid', 'Internazionale', quand, id_dom=86, id_ext=110,
                   event_id=1, ligne=2.5)])
    analyser_jours()
    data = exporter_snapshot(jours=30)
    avant = data['matchs'][0]['analyse']['options']

    with connect() as conn:
        cle = conn.execute('SELECT cle FROM matchs').fetchone()['cle']
        upsert_match(conn, {
            'cle': cle, 'competition_code': 'UCL', 'domicile_cle': 'real-madrid',
            'exterieur_cle': 'inter', 'coup_denvoi': quand.isoformat(),
            'statut': 'termine', 'buts_dom': 2, 'buts_ext': 1,
            'buts_dom_mt': 1, 'buts_ext_mt': 0,
        })
    # Le match a commencé : plus aucune analyse nouvelle.
    assert analyser_jours()['analyses'] == 0
    assert regler_termines() == 1

    apres = exporter_snapshot(jours=30)['matchs'][0]['analyse']['options']
    assert [o['code'] for o in apres] == [o['code'] for o in avant]
    assert [o['probabilite'] for o in apres] == [o['probabilite'] for o in avant]
    assert any(o['resultat'] in ('gagne', 'perdu') for o in apres)
