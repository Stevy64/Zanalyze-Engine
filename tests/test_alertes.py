"""Les pannes doivent crier, pas disparaître.

Trois pannes réelles, constatées entre le 20 septembre et le 9 octobre 2026 :
l'ingestion des résultats s'est arrêtée, la boucle d'apprentissage n'a plus
rien eu à régler, et le dépôt a continué de publier un instantané toutes les
deux heures en se déclarant en succès pendant dix-neuf jours.

Ce qui l'a permis : une erreur de transport traitée comme « cette ligue ne
joue pas ce jour-là ». Ces tests verrouillent la distinction.
"""
from datetime import datetime, timedelta, timezone

import pytest

from engine import espn
from engine.espn import journal_vierge
from engine.pipeline import (
    COMPETITIONS_MAJEURES,
    ECHECS_TOLERES,
    JOURS_SANS_REGLEMENT,
    alertes,
)
from engine.store import (
    connect,
    resoudre_equipe,
    save_analyse,
    upsert_competition,
    upsert_match,
)


# --------------------------------------------------------------------------
# Le journal de collecte
# --------------------------------------------------------------------------

def test_un_refus_espn_n_est_pas_un_jour_sans_match(monkeypatch):
    """Le cœur du défaut : les deux cas doivent se distinguer."""
    def refuse(url, timeout=25):
        raise espn.EspnErreur('ESPN 429 sur ' + url)

    monkeypatch.setattr(espn, '_get', refuse)
    j = journal_vierge()
    out = espn.evenements_fenetre('eng.1', jours_passes=2, jours_futurs=2,
                                  journal=j, code='PL')
    assert out == []
    assert j['requetes'] == 5
    assert j['jours_en_echec'] == 5
    assert j['jours_repondus'] == 0
    assert j['competitions_en_echec']['PL'] == 5
    assert j['echecs'][0]['competition'] == 'PL'
    assert '429' in j['echecs'][0]['erreur']


def test_un_vrai_jour_sans_match_ne_compte_aucun_echec(monkeypatch):
    monkeypatch.setattr(espn, '_get', lambda url, timeout=25: {'events': []})
    monkeypatch.setattr(espn.time, 'sleep', lambda *_: None)
    j = journal_vierge()
    espn.evenements_fenetre('eng.1', jours_passes=1, jours_futurs=1,
                            journal=j, code='PL')
    assert j['jours_en_echec'] == 0
    assert j['jours_repondus'] == 3
    assert j['evenements_par_competition']['PL'] == 0


def test_le_journal_reste_borne(monkeypatch):
    """Vingt échecs détaillés suffisent : le reste est compté, pas narré."""
    monkeypatch.setattr(
        espn, '_get',
        lambda url, timeout=25: (_ for _ in ()).throw(espn.EspnErreur('nope')),
    )
    j = journal_vierge()
    espn.evenements_fenetre('eng.1', jours_passes=40, jours_futurs=40,
                            journal=j, code='PL')
    assert j['jours_en_echec'] == 81
    assert len(j['echecs']) == 20


def test_journal_optionnel(monkeypatch):
    """Sans journal, le comportement d'origine est inchangé."""
    monkeypatch.setattr(espn, '_get', lambda url, timeout=25: {'events': []})
    monkeypatch.setattr(espn.time, 'sleep', lambda *_: None)
    assert espn.evenements_fenetre('eng.1', jours_passes=0, jours_futurs=0) == []


# --------------------------------------------------------------------------
# Les alertes
# --------------------------------------------------------------------------

def codes(liste):
    return {a['code'] for a in liste}


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """Base neuve et isolée. Les chemins sont des constantes de module :
    les patcher est le seul moyen d'éviter qu'un test hérite du précédent."""
    monkeypatch.setenv('ENGINE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ENGINE_ARCHIVE_DIR', str(tmp_path / 'archive'))
    from engine import paths, store
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(paths, 'DB_PATH', tmp_path / 'e.sqlite3')
    monkeypatch.setattr(paths, 'ARCHIVE_DIR', tmp_path / 'archive')
    monkeypatch.setattr(store, 'DB_PATH', paths.DB_PATH)
    store.init_db()
    return store


def test_une_competition_majeure_muette_declenche_l_alerte(base):
    j = journal_vierge()
    j['evenements_par_competition'] = {c: 5 for c in COMPETITIONS_MAJEURES}
    j['evenements_par_competition']['PL'] = 0
    out = alertes(j)
    assert 'ingestion_muette' in codes(out)
    assert 'PL' in next(a for a in out if a['code'] == 'ingestion_muette')['detail']


def test_aucune_alerte_quand_tout_repond(base):
    j = journal_vierge()
    j['evenements_par_competition'] = {c: 5 for c in COMPETITIONS_MAJEURES}
    assert alertes(j) == []


def test_une_competition_non_interrogee_ne_declenche_rien(base):
    """Hors saison, une compétition peut ne pas être au calendrier du tout.

    On n'alerte que sur une compétition interrogée qui ne renvoie rien, pas
    sur une absente de la collecte.
    """
    j = journal_vierge()
    j['evenements_par_competition'] = {'PL': 5}
    assert codes(alertes(j)) == set()


def test_trop_d_echecs_reseau_declenche_l_alerte(base):
    j = journal_vierge()
    j['evenements_par_competition'] = {c: 5 for c in COMPETITIONS_MAJEURES}
    j['requetes'] = 400
    j['jours_en_echec'] = ECHECS_TOLERES + 1
    j['echecs'] = [{'competition': 'SA', 'jour': '20261003', 'erreur': 'ESPN 429'}]
    out = alertes(j)
    assert 'ingestion_en_echec' in codes(out)
    assert '429' in next(a for a in out if a['code'] == 'ingestion_en_echec')['detail']


def _match_termine_avec_analyse(conn, cle, jours_avant, *, regle):
    quand = (datetime.now(timezone.utc) - timedelta(days=jours_avant)).isoformat()
    upsert_competition(conn, code='PL', nom='Premier League', pays='Angleterre',
                       ordre=20, sofascore_id=None)
    dom = resoudre_equipe(conn, nom='Arsenal', provider='espn', provider_id=359)
    ext = resoudre_equipe(conn, nom='Chelsea', provider='espn', provider_id=363)
    upsert_match(conn, {
        'cle': cle, 'competition_code': 'PL', 'domicile_cle': dom,
        'exterieur_cle': ext, 'coup_denvoi': quand, 'journee': '',
        'statut': 'termine', 'buts_dom': 2, 'buts_ext': 1,
        'buts_dom_mt': 1, 'buts_ext_mt': 0,
    })
    save_analyse(conn, cle, {'options': [
        {'code': '1X2_1', 'resultat': 'gagne' if regle else 'attente'},
    ]})


def test_boucle_figee_declenche_l_alerte(base):
    """La panne de septembre : des matchs terminés, et rien de réglé."""
    with connect() as conn:
        _match_termine_avec_analyse(conn, 'PL:x', 2, regle=False)
    out = alertes()
    assert 'apprentissage_fige' in codes(out)


def test_boucle_vivante_ne_declenche_rien(base):
    with connect() as conn:
        _match_termine_avec_analyse(conn, 'PL:y', 2, regle=True)
    assert codes(alertes()) == set()


def test_un_reglement_trop_ancien_ne_compte_pas(base):
    """Un règlement d'il y a trois semaines ne prouve rien sur aujourd'hui."""
    with connect() as conn:
        _match_termine_avec_analyse(
            conn, 'PL:z', JOURS_SANS_REGLEMENT + 3, regle=True)
    assert 'apprentissage_fige' in codes(alertes())


def test_base_vide_ne_declenche_rien(base):
    """Au premier démarrage, il n'y a rien à régler : ce n'est pas une panne."""
    assert codes(alertes()) == set()


def test_payload_illisible_n_interrompt_pas_l_audit(base):
    with connect() as conn:
        _match_termine_avec_analyse(conn, 'PL:w', 1, regle=False)
        conn.execute("UPDATE analyses SET payload = '{pas du json' WHERE cle = 'PL:w'")
    assert 'apprentissage_fige' in codes(alertes())
