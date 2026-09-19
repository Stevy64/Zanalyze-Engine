"""Lecture du flux ESPN. Aucun accès réseau : charges utiles figées.

Les formes de charge utile reproduites ici ont été relevées sur l'API publique
ESPN (`.../events/{id}/competitions/{id}/odds/{provider}`) pour trois matchs
réels : une ligne 2,5 en Premier League, une ligne 4,5 (PSG – Slovan) et une
ligne 5,5 (Bayern – Bodø/Glimt).
"""
import pytest

from engine.espn import (
    _statut_espn,
    american_to_decimal,
    normaliser_event,
    score_mi_temps,
)


def test_american_to_decimal():
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(-200) == 1.5
    assert american_to_decimal(135) == 2.35
    assert american_to_decimal(-140) == pytest.approx(1.714, abs=0.001)
    assert american_to_decimal(None) is None
    assert american_to_decimal(0) is None


def test_statut_espn():
    assert _statut_espn({'state': 'pre', 'completed': False}) == 'a_venir'
    assert _statut_espn({'state': 'in', 'name': 'STATUS_HALFTIME'}) == 'en_cours'
    assert _statut_espn(
        {'state': 'post', 'completed': True, 'name': 'STATUS_FULL_TIME'}
    ) == 'termine'
    assert _statut_espn({'name': 'STATUS_POSTPONED'}) == 'reporte'
    # Un match annulé « terminé » côté ESPN ne doit pas compter comme joué.
    assert _statut_espn(
        {'state': 'post', 'completed': True, 'name': 'STATUS_CANCELED'}
    ) == 'reporte'


# --------------------------------------------------------------------------
# Mi-temps reconstruite
# --------------------------------------------------------------------------

def _but(minute, team_id, own=False):
    return {
        'type': {'id': '137', 'text': 'Goal'},
        'clock': {'displayValue': f"{minute}'"},
        'team': {'id': str(team_id)},
        'scoreValue': 1,
        'scoringPlay': True,
        'ownGoal': own,
    }


def test_mi_temps_depuis_les_buts_horodates():
    """`linescores` est vide en football : sans reconstruction, les sept
    options de mi-temps d'un match restent « en attente » pour toujours."""
    comp = {'details': [
        _but(12, 83), _but(39, 83), _but(45, 77), _but(58, 83), _but("90+2", 77),
    ]}
    assert score_mi_temps(comp, 83, 77) == (2, 1)


def test_mi_temps_but_contre_son_camp():
    """Un but contre son camp compte pour l'adversaire."""
    comp = {'details': [_but(20, 83, own=True)]}
    assert score_mi_temps(comp, 83, 77) == (0, 1)


def test_mi_temps_ignore_les_tirs_au_but():
    comp = {'details': [
        _but(30, 83),
        {'type': {'text': 'Penalty Shootout - Scored'}, 'clock': {'displayValue': "120'"},
         'team': {'id': '77'}, 'scoringPlay': True},
    ]}
    assert score_mi_temps(comp, 83, 77) == (1, 0)


def test_mi_temps_absente_si_aucun_evenement():
    assert score_mi_temps({'details': []}, 83, 77) == (None, None)


def test_mi_temps_zero_zero_reste_un_resultat():
    """0-0 à la pause est une information, pas une absence d'information."""
    comp = {'details': [_but(67, 83)]}
    assert score_mi_temps(comp, 83, 77) == (0, 0)


# --------------------------------------------------------------------------
# Normalisation d'un événement
# --------------------------------------------------------------------------

META = {'code': 'UCL', 'nom': 'Ligue des champions', 'pays': 'Europe', 'ordre': 10}


def _event(nom_dom, nom_ext, id_dom=1, id_ext=2, statut='post', details=None):
    return {
        'id': '401915443',
        'date': '2026-09-10T19:00Z',
        'competitions': [{
            'id': '401915443',
            'date': '2026-09-10T19:00Z',
            'status': {'type': {'state': statut, 'completed': statut == 'post',
                                'name': 'STATUS_FULL_TIME'}},
            'competitors': [
                {'homeAway': 'home', 'score': '5',
                 'team': {'id': str(id_dom), 'displayName': nom_dom,
                          'shortDisplayName': nom_dom[:24], 'logo': 'http://x/l.png'}},
                {'homeAway': 'away', 'score': '0',
                 'team': {'id': str(id_ext), 'displayName': nom_ext,
                          'shortDisplayName': nom_ext[:24]}},
            ],
            'details': details or [],
        }],
    }


def test_normaliser_produit_une_cle_canonique():
    ev = _event('Bayern Munich', 'Bodø/Glimt', details=[_but(30, 1)])
    norm = normaliser_event('uefa.champions', META, ev)
    assert norm['cle'] == 'UCL:2026-09-10:bayern-munich:bodo-glimt'
    assert norm['statut'] == 'termine'
    assert norm['buts_dom'] == 5 and norm['buts_ext'] == 0
    assert norm['buts_dom_mt'] == 1 and norm['buts_ext_mt'] == 0


def test_deux_libelles_du_meme_match_donnent_la_meme_cle():
    """Le doublon d'un même match vient de libellés différents, pas du match."""
    a = normaliser_event('uefa.champions', META, _event('FC Bayern München', 'Bodo/Glimt'))
    b = normaliser_event('uefa.champions', META, _event('Bayern Munich', 'Bodø/Glimt'))
    assert a['cle'] == b['cle']


def test_mi_temps_incoherente_ecartee():
    """Trois buts avant la pause pour un score final de 5-0 côté extérieur :
    la reconstruction est rejetée plutôt que de polluer les données."""
    ev = _event('Bayern Munich', 'Bodø/Glimt', details=[_but(10, 2), _but(20, 2)])
    norm = normaliser_event('uefa.champions', META, ev)
    assert norm['buts_ext'] == 0
    assert norm['buts_dom_mt'] is None and norm['buts_ext_mt'] is None


def test_event_sans_equipe_ignore():
    ev = _event('Bayern Munich', '')
    assert normaliser_event('uefa.champions', META, ev) is None
