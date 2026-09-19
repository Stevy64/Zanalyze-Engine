"""Persistance : régression du bug d'identité, historique des cotes, prédictions."""
import pytest


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """Base neuve et isolée pour chaque test."""
    monkeypatch.setenv('ENGINE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ENGINE_DB_PATH', str(tmp_path / 'e.sqlite3'))
    monkeypatch.setenv('ENGINE_ARCHIVE_DIR', str(tmp_path / 'archive'))
    from engine import paths, store
    paths.DATA_DIR = tmp_path
    paths.DB_PATH = tmp_path / 'e.sqlite3'
    paths.ARCHIVE_DIR = tmp_path / 'archive'
    store.DB_PATH = paths.DB_PATH
    store.init_db()
    return store


def test_ids_de_fournisseurs_ne_se_telescopent_plus(base):
    """Régression du bug « Real Madrid – Le Mans ».

    L'identifiant ESPN de Le Mans (2697) vaut l'identifiant SofaScore de
    l'Inter. En v1, une colonne unique partagée faisait hériter l'Inter du
    slug `le-mans`. Chaque club doit désormais garder le sien.
    """
    with base.connect() as conn:
        le_mans = base.resoudre_equipe(
            conn, nom='Le Mans', provider='espn', provider_id=2697,
        )
        inter = base.resoudre_equipe(
            conn, nom='Internazionale', provider='sofascore', provider_id=2697,
        )
        assert le_mans != inter
        lignes = {r['cle']: r for r in conn.execute('SELECT * FROM equipes')}
        assert len(lignes) == 2
        assert lignes[le_mans]['slug'] != lignes[inter]['slug']
        assert lignes[le_mans]['espn_id'] == 2697
        # L'id SofaScore 2697 est déjà pris côté ESPN : on ne le vole pas.
        assert lignes[inter]['sofascore_id'] in (2697, None)
        assert 'mans' not in lignes[inter]['nom'].lower()


def test_deux_fournisseurs_ecrivent_dans_la_meme_equipe(base):
    """« Feyenoord » et « Feyenoord Rotterdam » ne font qu'un club."""
    with base.connect() as conn:
        a = base.resoudre_equipe(conn, nom='Feyenoord', provider='sofascore', provider_id=11)
        b = base.resoudre_equipe(
            conn, nom='Feyenoord Rotterdam', provider='espn', provider_id=99,
        )
        assert a == b
        n = conn.execute('SELECT COUNT(*) AS n FROM equipes').fetchone()['n']
        assert n == 1
        row = conn.execute('SELECT * FROM equipes WHERE cle = ?', (a,)).fetchone()
        # Les deux identifiants cohabitent, chacun dans sa colonne.
        assert row['sofascore_id'] == 11
        assert row['espn_id'] == 99
        # Le libellé le plus complet est conservé.
        assert row['nom'] == 'Feyenoord Rotterdam'


def test_slug_jamais_vole(base):
    """Deux clubs différents ne peuvent pas se disputer un slug."""
    with base.connect() as conn:
        a = base.resoudre_equipe(conn, nom='Arsenal', provider='espn', provider_id=1)
        b = base.resoudre_equipe(conn, nom='Arsenal Tula', provider='espn', provider_id=2)
        assert a != b
        slugs = [r['slug'] for r in conn.execute('SELECT slug FROM equipes')]
        assert len(slugs) == len(set(slugs))


def _match(base, conn, cle='PL:2026-09-20:arsenal:chelsea'):
    dom = base.resoudre_equipe(conn, nom='Arsenal', provider='espn', provider_id=359)
    ext = base.resoudre_equipe(conn, nom='Chelsea', provider='espn', provider_id=363)
    base.upsert_competition(
        conn, code='PL', nom='Premier League', pays='Angleterre',
        ordre=20, sofascore_id=None,
    )
    base.upsert_match(conn, {
        'cle': cle, 'competition_code': 'PL', 'domicile_cle': dom,
        'exterieur_cle': ext, 'coup_denvoi': '2026-09-20T15:00:00+00:00',
        'statut': 'a_venir', 'espn_id': 4242,
    })
    return cle


def test_cotes_historisees_et_mouvement(base):
    """L'ouverture est conservée : sans elle, aucun mouvement n'est observable."""
    with base.connect() as conn:
        cle = _match(base, conn)
        base.upsert_cotes(conn, cle, 'espn', '1X2',
                          [('1', 2.10), ('N', 3.40), ('2', 3.50)], phase='ouverture')
        base.upsert_cotes(conn, cle, 'espn', '1X2',
                          [('1', 1.90), ('N', 3.50), ('2', 4.00)], phase='courante')
        base.upsert_cotes(conn, cle, 'espn', 'TOTAUX',
                          [('over', 1.83), ('under', 1.95)], ligne=3.5, phase='courante')

        mvt = base.mouvement_cotes(conn, cle, 'espn')
        assert mvt['1'] > 0  # le domicile s'est raccourci : l'argent est allé dessus
        assert mvt['2'] < 0

        hist = conn.execute(
            'SELECT COUNT(*) AS n FROM cotes_historique WHERE cle = ?', (cle,)
        ).fetchone()['n']
        assert hist == 8  # 3 + 3 + 2 relevés distincts

        utiles = base.cotes_utilisables(conn, cle)
        assert utiles['1x2'] == (1.90, 3.50, 4.00)
        assert utiles['totaux'] == (1.83, 1.95, 3.5)
        assert utiles['source'] == 'espn'


def test_cotes_identiques_ne_gonflent_pas_l_historique(base):
    with base.connect() as conn:
        cle = _match(base, conn)
        for _ in range(4):
            base.upsert_cotes(conn, cle, 'espn', '1X2',
                              [('1', 1.90), ('N', 3.50), ('2', 4.00)])
        n = conn.execute(
            'SELECT COUNT(*) AS n FROM cotes_historique WHERE cle = ?', (cle,)
        ).fetchone()['n']
        assert n == 3


def test_predictions_en_ajout_seul(base):
    """Une prédiction figée n'est jamais réécrite, et seule celle d'avant compte."""
    with base.connect() as conn:
        cle = _match(base, conn)
        base.enregistrer_prediction(
            conn, cle, {'version_moteur': '4.0.0', 'options': [{'code': 'OV_1.5'}]},
            minutes_avant=600, avant_match=True,
        )
        base.enregistrer_prediction(
            conn, cle, {'version_moteur': '4.0.0', 'options': [{'code': 'UN_3.5'}]},
            minutes_avant=45, avant_match=True,
        )
        base.enregistrer_prediction(
            conn, cle, {'version_moteur': '4.0.0', 'options': [{'code': 'TRICHE'}]},
            minutes_avant=-30, avant_match=False,
        )
        n = conn.execute(
            'SELECT COUNT(*) AS n FROM predictions WHERE cle = ?', (cle,)
        ).fetchone()['n']
        assert n == 3
        officielle = base.prediction_officielle(conn, cle)
        assert officielle['options'][0]['code'] == 'UN_3.5'
        assert base.a_prediction_avant_match(conn, cle)


def test_match_commence_exclu_de_l_analyse(base):
    """Un match en cours ne doit jamais être analysé : ses cotes voient le terrain."""
    with base.connect() as conn:
        base.resoudre_equipe(conn, nom='Arsenal', provider='espn', provider_id=359)
        base.resoudre_equipe(conn, nom='Chelsea', provider='espn', provider_id=363)
        base.upsert_competition(
            conn, code='PL', nom='Premier League', pays='Angleterre',
            ordre=20, sofascore_id=None,
        )
        base.upsert_match(conn, {
            'cle': 'PL:2020-01-01:arsenal:chelsea', 'competition_code': 'PL',
            'domicile_cle': 'arsenal', 'exterieur_cle': 'chelsea',
            'coup_denvoi': '2020-01-01T15:00:00+00:00', 'statut': 'a_venir',
        })
        assert base.matchs_a_analyser(conn) == []


def test_statut_termine_ne_regresse_pas(base):
    with base.connect() as conn:
        cle = _match(base, conn)
        base.upsert_match(conn, {
            'cle': cle, 'competition_code': 'PL', 'domicile_cle': 'arsenal',
            'exterieur_cle': 'chelsea', 'coup_denvoi': '2026-09-20T15:00:00+00:00',
            'statut': 'termine', 'buts_dom': 2, 'buts_ext': 1,
        })
        base.upsert_match(conn, {
            'cle': cle, 'competition_code': 'PL', 'domicile_cle': 'arsenal',
            'exterieur_cle': 'chelsea', 'coup_denvoi': '2026-09-20T15:00:00+00:00',
            'statut': 'a_venir',
        })
        row = conn.execute('SELECT * FROM matchs WHERE cle = ?', (cle,)).fetchone()
        assert row['statut'] == 'termine'
        assert row['buts_dom'] == 2
        # Le résultat a rejoint la mémoire longue.
        res = conn.execute('SELECT * FROM resultats WHERE cle = ?', (cle,)).fetchone()
        assert res is not None and res['buts_ext'] == 1
