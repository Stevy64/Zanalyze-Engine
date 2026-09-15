"""Tests du moteur : purs, sans accès base."""

import numpy as np
import pytest

from engine.moteur import (
    AnalyseInvalide,
    CODE_FILET,
    FACTEUR_MI_TEMPS,
    P_1X2_MAX_RECO,
    RESIDU_DOUTEUX,
    ajuster,
    analyser,
    classer_journee,
    corriger,
    choisir_trois,
    devig_puissance,
    est_eligible,
    matrice,
    moyennes_par_code,
    profil_match,
    selections_niveaux,
    uniformite_excessive,
)


def test_devig_somme_a_un():
    p = devig_puissance([1.80, 3.60, 4.50])
    assert abs(sum(p) - 1) < 1e-9
    assert all(0 < x < 1 for x in p)


def test_matrice_normalisee():
    M = matrice(1.4, 1.1)
    assert abs(M.sum() - 1) < 1e-9


def test_ajuster_retrouve_ordre_des_favoris():
    lh, la, residu = ajuster(0.55, 0.25, 0.20)
    assert lh > la
    assert residu < 0.05


def test_corriger_interpole_et_borne():
    # Courbe marché +1.5 (v3.1) : ~0.7434 → 0.737
    assert corriger(0.7434, '+1.5') == pytest.approx(0.737, abs=0.01)
    assert corriger(0.5, None) == 0.5
    assert corriger(0.5, 'cle_inconnue') == 0.5
    # Complément : 1 - corriger(1-p)
    p_un = corriger(0.30, ('~', '+2.5'))
    assert 0.005 <= p_un <= 0.995
    # Anciennes clés « famille » : pas de correction aveugle
    assert corriger(0.75, 'Total buts') == 0.75


def test_marche_1x2_et_ou25_non_corriges():
    """Les probs marché ne passent pas par la table de correction."""
    a = analyser((1.90, 3.50, 4.00), (1.80, 2.05), 'A', 'B')
    by = {o['code']: o for o in a['options']}
    p1, pn, p2 = a['p1'], a['pn'], a['p2']
    assert by['1X2_1']['probabilite'] == pytest.approx(p1)
    assert by['1X2_N']['probabilite'] == pytest.approx(pn)
    assert by['1X2_2']['probabilite'] == pytest.approx(p2)
    assert by['OV_2.5']['origine'] == 'marche'
    # corriger changerait un total calculé autour de 0.75 ; le marché reste brut
    brut = a['p_over25']
    assert by['OV_2.5']['probabilite'] == pytest.approx(brut)


def test_mi_temps_utilise_sa_propre_matrice():
    lh, la = 1.6, 1.1
    a = analyser((1.70, 3.80, 5.00), (1.75, 2.15), 'A', 'B')
    # Recalcule la référence : 0,45 × λ, pas une fraction du total
    Mh = matrice(FACTEUR_MI_TEMPS * a['buts_dom_attendus'],
                 FACTEUR_MI_TEMPS * a['buts_ext_attendus'])
    n = Mh.shape[0]
    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    p_ref = float(Mh[(i + j) > 0.5].sum())
    by = {o['code']: o for o in a['options']}
    # Après correction Mi-temps, on n'exige pas l'égalité brute,
    # mais l'option HT existe et n'est pas 0,45 × P(OV_0.5) du match.
    p_ov = by['OV_0.5']['probabilite']
    p_ht = by['HT_OV_0.5']['probabilite']
    assert p_ht != pytest.approx(0.45 * p_ov, abs=0.02)
    assert p_ref > 0.3
    assert by['HT_OV_0.5']['famille'] == 'Mi-temps'


def test_residu_stocke_et_drapeau_douteux():
    a = analyser((2.10, 3.30, 3.50), (1.90, 1.95))
    assert 'residu' in a
    assert a['douteuse'] is (a['residu'] > RESIDU_DOUTEUX)


def test_cotes_invalides_refusees():
    with pytest.raises(AnalyseInvalide):
        analyser((0.5, 3.0, 3.0))
    with pytest.raises(AnalyseInvalide):
        analyser((-1.0, 3.0, 3.0))


def test_btts_jamais_eligible():
    a = analyser((2.20, 3.30, 3.30), (1.85, 2.00))
    btts = [o for o in a['options'] if o['famille'] == 'BTTS']
    assert btts
    assert all(not est_eligible(o) for o in btts)


def test_1x2_favori_net_eligible():
    """Victoire sèche ~60–70 % : proposée (plus bloquée à 62 %)."""
    a = analyser((1.45, 4.20, 7.50), (1.70, 2.20))
    un = next(o for o in a['options'] if o['code'] == '1X2_1')
    assert 0.55 <= un['probabilite'] < P_1X2_MAX_RECO
    assert est_eligible(un)


def test_1x2_lock_trop_court_non_eligible():
    """Au-delà du plafond (~75 %) : trop « lock », non recommandé."""
    a = analyser((1.25, 6.00, 11.00), (1.55, 2.45))
    un = next(o for o in a['options'] if o['code'] == '1X2_1')
    assert un['probabilite'] >= P_1X2_MAX_RECO
    assert not est_eligible(un)


def test_filet_exclu_du_classement():
    a = analyser((2.10, 3.40, 3.40), (1.90, 1.95), 'A', 'B')
    moy = moyennes_par_code([a['options']])
    out = choisir_trois(a['options'], a['profil'], moy)
    filet = next(o for o in out if o['code'] == CODE_FILET)
    assert filet['niveau'] == 'filet'
    recos = [o for o in out if o['niveau'] in ('prudente', 'equilibree', 'audacieuse')]
    assert all(o['code'] != CODE_FILET for o in recos)


def test_recommandee_meme_famille_proba_max():
    a = analyser((2.10, 3.40, 3.40), (1.90, 1.95), 'A', 'B')
    out = choisir_trois(a['options'], a['profil'], moyennes_par_code([a['options']]))
    prudente = next(o for o in out if o['niveau'] == 'prudente')
    reco = next((o for o in out if o['niveau'] == 'recommandee'), None)
    assert reco is not None
    assert reco['famille'] == prudente['famille']
    assert reco['code'] != CODE_FILET
    assert reco['code'] != prudente['code']
    # Meilleure proba parmi les options détail restantes de la famille.
    details_famille = [
        o for o in out
        if o['niveau'] == 'detail'
        and o['famille'] == prudente['famille']
        and o['code'] != CODE_FILET
    ]
    assert all(float(reco['probabilite']) >= float(o['probabilite']) for o in details_famille)
    assert sum(1 for o in out if o['niveau'] == 'recommandee') == 1
    assert reco['code'] not in {
        o['code'] for o in out if o['niveau'] in ('prudente', 'equilibree', 'audacieuse')
    }


def test_trois_familles_distinctes():
    a = analyser((2.05, 3.40, 3.60), (1.85, 2.00), 'A', 'B')
    out = choisir_trois(a['options'], a['profil'], moyennes_par_code([a['options']]))
    recos = [o for o in out if o['niveau'] in ('prudente', 'equilibree', 'audacieuse')]
    assert len(recos) == 3
    assert len({o['famille'] for o in recos}) == 3


def test_detecteur_uniformite_echoue_volontairement_sur_classement_casse():
    """Le détecteur doit crier si la même option sort partout au même niveau."""
    casse = [{'prudente': 'OV_1.5', 'equilibree': 'DC_1X', 'audacieuse': 'UN_2.5'}
             for _ in range(9)]
    assert uniformite_excessive(casse) is True


def test_classement_journee_pas_uniforme():
    """Sur une journée variée, le vrai classement ne doit pas être cassé."""
    cotes = [
        ((1.35, 5.00, 9.00), (1.55, 2.50)),
        ((1.40, 4.80, 8.00), (1.60, 2.40)),
        ((1.55, 4.20, 6.50), (1.70, 2.20)),
        ((1.90, 3.50, 4.10), (1.85, 2.00)),
        ((2.20, 3.30, 3.30), (1.95, 1.90)),
        ((2.40, 3.20, 3.00), (2.05, 1.80)),
        ((3.10, 3.30, 2.30), (1.90, 1.95)),
        ((4.50, 3.70, 1.75), (1.80, 2.05)),
        ((6.50, 4.40, 1.50), (1.65, 2.30)),
        ((8.00, 5.00, 1.38), (1.58, 2.45)),
        ((2.05, 3.25, 3.80), (2.20, 1.70)),
        ((2.80, 3.10, 2.70), (1.72, 2.15)),
    ]
    analyses = [analyser(c1x2, ou, f'D{i}', f'E{i}') for i, (c1x2, ou) in enumerate(cotes)]
    classer_journee(analyses)
    sels = [selections_niveaux(a['options']) for a in analyses]
    assert uniformite_excessive(sels) is False
    for s in sels:
        assert len(s) == 3
        assert len(set(s.values())) == 3


def test_profil_gros_favori_et_equilibre():
    assert profil_match(0.62, 0.18) == 'desequilibre'
    assert profil_match(0.34, 0.33) == 'equilibre'


def test_coherence_complementaires_apres_calibration():
    a = analyser((2.10, 3.40, 3.40), None, 'A', 'B')
    assert a['incoherence'] < 1e-9
    by = {o['code']: o for o in a['options']}
    assert by['OV_1.5']['probabilite'] + by['UN_1.5']['probabilite'] == pytest.approx(1.0)
    assert by['OV_3.5']['probabilite'] + by['UN_3.5']['probabilite'] == pytest.approx(1.0)


def test_plafond_forme_sur_journee():
    from collections import Counter
    from engine.moteur import forme_pari

    cotes = [
        ((1.35, 5.00, 9.00), (1.55, 2.50)),
        ((1.40, 4.80, 8.00), (1.60, 2.40)),
        ((1.55, 4.20, 6.50), (1.70, 2.20)),
        ((1.90, 3.50, 4.10), (1.85, 2.00)),
        ((2.20, 3.30, 3.30), (1.95, 1.90)),
        ((2.40, 3.20, 3.00), (2.05, 1.80)),
        ((3.10, 3.30, 2.30), (1.90, 1.95)),
        ((4.50, 3.70, 1.75), (1.80, 2.05)),
        ((6.50, 4.40, 1.50), (1.65, 2.30)),
        ((8.00, 5.00, 1.38), (1.58, 2.45)),
        ((2.05, 3.25, 3.80), (2.20, 1.70)),
        ((2.80, 3.10, 2.70), (1.72, 2.15)),
    ]
    analyses = [analyser(c1x2, ou, f'D{i}', f'E{i}') for i, (c1x2, ou) in enumerate(cotes)]
    classer_journee(analyses)
    formes = Counter()
    for a in analyses:
        for o in a['options']:
            if o['niveau'] in ('prudente', 'equilibree', 'audacieuse'):
                formes[forme_pari(o['code'], o['libelle'])] += 1
    assert formes
    assert max(formes.values()) <= 3
