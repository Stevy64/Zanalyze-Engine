"""Tests client ESPN (purs, sans réseau)."""
from engine.espn import american_to_decimal, _statut_espn


def test_american_to_decimal():
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(-200) == 1.5
    assert american_to_decimal(135) == 2.35
    assert american_to_decimal(None) is None


def test_statut_espn():
    assert _statut_espn({'state': 'pre', 'completed': False}) == 'a_venir'
    assert _statut_espn({'state': 'in', 'name': 'STATUS_HALFTIME'}) == 'en_cours'
    assert _statut_espn({'state': 'post', 'completed': True, 'name': 'STATUS_FULL_TIME'}) == 'termine'
    assert _statut_espn({'name': 'STATUS_POSTPONED'}) == 'reporte'
