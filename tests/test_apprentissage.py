"""Boucle d'auto-amélioration : apprendre, valider, ne publier que si meilleur."""
import random

import pytest

from engine.apprentissage import (
    GAIN_MINIMAL,
    MIN_OBS_MARCHE,
    Observation,
    _coupure_chronologique,
    apprendre,
    bilan_par_marche,
    brier,
    courbe,
    evaluer_candidate,
    log_loss,
)


def observations(n=3000, biais=0.10, marche='+2.5', ligue='PL', graine=7):
    """Marché annoncé à p, qui se produit en réalité à p + biais.

    Reproduit le défaut mesuré sur la journée 1 de Ligue des champions :
    « plus de 2,5 buts » annoncé à 50 % s'était produit 61 % du temps.
    """
    rng = random.Random(graine)
    out = []
    for i in range(n):
        p = rng.uniform(0.08, 0.92)
        vrai = min(max(p + biais, 0.01), 0.99)
        jour = f'2026-{1 + i * 11 // n:02d}-{1 + (i * 7) % 28:02d}'
        out.append(Observation(
            marche=marche, complement=False, ligue=ligue,
            p=p, y=1 if rng.random() < vrai else 0,
            quand=f'{jour}T18:00:00+00:00',
        ))
    out.sort(key=lambda ob: ob.quand)
    return out


def test_courbe_corrige_le_biais():
    """La courbe doit remonter les probabilités d'à peu près le biais réel."""
    c = courbe(observations())
    assert c is not None
    milieu = [(x, y) for x, y in c if 0.3 <= x <= 0.7]
    assert milieu
    corrections = [y - x for x, y in milieu]
    moyenne = sum(corrections) / len(corrections)
    assert 0.05 < moyenne < 0.12


def test_courbe_croissante_et_ancree():
    c = courbe(observations())
    ys = [y for _, y in c]
    assert ys == sorted(ys)
    assert c[0][0] == pytest.approx(0.0, abs=0.03)
    assert c[-1][0] == pytest.approx(1.0, abs=0.03)


def test_pas_de_courbe_sous_le_seuil_de_volume():
    """Un signal mesuré sur douze matchs s'était évaporé au dix-huitième."""
    assert courbe(observations(n=MIN_OBS_MARCHE - 1)) is None


def test_retrecissement_bride_les_petits_echantillons():
    """Peu d'observations : la correction reste proche de « ne rien faire »."""
    petit = courbe(observations(n=MIN_OBS_MARCHE + 50, biais=0.25))
    grand = courbe(observations(n=6000, biais=0.25))
    assert petit is not None and grand is not None

    def correction_moyenne(c):
        m = [(x, y) for x, y in c if 0.3 <= x <= 0.7]
        return sum(y - x for x, y in m) / len(m)

    assert correction_moyenne(petit) < correction_moyenne(grand)


def test_coupure_alignee_sur_une_date():
    """Deux options d'un même jour ne peuvent pas tomber de part et d'autre."""
    obs = observations(n=1000)
    i = _coupure_chronologique(obs)
    assert 0 < i < len(obs)
    assert obs[i - 1].quand[:10] != obs[i].quand[:10]


def test_candidate_retenue_quand_elle_gagne():
    rapport = evaluer_candidate(observations(n=4000))
    assert rapport['retenue'] is True
    assert rapport['brier_candidate'] < rapport['brier_sans_correction'] - GAIN_MINIMAL
    assert 'tables' in rapport


def test_candidate_refusee_sur_un_marche_deja_juste():
    """Aucun biais à corriger : la version en place doit être conservée."""
    rapport = evaluer_candidate(observations(n=4000, biais=0.0, marche='+1.5'))
    assert rapport['retenue'] is False
    assert 'tables' not in rapport
    assert rapport['raison']


def test_candidate_refusee_sans_volume():
    rapport = evaluer_candidate(observations(n=200))
    assert rapport['retenue'] is False
    assert 'volume insuffisant' in rapport['raison']


def test_apprendre_produit_une_courbe_par_ligue_si_le_volume_suit():
    obs = observations(n=4000, ligue='UCL')
    tables = apprendre(obs)
    assert '+2.5' in tables
    assert 'UCL|+2.5' in tables


def test_apprendre_ignore_une_ligue_trop_maigre():
    obs = observations(n=2000, ligue='PL') + observations(
        n=300, ligue='DFB', graine=11,
    )
    obs.sort(key=lambda ob: ob.quand)
    tables = apprendre(obs)
    assert 'DFB|+2.5' not in tables


def test_complement_deduit_et_coherent():
    """« Moins de 4 buts » se déduit de « plus de 3,5 » : somme garantie à 1."""
    tables = apprendre(observations(n=4000, marche='+3.5'))
    direct = Observation('+3.5', False, 'PL', 0.40, 1, '2026-03-01T18:00:00+00:00')
    inverse = Observation('+3.5', True, 'PL', 0.60, 0, '2026-03-01T18:00:00+00:00')
    from engine.apprentissage import _applique
    assert _applique(direct, tables) + _applique(inverse, tables) == pytest.approx(1.0)


def test_brier_et_logloss_s_ameliorent_ensemble():
    obs = observations(n=4000)
    tables = apprendre(obs)
    assert brier(obs, tables) < brier(obs, {})
    assert log_loss(obs, tables) < log_loss(obs, {})


def test_bilan_distingue_le_bruit_du_signal():
    fort = bilan_par_marche(observations(n=4000, biais=0.10))[0]
    assert fort['significatif'] is True
    assert fort['ecart_points'] > 5

    faible = bilan_par_marche(observations(n=300, biais=0.0, graine=3))[0]
    assert faible['significatif'] is False


# --------------------------------------------------------------------------
# Arbitrage par marché : savoir désapprendre
# --------------------------------------------------------------------------

def test_courbe_nuisible_est_neutralisee():
    """Une table héritée que les données démentent doit pouvoir être annulée.

    Mesuré sur 6 305 matchs des cinq grands championnats : la table livrée
    avec la v3.1 dégradait 15 marchés sur 18. Sans neutralisation explicite,
    elle resterait active pour toujours, puisque ne rien publier revient à
    la laisser en place.
    """
    from engine.apprentissage import IDENTITE, selectionner_courbes

    # Marché déjà bien calibré : toute correction ne peut que nuire.
    validation = observations(n=2000, biais=0.0, marche='+2.5')
    # Table « en place » qui décale tout de 15 points : franchement mauvaise.
    actuelles = {'+2.5': [[0.0, 0.15], [0.5, 0.65], [1.0, 1.0]]}
    retenues = selectionner_courbes({}, validation, actuelles)
    assert retenues.get('+2.5') == IDENTITE


def test_courbe_utile_est_conservee():
    """À l'inverse, une courbe qui corrige un vrai biais doit être publiée."""
    from engine.apprentissage import IDENTITE, apprendre, selectionner_courbes

    apprentissage = observations(n=4000, biais=0.10, marche='+2.5', graine=1)
    validation = observations(n=2000, biais=0.10, marche='+2.5', graine=2)
    brutes = apprendre(apprentissage)
    retenues = selectionner_courbes(brutes, validation, {})
    assert '+2.5' in retenues
    assert retenues['+2.5'] != IDENTITE


def test_defaut_conserve_quand_il_gagne():
    """Si la table en place est la meilleure des trois, on ne publie rien."""
    from engine.apprentissage import apprendre, selectionner_courbes

    validation = observations(n=2000, biais=0.10, marche='+2.5', graine=3)
    bonne = apprendre(observations(n=4000, biais=0.10, marche='+2.5', graine=4))
    retenues = selectionner_courbes({}, validation, bonne)
    assert '+2.5' not in retenues


def test_arbitrage_exige_un_volume_minimal():
    """Sous le seuil, aucun arbitrage : on ne tranche pas sur trois matchs."""
    from engine.apprentissage import selectionner_courbes

    assert selectionner_courbes({}, observations(n=50), {'+2.5': [[0.0, 0.2], [1.0, 1.0]]}) == {}


# --------------------------------------------------------------------------
# Le critère d'arbitrage : l'erreur de calibration, pas le score de Brier
# --------------------------------------------------------------------------

def test_erreur_calibration_mesure_bien_des_points_de_pourcentage():
    """Un décalage de dix points doit se lire « dix », pas « 0,009 »."""
    from engine.apprentissage import erreur_calibration

    assert erreur_calibration(observations(n=4000, biais=0.10)) == pytest.approx(
        10.0, abs=1.5)
    assert erreur_calibration(observations(n=4000, biais=0.0)) < 1.5


def test_courbe_adoptee_quand_elle_recadre_sans_bouger_le_brier():
    """Le cas que l'ancien critère laissait passer.

    Un biais de deux points sur les pourcentages annoncés déplace le score
    de Brier de 0,0001 — sous le seuil de gain, donc invisible pour un
    arbitrage fondé sur le Brier. Pour l'utilisateur qui lit « 74 % », c'est
    pourtant la seule chose qui compte. Le critère doit trancher sur la
    calibration.
    """
    from engine.apprentissage import (
        GAIN_MINIMAL,
        _brier_marche,
        apprendre,
        erreur_calibration,
        selectionner_courbes,
    )

    apprentissage = observations(n=6000, biais=0.02, marche='+2.5', graine=61)
    validation = observations(n=3000, biais=0.02, marche='+2.5', graine=62)
    courbes = apprendre(apprentissage)

    gain_brier = (_brier_marche(validation, None)
                  - _brier_marche(validation, courbes['+2.5']))
    assert gain_brier < 5 * GAIN_MINIMAL  # le Brier ne voit presque rien

    avant = erreur_calibration(validation)
    apres = erreur_calibration(validation, courbes['+2.5'])
    assert avant - apres > 0.3  # la calibration, elle, gagne franchement

    assert '+2.5' in selectionner_courbes(courbes, validation, {})


def test_garde_fou_refuse_une_courbe_qui_casse_le_brier():
    """Aplatir tout sur la fréquence de base donne une calibration parfaite
    et une prévision sans valeur. Le garde-fou Brier existe pour cela."""
    from engine.apprentissage import (
        _brier_marche,
        erreur_calibration,
        selectionner_courbes,
    )

    validation = observations(n=6000, biais=0.02, marche='+2.5', graine=55)
    plate = [[0.0, 0.52], [1.0, 0.52]]

    assert erreur_calibration(validation, plate) < erreur_calibration(validation)
    assert _brier_marche(validation, plate) > _brier_marche(validation, None)
    assert selectionner_courbes({'+2.5': plate}, validation, {}) == {}
