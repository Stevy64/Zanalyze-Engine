"""Contrôle de plausibilité : ce qui protège l'utilisateur des données absurdes."""
from engine.qualite import (
    auditer,
    conflits_calendrier,
    doublons_residuels,
    est_bloquant,
    verifier_cotes,
    verifier_match,
)


def _m(cle, dom, ext, quand, **extra):
    base = {
        'cle': cle, 'competition_code': cle.split(':')[0],
        'domicile_slug': dom, 'exterieur_slug': ext,
        'coup_denvoi': quand, 'statut': 'a_venir',
    }
    base.update(extra)
    return base


def test_equipe_contre_elle_meme():
    a = verifier_match(_m('PL:2026-09-20:arsenal:arsenal', 'arsenal', 'arsenal',
                          '2026-09-20T15:00:00+00:00'))
    assert [x.code for x in a] == ['equipe_contre_elle_meme']
    assert est_bloquant(a)


def test_score_aberrant():
    a = verifier_match(_m('PL:2026-09-20:a:b', 'a', 'b', '2026-09-20T15:00:00+00:00',
                          buts_dom=42, buts_ext=0))
    assert any(x.code == 'score_aberrant' for x in a)
    assert est_bloquant(a)


def test_mi_temps_superieure_au_score_final():
    a = verifier_match(_m('PL:2026-09-20:a:b', 'a', 'b', '2026-09-20T15:00:00+00:00',
                          buts_dom=1, buts_ext=0, buts_dom_mt=3, buts_ext_mt=0))
    assert any(x.code == 'mi_temps_incoherente' for x in a)


def test_chevauchement_calendrier():
    """Une équipe ne peut pas jouer deux fois dans la même soirée.

    C'est le filet qui attrape une identité mal résolue : si deux clubs ont
    été confondus, leurs calendriers se télescopent.
    """
    matchs = [
        _m('UCL:2026-09-08:real-madrid:inter', 'real-madrid', 'inter',
           '2026-09-08T19:00:00+00:00'),
        _m('LIGA:2026-09-08:real-madrid:betis', 'real-madrid', 'betis',
           '2026-09-08T21:00:00+00:00'),
    ]
    anomalies = conflits_calendrier(matchs)
    assert len(anomalies) == 1
    assert anomalies[0].code == 'chevauchement_calendrier'
    assert 'real-madrid' in anomalies[0].detail


def test_pas_de_chevauchement_a_trois_jours():
    matchs = [
        _m('UCL:2026-09-08:real-madrid:inter', 'real-madrid', 'inter',
           '2026-09-08T19:00:00+00:00'),
        _m('LIGA:2026-09-12:real-madrid:betis', 'real-madrid', 'betis',
           '2026-09-12T19:00:00+00:00'),
    ]
    assert conflits_calendrier(matchs) == []


def test_match_reporte_ne_declenche_pas_de_conflit():
    matchs = [
        _m('UCL:2026-09-08:a:b', 'a', 'b', '2026-09-08T19:00:00+00:00'),
        _m('LIGA:2026-09-08:a:c', 'a', 'c', '2026-09-08T20:00:00+00:00',
           statut='reporte'),
    ]
    assert conflits_calendrier(matchs) == []


def test_doublon_residuel_detecte():
    matchs = [
        _m('UCL:2026-09-08:barcelona:feyenoord', 'barcelona', 'feyenoord',
           '2026-09-08T19:00:00+00:00'),
        _m('UCL:2026-09-08:barcelona:feyenoord#2', 'barcelona', 'feyenoord',
           '2026-09-08T19:05:00+00:00'),
    ]
    anomalies = doublons_residuels(matchs)
    assert len(anomalies) == 1 and anomalies[0].code == 'doublon_residuel'


def test_audit_regroupe_par_cle():
    matchs = [
        _m('PL:2026-09-20:a:a', 'a', 'a', '2026-09-20T15:00:00+00:00'),
        _m('PL:2026-09-20:b:c', 'b', 'c', '2026-09-20T15:00:00+00:00'),
    ]
    resultat = auditer(matchs)
    assert 'PL:2026-09-20:a:a' in resultat
    assert 'PL:2026-09-20:b:c' not in resultat


# --------------------------------------------------------------------------
# Cotes
# --------------------------------------------------------------------------

def test_cotes_saines():
    assert verifier_cotes((1.90, 3.50, 4.00)) == []
    assert verifier_cotes(None, 2.5, (1.83, 1.95)) == []
    assert verifier_cotes(None, 5.5, (2.20, 1.66)) == []


def test_marge_excessive():
    assert 'marge_excessive' in verifier_cotes((1.20, 1.20, 1.20))


def test_nul_favori_signale():
    """Un nul moins cher que les deux camps trahit un flux corrompu."""
    assert 'nul_favori' in verifier_cotes((3.00, 1.50, 3.20))


def test_ligne_hors_bornes_et_non_standard():
    assert 'ligne_hors_bornes' in verifier_cotes(None, 99, (1.9, 1.9))
    assert 'ligne_non_standard' in verifier_cotes(None, 2.6, (1.9, 1.9))


def test_cote_hors_bornes():
    assert 'cote_hors_bornes' in verifier_cotes((0.5, 3.0, 3.0))
