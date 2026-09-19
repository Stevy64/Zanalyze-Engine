"""Force des équipes : estimée sur les seuls résultats, mélangée sous condition."""
import random

import pytest

from engine.force import (
    MIN_MATCHS,
    Forces,
    ajuster_forces,
    ajuster_par_ligue,
    melanger,
    poids_optimal,
)


def championnat(n_equipes=14, n_journees=26, graine=3):
    """Saison simulée où la hiérarchie est connue d'avance.

    L'équipe 0 est la plus forte, la dernière la plus faible. Un modèle qui
    fonctionne doit retrouver cet ordre sans qu'on le lui dise.
    """
    rng = random.Random(graine)
    equipes = [f'club-{i:02d}' for i in range(n_equipes)]
    niveau = {e: 0.9 - 1.8 * i / (n_equipes - 1) for i, e in enumerate(equipes)}
    out = []
    jour = 0
    for _ in range(n_journees):
        ordre = equipes[:]
        rng.shuffle(ordre)
        jour += 7
        for k in range(0, n_equipes - 1, 2):
            dom, ext = ordre[k], ordre[k + 1]
            lh = 1.35 * pow(2.718, niveau[dom] - niveau[ext] + 0.20)
            la = 1.35 * pow(2.718, niveau[ext] - niveau[dom])
            out.append({
                'date': '2025-01-01', 'ligue': 'TEST', 'dom': dom, 'ext': ext,
                'bd': sum(rng.random() < lh / 6 for _ in range(6)),
                'be': sum(rng.random() < la / 6 for _ in range(6)),
                '_jour': jour,
            })
    # Dates réparties, pour que la pondération temporelle ait du sens.
    from datetime import date, timedelta
    base = date(2025, 1, 1)
    for r in out:
        r['date'] = (base + timedelta(days=r.pop('_jour'))).isoformat()
    return out, equipes


def test_retrouve_la_hierarchie():
    matchs, equipes = championnat()
    f = ajuster_forces(matchs, ligue='TEST')
    assert f is not None
    classement = [e for e, _ in f.classement(len(equipes))]
    # Le meilleur club doit sortir dans le premier tiers, le pire dans le dernier.
    assert classement.index(equipes[0]) < len(equipes) // 3
    assert classement.index(equipes[-1]) > 2 * len(equipes) // 3


def test_avantage_du_terrain_positif():
    matchs, _ = championnat()
    f = ajuster_forces(matchs, ligue='TEST')
    assert f.avantage_terrain > 0


def test_lambdas_ordonnes_et_bornes():
    matchs, equipes = championnat()
    f = ajuster_forces(matchs, ligue='TEST')
    fort, faible = equipes[0], equipes[-1]
    lh, la = f.lambdas(fort, faible)
    assert lh > la
    inverse = f.lambdas(faible, fort)
    assert inverse[1] > inverse[0]
    for v in (*f.lambdas(fort, faible), *inverse):
        assert 0.15 <= v <= 5.0


def test_refus_si_echantillon_trop_maigre():
    matchs, _ = championnat(n_journees=4)
    assert len(matchs) < MIN_MATCHS
    assert ajuster_forces(matchs, ligue='TEST') is None


def test_equipe_inconnue_ou_trop_peu_vue():
    """Mieux vaut ne rien dire que d'inventer la force d'un promu."""
    matchs, equipes = championnat()
    f = ajuster_forces(matchs, ligue='TEST')
    assert f.lambdas('club-inexistant', equipes[0]) is None
    assert not f.connait('club-inexistant')


def test_ajustement_par_ligue_separe_les_competitions():
    a, _ = championnat(graine=1)
    b, _ = championnat(graine=2)
    for r in b:
        r['ligue'] = 'AUTRE'
        r['dom'] = 'x-' + r['dom']
        r['ext'] = 'x-' + r['ext']
    forces = ajuster_par_ligue(a + b)
    assert set(forces) == {'TEST', 'AUTRE'}
    assert not (set(forces['TEST'].attaque) & set(forces['AUTRE'].attaque))


def test_aucune_cote_n_entre_dans_l_ajustement():
    """La condition d'indépendance : si une cote traîne, elle est ignorée."""
    matchs, _ = championnat()
    avec_cotes = [dict(r, c1=1.5, cn=4.0, c2=6.0, over=1.8, under=2.0) for r in matchs]
    a = ajuster_forces(matchs, ligue='TEST')
    b = ajuster_forces(avec_cotes, ligue='TEST')
    assert a.mu == pytest.approx(b.mu)
    assert a.attaque == pytest.approx(b.attaque)


# --------------------------------------------------------------------------
# Mélange avec le marché
# --------------------------------------------------------------------------

def test_melange_neutre_a_poids_nul():
    assert melanger((1.5, 1.1), (2.0, 0.8), 0.0) == (1.5, 1.1)
    assert melanger((1.5, 1.1), None, 0.5) == (1.5, 1.1)


def test_melange_geometrique():
    lh, la = melanger((1.0, 1.0), (4.0, 0.25), 0.5)
    assert lh == pytest.approx(2.0)
    assert la == pytest.approx(0.5)


def test_poids_reste_nul_quand_le_melange_n_apporte_rien():
    """Le portillon. Mesuré sur 4 247 matchs réels : le marché seul gagne,
    et le poids optimal vaut zéro. Le code doit conclure de même."""
    rng = random.Random(5)
    echantillon = []
    for _ in range(600):
        lam = (rng.uniform(0.8, 2.0), rng.uniform(0.6, 1.6))
        # Forces volontairement bruitées : elles n'ajoutent aucune information.
        bruit = (lam[0] * rng.uniform(0.6, 1.6), lam[1] * rng.uniform(0.6, 1.6))
        issue = rng.choices([0, 1, 2], weights=[0.43, 0.26, 0.31])[0]
        echantillon.append({'lam_marche': lam, 'lam_force': bruit, 'issue': issue})
    poids, detail = poids_optimal(echantillon)
    assert poids == 0.0
    assert detail['logloss_marche_seul'] > 0


def test_poids_nul_sans_volume():
    poids, detail = poids_optimal([{'lam_marche': (1.4, 1.1),
                                    'lam_force': (1.5, 1.0), 'issue': 0}] * 50)
    assert poids == 0.0


def test_forces_serialisables():
    """Les forces doivent pouvoir être stockées telles quelles."""
    matchs, _ = championnat()
    f = ajuster_forces(matchs, ligue='TEST')
    assert isinstance(f, Forces)
    assert f.arretee_le and f.n_matchs == len(matchs)
    assert all(isinstance(v, float) for v in f.attaque.values())
