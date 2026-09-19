"""Mémoire longue : la base CI peut disparaître, l'archive non."""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def moteur(tmp_path, monkeypatch):
    monkeypatch.setenv('ENGINE_DATA_DIR', str(tmp_path))
    from engine import archive, paths, store
    paths.DATA_DIR = tmp_path
    paths.DB_PATH = tmp_path / 'e.sqlite3'
    paths.ARCHIVE_DIR = tmp_path / 'archive'
    store.DB_PATH = paths.DB_PATH
    archive.ARCHIVE_DIR = paths.ARCHIVE_DIR
    store.init_db()
    return store


def _match_regle(store, conn, jour, cle_suffixe, buts=(2, 1), options=None):
    """Un match terminé, avec sa prédiction figée avant le coup d'envoi."""
    dom = store.resoudre_equipe(conn, nom=f'Dom{cle_suffixe}', provider='espn')
    ext = store.resoudre_equipe(conn, nom=f'Ext{cle_suffixe}', provider='espn')
    store.upsert_competition(
        conn, code='PL', nom='Premier League', pays='Angleterre',
        ordre=20, sofascore_id=None,
    )
    cle = f'PL:{jour}:{dom}:{ext}'
    store.upsert_match(conn, {
        'cle': cle, 'competition_code': 'PL', 'domicile_cle': dom,
        'exterieur_cle': ext, 'coup_denvoi': f'{jour}T15:00:00+00:00',
        'statut': 'termine', 'buts_dom': buts[0], 'buts_ext': buts[1],
        'buts_dom_mt': 1, 'buts_ext_mt': 0,
    })
    store.enregistrer_prediction(conn, cle, {
        'version_moteur': '4.0.0',
        'options': options or [
            {'code': 'OV_1.5', 'probabilite': 0.70, 'p_brute': 0.68, 'origine': 'calcul'},
            {'code': 'UN_3.5', 'probabilite': 0.62, 'p_brute': 0.65, 'origine': 'calcul'},
            {'code': '1X2_1', 'probabilite': 0.50, 'p_brute': 0.50, 'origine': 'marche'},
        ],
    }, minutes_avant=90, avant_match=True)
    return cle


def test_export_puis_relecture(moteur):
    from engine import archive
    with moteur.connect() as conn:
        _match_regle(moteur, conn, '2026-03-04', 'A')
        _match_regle(moteur, conn, '2026-03-11', 'B', buts=(0, 0))
        stats = archive.exporter(conn)
        archive.exporter_resultats(conn)

    # L'archive conserve toutes les options réglées, y compris celles issues
    # du marché : elles servent au bilan. L'apprentissage, lui, n'en retient
    # que les probabilités calculées.
    assert stats['ajoutees'] == 6  # 3 options × 2 matchs
    observations = archive.charger_observations()
    assert len(observations) == 4
    assert {ob.marche for ob in observations} == {'+1.5', '+3.5'}
    assert {ob.ligue for ob in observations} == {'PL'}
    # « Moins de 4 buts » est le complément de « plus de 3,5 ».
    assert any(ob.complement for ob in observations)
    # 2-1 : au moins 2 buts gagné, moins de 4 buts gagné.
    gagnees = {(ob.marche, ob.complement): ob.y for ob in observations
               if ob.quand.startswith('2026-03-04')}
    assert gagnees[('+1.5', False)] == 1
    assert gagnees[('+3.5', True)] == 1


def test_export_idempotent(moteur):
    from engine import archive
    with moteur.connect() as conn:
        _match_regle(moteur, conn, '2026-03-04', 'A')
        premier = archive.exporter(conn)
        second = archive.exporter(conn)
    assert premier['ajoutees'] == 3
    assert second['ajoutees'] == 0
    assert len(archive.charger_observations()) == 2


def test_probabilites_de_marche_exclues_de_l_apprentissage(moteur):
    """Corriger une probabilité lue sur le marché reviendrait à apprendre
    le bruit du bookmaker."""
    from engine import archive
    with moteur.connect() as conn:
        _match_regle(moteur, conn, '2026-03-04', 'A')
    observations = archive.charger_observations()
    assert all(ob.marche != '1' for ob in observations)


def test_prediction_posterieure_au_match_ignoree(moteur):
    """Une analyse calculée après coup n'est pas une prédiction."""
    from engine import archive
    with moteur.connect() as conn:
        cle = _match_regle(moteur, conn, '2026-03-04', 'A')
        moteur.enregistrer_prediction(conn, cle, {
            'version_moteur': '4.0.0',
            'options': [{'code': 'OV_4.5', 'probabilite': 0.99, 'p_brute': 0.99,
                         'origine': 'calcul'}],
        }, minutes_avant=-120, avant_match=False)
        archive.exporter(conn)
    codes = {ob.marche for ob in archive.charger_observations()}
    assert '+4.5' not in codes


def test_statistiques(moteur):
    from engine import archive
    with moteur.connect() as conn:
        _match_regle(moteur, conn, '2026-03-04', 'A')
        _match_regle(moteur, conn, '2026-04-08', 'B')
        archive.exporter(conn)
        archive.exporter_resultats(conn)
    stats = archive.statistiques()
    assert stats['observations'] == 6
    assert stats['resultats'] == 2
    assert stats['mois_couverts'] == ['2026-03', '2026-04']


def test_date_football_data():
    from engine.archive import _date_football_data
    assert _date_football_data('17/08/2024') == '2024-08-17'
    assert _date_football_data('17/08/24') == '2024-08-17'
    assert _date_football_data('pas une date') is None
    assert _date_football_data('') is None


def test_ingestion_football_data(moteur):
    """Amorçage de l'archive par un CSV de résultats historiques."""
    from engine.archive import _ingerer_csv_football_data
    csv = (
        'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR\n'
        'E0,17/08/2024,Man United,Fulham,1,0,H,0,0,D\n'
        'E0,17/08/2024,Ipswich,Liverpool,0,2,A,0,0,D\n'
        'E0,mauvaise ligne,,,,,,,,\n'
    )
    with moteur.connect() as conn:
        n = _ingerer_csv_football_data(conn, csv, 'PL')
        lignes = list(conn.execute('SELECT * FROM resultats ORDER BY cle'))
    assert n == 2
    assert len(lignes) == 2
    assert lignes[0]['buts_dom'] + lignes[0]['buts_ext'] in (1, 2)
    assert all(l['buts_dom_mt'] == 0 for l in lignes)
    # Les noms passent par la même normalisation que le flux courant.
    cles = {l['domicile_cle'] for l in lignes}
    assert 'manchester-united' in cles


def test_cycle_complet_archive_vers_calibration(moteur):
    """Une base vidée ne doit pas effacer ce que le moteur a appris."""
    from engine import archive
    from engine.apprentissage import evaluer_candidate

    # Cinq niveaux d'annonce : une courbe a besoin de plusieurs appuis pour
    # exister, et le protocole coupe l'historique en trois (apprentissage,
    # arbitrage, test) — il faut donc de quoi remplir les trois tranches.
    import random
    rng = random.Random(4)
    jour0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    with moteur.connect() as conn:
        for i in range(1800):
            jour = (jour0 + timedelta(days=i // 6)).date().isoformat()
            annonce = (0.25, 0.40, 0.55, 0.70, 0.85)[i % 5]
            reel = min(annonce + 0.15, 0.98)  # le moteur sous-estime
            gagne = rng.random() < reel
            _match_regle(
                moteur, conn, jour, f'{i}',
                buts=(2, 1) if gagne else (1, 0),
                options=[{'code': 'OV_1.5', 'probabilite': annonce,
                          'p_brute': annonce, 'origine': 'calcul'}],
            )
        archive.exporter(conn)

    observations = archive.charger_observations()
    assert len(observations) == 1800
    rapport = evaluer_candidate(observations)
    assert rapport['retenue'] is True
    assert rapport['brier_candidate'] < rapport['brier_sans_correction']


# --------------------------------------------------------------------------
# Coupes d'Europe
# --------------------------------------------------------------------------

EXTRAIT_UCL = """= UEFA Champions League 2023/24

▪ Group, Matchday 1
  Tue Sep 19 2023
    18:45  AC Milan (ITA)          v Newcastle United FC (ENG)  0-0
           BSC Young Boys (SUI)    v RB Leipzig (GER)         1-3 (1-1)
  Wed Sep 20
    21:00  FC Bayern München (GER) v Manchester United FC (ENG)  4-3 (2-0)

▪ Finals, Final
  Sat Jun 1 2024
    21:00  Borussia Dortmund (GER) v Real Madrid CF (ESP)     0-2 (0-0)
"""


def test_lecture_football_txt():
    """Le score de la pause est entre parenthèses ; l'année se reporte."""
    from engine.archive import lire_football_txt

    m = lire_football_txt(EXTRAIT_UCL, 'UCL')
    assert len(m) == 4
    assert m[0]['date'] == '2023-09-19'
    assert m[0]['bd'] == 0 and m[0]['bdm'] is None      # pas de mi-temps publiée
    assert m[1]['bdm'] == 1 and m[1]['bem'] == 1
    # L'année n'est répétée qu'au premier jour du bloc : elle doit se reporter.
    assert m[2]['date'] == '2023-09-20'
    assert m[2]['bd'] == 4 and m[2]['be'] == 3
    assert m[3]['date'] == '2024-06-01'
    assert all(x['ligue'] == 'UCL' for x in m)


def test_lecture_football_txt_ignore_le_bavardage():
    from engine.archive import lire_football_txt

    assert lire_football_txt('# Teams 32\n\nrien ici\n', 'UCL') == []


def test_noms_europeens_passent_par_la_normalisation():
    """« FC Bayern München » et « Bayern Munich » doivent se rejoindre."""
    from engine.archive import lire_football_txt
    from engine.identite import cle_equipe

    m = lire_football_txt(EXTRAIT_UCL, 'UCL')
    cles = {cle_equipe(x['dom']) for x in m} | {cle_equipe(x['ext']) for x in m}
    assert 'bayern-munich' in cles
    assert 'real-madrid' in cles
    assert 'manchester-united' in cles
