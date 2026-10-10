"""Une courbe ne dit rien hors du domaine où elle a été ajustée.

`np.interp` écrase silencieusement toute valeur hors plage sur la dernière
valeur connue. Appliquer une courbe hors de son domaine est le même défaut
que lui appliquer la courbe d'un autre marché : la table de « plus de 3,5
buts » ajustée entre 0,106 et 0,532 transformait un 0,662 en 0,452, soit
vingt-un points de « correction » qui ne reposaient sur aucune observation.
"""
import pytest

from engine.apprentissage import Observation, bilan_par_marche, dedoublonner
from engine.calibrage import MARGE_PLAGE, corriger, corriger_detail

# Ajustée entre 0,10 et 0,55 seulement, comme les courbes que la boucle
# produira : les prédictions de production se concentrent.
ETROITE = {'+3.5': [[0.10, 0.14], [0.30, 0.36], [0.55, 0.58]]}


def test_dans_le_domaine_la_courbe_s_applique():
    p, corrigee = corriger_detail(0.30, '+3.5', ETROITE)
    assert corrigee is True
    assert p == pytest.approx(0.36)


def test_hors_domaine_la_probabilite_brute_est_rendue():
    p, corrigee = corriger_detail(0.662, '+3.5', ETROITE)
    assert corrigee is False
    assert p == pytest.approx(0.662)


def test_la_marge_de_tolerance_est_respectee():
    """Juste au bord, on corrige encore ; au-delà de la marge, non."""
    _, dedans = corriger_detail(0.55 + MARGE_PLAGE / 2, '+3.5', ETROITE)
    _, dehors = corriger_detail(0.55 + MARGE_PLAGE * 2, '+3.5', ETROITE)
    assert dedans is True
    assert dehors is False


def test_le_complement_suit_le_meme_verdict():
    """La garantie à ne jamais perdre : les deux sens somment à 100 %."""
    from engine.calibrage import COMPLEMENT

    for p_brute in (0.20, 0.40, 0.662, 0.95):
        direct = corriger(p_brute, '+3.5', ETROITE)
        complement = corriger(1.0 - p_brute, (COMPLEMENT, '+3.5'), ETROITE)
        assert direct + complement == pytest.approx(1.0, abs=1e-12)


def test_sans_courbe_rien_n_est_corrige():
    p, corrigee = corriger_detail(0.42, 'marche-inconnu', ETROITE)
    assert (p, corrigee) == (0.42, False)


def test_l_option_dit_si_elle_a_ete_corrigee(monkeypatch):
    """Le moteur publie le verdict : l'app n'a plus à le devenir."""
    from engine import moteur

    monkeypatch.setattr(moteur, 'tables_calibration', lambda: ETROITE)
    dedans = moteur._option('OV_3.5', 'Total buts', 0.30, 'calcul', 'A', 'B')
    dehors = moteur._option('OV_3.5', 'Total buts', 0.95, 'calcul', 'A', 'B')
    assert dedans['calibree'] is True
    assert dedans['probabilite'] != dedans['p_brute']
    assert dehors['calibree'] is False
    assert dehors['probabilite'] == pytest.approx(dehors['p_brute'])


def test_une_cote_de_marche_n_est_jamais_corrigee(monkeypatch):
    from engine import moteur

    monkeypatch.setattr(moteur, 'tables_calibration', lambda: ETROITE)
    o = moteur._option('OV_3.5', 'Total buts', 0.30, 'marche', 'A', 'B')
    assert o['calibree'] is False
    assert o['probabilite'] == pytest.approx(0.30)


def test_la_boucle_evalue_comme_elle_appliquera():
    """Sinon elle choisit une courbe sur un comportement qu'elle n'aura pas."""
    from engine.apprentissage import _applique

    hors = Observation('+3.5', False, 'PL', 0.95, 1, '2026-10-01T18:00:00+00:00')
    dans = Observation('+3.5', False, 'PL', 0.30, 1, '2026-10-01T18:00:00+00:00')
    assert _applique(hors, ETROITE) == pytest.approx(0.95)
    assert _applique(dans, ETROITE) == pytest.approx(0.36)


# --------------------------------------------------------------------------
# Dédoublonnage des deux sens
# --------------------------------------------------------------------------

def ob(marche, complement, p, y, cle='PL:m1'):
    return cle, Observation(marche, complement, 'PL', p, y,
                            '2026-10-01T18:00:00+00:00')


def test_les_deux_sens_d_un_marche_ne_comptent_qu_une_fois():
    """Mesuré sur les 2 000 options réglées de septembre : 587 couples à deux
    sens, donc 587 matchs comptés deux fois."""
    paires = [ob('+2.5', False, 0.55, 1), ob('+2.5', True, 0.45, 0)]
    assert len(dedoublonner(paires)) == 1


def test_le_sens_direct_est_prefere():
    direct = [ob('+2.5', True, 0.45, 0), ob('+2.5', False, 0.55, 1)]
    (garde,) = dedoublonner(direct)
    assert garde.complement is False
    assert garde.p == pytest.approx(0.55)


def test_un_complement_seul_est_conserve():
    (garde,) = dedoublonner([ob('+2.5', True, 0.45, 0)])
    assert garde.complement is True


def test_deux_matchs_restent_deux_observations():
    paires = [ob('+2.5', False, 0.55, 1, 'PL:m1'),
              ob('+2.5', False, 0.60, 0, 'PL:m2')]
    assert len(dedoublonner(paires)) == 2


def test_la_marge_d_erreur_n_est_plus_sous_estimee():
    """Doubler n divise la marge par racine de deux : 9,0 points annoncés
    contre 12,7 réels sur « plus de 2,5 » en septembre."""
    import math

    un_par_match = [
        Observation('+2.5', False, 'PL', 0.55, i % 2, f'2026-10-{1 + i:02d}')
        for i in range(60)
    ]
    double = un_par_match + [
        Observation('+2.5', True, 'PL', 0.45, 1 - (i % 2), f'2026-10-{1 + i:02d}')
        for i in range(60)
    ]
    marge_juste = bilan_par_marche(un_par_match)[0]['marge_points']
    marge_gonflee = bilan_par_marche(double)[0]['marge_points']
    assert marge_juste == pytest.approx(marge_gonflee * math.sqrt(2), rel=0.02)


def test_la_couverture_est_publiee():
    """31 % des matchs n'ont pas de score à la pause : le bilan doit le dire."""
    lot = [
        Observation('+2.5', False, 'PL', 0.55, 1, f'2026-10-{1 + i:02d}')
        for i in range(30)
    ] + [
        Observation('MT 1', False, 'PL', 0.33, 0, f'2026-10-{1 + i:02d}')
        for i in range(20)
    ]
    par = {b['marche']: b for b in bilan_par_marche(lot)}
    assert par['+2.5']['couverture'] == 100.0
    assert par['MT 1']['couverture'] == pytest.approx(66.7, abs=0.1)
