"""Moteur : lignes de totaux, calibration, sélection. Aucun accès réseau ni base."""
from collections import Counter, defaultdict

import numpy as np
import pytest

from engine.moteur import (
    BANDE_RECOMMANDEE,
    BANDES,
    CODE_FILET,
    FACTEUR_MI_TEMPS,
    AnalyseInvalide,
    ajuster,
    analyser,
    choisir_options,
    classer_journee,
    corriger,
    decomposer_ligne,
    devig_puissance,
    est_eligible,
    facteur_cache,
    forme_pari,
    matrice,
    moyennes_par_code,
    p_over_modele,
    profil_match,
    selections_niveaux,
    uniformite_excessive,
)

def cotes_coherentes(lh, la, ligne, marge=0.06):
    """Fabrique des cotes de marché cohérentes avec un couple de λ donné.

    Inventer des cotes à la main produit un 1X2 et des totaux qui se
    contredisent : le moteur le signale alors à juste titre par un résidu
    élevé. Mesuré sur des cotes réelles à ligne 2,5, le résidu médian vaut
    0,007 ; sur des cotes bricolées il dépasse 0,03. Les fixtures partent
    donc du modèle, puis y ajoutent une marge de bookmaker.
    """
    M = matrice(lh, la)
    n = M.shape[0]
    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    p1 = float(M[i > j].sum())
    pn = float(np.trace(M))
    p2 = float(M[i < j].sum())
    po = p_over_modele(M, ligne)
    k = 1 + marge
    trio = (round(1 / (p1 * k), 2), round(1 / (pn * k), 2), round(1 / (p2 * k), 2))
    totaux = (round(1 / (po * k), 2), round(1 / ((1 - po) * k), 2), ligne)
    return trio, totaux


# Journée variée : favoris nets, matchs serrés, lignes de totaux différentes.
JOURNEE = [
    cotes_coherentes(lh, la, ligne)
    for lh, la, ligne in [
        (2.30, 0.70, 3.5), (2.10, 0.75, 3.5), (1.95, 0.85, 3.0),
        (1.55, 1.10, 2.5), (1.40, 1.25, 2.5), (1.35, 1.30, 2.5),
        (1.15, 1.45, 2.5), (0.95, 1.70, 2.5), (0.80, 2.05, 3.0),
        (0.70, 2.25, 3.5), (1.25, 1.05, 2.0), (1.05, 1.35, 3.0),
    ]
]


def journee_analysee():
    return [
        analyser(c, t, f'D{i}', f'E{i}') for i, (c, t) in enumerate(JOURNEE)
    ]


def test_fixtures_coherentes():
    """Garde-fou : des cotes cohérentes doivent produire un résidu faible."""
    analyses = journee_analysee()
    residus = sorted(a['residu'] for a in analyses)
    mediane = residus[len(residus) // 2]
    assert mediane < 0.02, f'fixtures incohérentes, résidu médian {mediane:.4f}'
    assert sum(a['douteuse'] for a in analyses) <= 2


# --------------------------------------------------------------------------
# Bases
# --------------------------------------------------------------------------

def test_devig_somme_a_un():
    p = devig_puissance([1.80, 3.60, 4.50])
    assert abs(sum(p) - 1) < 1e-9
    assert all(0 < x < 1 for x in p)


def test_matrice_normalisee():
    assert abs(matrice(1.4, 1.1).sum() - 1) < 1e-9


def test_ajuster_retrouve_ordre_des_favoris():
    lh, la, residu = ajuster(0.55, 0.25, 0.20)
    assert lh > la
    assert residu < 0.05


def test_corriger_interpole_et_borne():
    """Interpolation testée sur une table explicite.

    Ne pas dépendre des courbes actives : elles changent avec ce que le
    moteur apprend, et un test qui en dépend finirait par mesurer l'ambiance
    plutôt que le code.
    """
    from engine.calibrage import corriger as corriger_table

    table = {'+1.5': [(0.0, 0.0), (0.5, 0.45), (1.0, 1.0)]}
    assert corriger_table(0.5, '+1.5', table) == pytest.approx(0.45)
    assert corriger_table(0.25, '+1.5', table) == pytest.approx(0.225)
    # Complément : 1 − courbe(1 − p), pour que la paire somme toujours à 1.
    assert (corriger_table(0.5, '+1.5', table)
            + corriger_table(0.5, ('~', '+1.5'), table)) == pytest.approx(1.0)
    assert corriger_table(0.4, 'cle_absente', table) == 0.4

    assert corriger(0.5, None) == 0.5
    assert corriger(0.5, 'cle_inconnue') == 0.5
    assert 0.005 <= corriger(0.30, ('~', '+2.5')) <= 0.995
    assert corriger(0.75, 'Total buts') == 0.75  # ancienne clé « famille » : sans effet


# --------------------------------------------------------------------------
# Lignes de totaux — la correction centrale de la v4
# --------------------------------------------------------------------------

def test_decomposer_ligne():
    assert decomposer_ligne(2.5) == [(2.5, 1.0)]
    assert decomposer_ligne(3.0) == [(3.0, 1.0)]
    assert decomposer_ligne(2.75) == [(2.5, 0.5), (3.0, 0.5)]
    assert decomposer_ligne(2.25) == [(2.0, 0.5), (2.5, 0.5)]


def test_ligne_entiere_neutralise_le_remboursement():
    """Sur une ligne entière, le prix ne porte que sur les issues non remboursées."""
    M = matrice(1.5, 1.2)
    brut = float(M[np.add.outer(np.arange(13), np.arange(13)) > 3].sum())
    corrige = p_over_modele(M, 3.0)
    assert corrige > brut  # le dénominateur exclut les cas « total = 3 »


def test_meme_prix_lignes_differentes_donnent_des_buts_differents():
    """Le bug de v3.1, mesuré : un prix sur 5,5 lu comme un prix sur 2,5.

    Bayern – Bodø/Glimt était coté « plus de 5,5 buts » à 2,20. Le moteur v3.1
    rangeait ce prix sous « plus de 2,5 buts » et en déduisait un match fermé.
    """
    cotes = (1.30, 6.00, 9.50)
    sur_5_5 = analyser(cotes, (2.20, 1.66, 5.5), 'Bayern', 'Bodo')
    sur_2_5 = analyser(cotes, (2.20, 1.66, 2.5), 'Bayern', 'Bodo')
    buts_5_5 = sur_5_5['buts_dom_attendus'] + sur_5_5['buts_ext_attendus']
    buts_2_5 = sur_2_5['buts_dom_attendus'] + sur_2_5['buts_ext_attendus']
    assert buts_5_5 > buts_2_5 + 2.0
    assert buts_5_5 > 4.5


def test_totaux_sans_ligne_refuses():
    """Une cote de totaux sans sa ligne est inexploitable : le moteur refuse."""
    with pytest.raises(AnalyseInvalide):
        analyser((1.90, 3.50, 4.00), (1.80, 2.05))
    with pytest.raises(AnalyseInvalide):
        analyser((1.90, 3.50, 4.00), (1.80, 2.05, 99))


def test_seul_le_seuil_cote_vient_du_marche():
    """Les autres seuils sont calculés puis recalibrés, jamais copiés."""
    a = analyser((1.90, 3.50, 4.00), (1.83, 1.95, 3.5), 'A', 'B')
    by = {o['code']: o for o in a['options']}
    assert by['OV_3.5']['origine'] == 'marche'
    assert by['UN_3.5']['origine'] == 'marche'
    assert by['OV_2.5']['origine'] == 'calcul'
    assert by['OV_3.5']['probabilite'] == pytest.approx(a['p_over'])


def test_1x2_jamais_corrige():
    a = analyser((1.90, 3.50, 4.00), (1.80, 2.05, 2.5), 'A', 'B')
    by = {o['code']: o for o in a['options']}
    assert by['1X2_1']['probabilite'] == pytest.approx(a['p1'])
    assert by['1X2_N']['probabilite'] == pytest.approx(a['pn'])
    assert by['1X2_2']['probabilite'] == pytest.approx(a['p2'])


def test_marche_incoherent_refuse():
    """1X2 de gros favori contre totaux très bas : contradiction insoluble."""
    with pytest.raises(AnalyseInvalide):
        analyser((1.02, 30.0, 60.0), (15.0, 1.02, 0.5), 'A', 'B')


def test_mi_temps_a_sa_propre_matrice():
    a = analyser((1.70, 3.80, 5.00), (1.75, 2.15, 2.5), 'A', 'B')
    by = {o['code']: o for o in a['options']}
    assert by['HT_OV_0.5']['probabilite'] != pytest.approx(
        FACTEUR_MI_TEMPS * by['OV_0.5']['probabilite'], abs=0.02
    )
    assert by['HT_OV_0.5']['famille'] == 'Mi-temps'


def test_coherence_complementaires():
    a = analyser((2.10, 3.40, 3.40), None, 'A', 'B')
    assert a['incoherence'] < 1e-9
    by = {o['code']: o for o in a['options']}
    for seuil in ('1.5', '3.5'):
        assert by[f'OV_{seuil}']['probabilite'] + by[f'UN_{seuil}']['probabilite'] == \
            pytest.approx(1.0)


def test_cotes_invalides_refusees():
    with pytest.raises(AnalyseInvalide):
        analyser((0.5, 3.0, 3.0))
    with pytest.raises(AnalyseInvalide):
        analyser((-1.0, 3.0, 3.0))


# --------------------------------------------------------------------------
# Options produites
# --------------------------------------------------------------------------

def test_handicaps_redondants_supprimes():
    """`HCP_H_-1` désignait le même événement que `MRG_H_2` : un seul subsiste."""
    a = analyser((1.45, 4.20, 7.50), (1.70, 2.20, 3.0), 'A', 'B')
    codes = {o['code'] for o in a['options']}
    assert 'HCP_H_-1' not in codes
    assert 'HCP_H_-2' not in codes
    assert 'HCP_A_-1' not in codes
    assert 'MRG_H_2' in codes
    assert 'HCP_H_+1' in codes  # celui-ci reste : il est bien distinct


def test_familles_eligibles_suivent_la_mesure():
    """L'éligibilité suit l'erreur de calibration mesurée, pas une habitude.

    Sur 212 746 options de trois saisons, « BTTS » mesure 1,42 point d'erreur
    et « Handicap » 2,94. Exclure le premier tout en gardant le second, comme
    le faisait la v3.1, n'était justifié par rien.
    """
    from engine.moteur import FIABILITE

    a = analyser((2.20, 3.30, 3.30), (1.85, 2.00, 2.5))
    par_famille = defaultdict(list)
    for o in a['options']:
        par_famille[o['famille']].append(o)

    assert all(est_eligible(o) for o in par_famille['BTTS'])
    assert FIABILITE['BTTS'] < FIABILITE['Handicap']
    # Les deux familles les moins bien calibrées restent écartées.
    for famille in ('Une équipe marque', "Total d'une équipe"):
        assert par_famille[famille]
        assert all(not est_eligible(o) for o in par_famille[famille])


def test_toute_famille_eligible_a_une_fiabilite_et_un_routage():
    """Une famille sans entrée prendrait une pénalité par défaut, en silence."""
    from engine.moteur import FAMILLES_ELIGIBLES, FIABILITE, ROUTAGE

    for famille in FAMILLES_ELIGIBLES:
        assert famille in FIABILITE, f'{famille} absente de FIABILITE'
        for profil, table in ROUTAGE.items():
            assert famille in table, f'{famille} absente du routage {profil}' 


def test_facteur_regroupe_les_paris_correles():
    assert facteur_cache({'famille': 'Total buts', 'code': 'OV_2.5'}) == 'buts'
    assert facteur_cache({'famille': 'Mi-temps', 'code': 'HT_OV_1.5'}) == 'buts'
    assert facteur_cache({'famille': 'Mi-temps', 'code': 'HT_1'}) == 'issue'
    assert facteur_cache({'famille': 'Double chance', 'code': 'DC_12'}) == 'nul'
    assert facteur_cache({'famille': 'Ecart de buts', 'code': 'MRG_H_2'}) == 'issue'


# --------------------------------------------------------------------------
# Sélection
# --------------------------------------------------------------------------

def test_trois_niveaux_familles_distinctes():
    a = analyser((2.05, 3.40, 3.60), (1.85, 2.00, 2.5), 'A', 'B')
    out = choisir_options(a['options'], a['profil'], moyennes_par_code([a['options']]),
                          analyse_ctx=a)
    niveaux = [o for o in out if o['niveau'] in BANDES]
    assert len(niveaux) == 3
    assert len({o['famille'] for o in niveaux}) == 3


def test_recommandee_dans_sa_bande_et_distincte():
    a = analyser((2.10, 3.40, 3.40), (1.90, 1.95, 2.5), 'A', 'B')
    out = choisir_options(a['options'], a['profil'], moyennes_par_code([a['options']]),
                          analyse_ctx=a)
    reco = [o for o in out if o['niveau'] == 'recommandee']
    assert len(reco) == 1
    r = reco[0]
    lo, hi = BANDE_RECOMMANDEE
    assert lo <= r['probabilite'] <= hi
    assert r['code'] != CODE_FILET
    assert r['confiance'] and 1 <= r['confiance'] <= 99
    assert r['explication']
    # Elle ne double aucun des trois niveaux.
    autres = {o['code'] for o in out if o['niveau'] in BANDES}
    assert r['code'] not in autres


def test_filet_present_et_hors_classement():
    a = analyser((2.10, 3.40, 3.40), (1.90, 1.95, 2.5), 'A', 'B')
    out = choisir_options(a['options'], a['profil'], moyennes_par_code([a['options']]),
                          analyse_ctx=a)
    filet = next(o for o in out if o['code'] == CODE_FILET)
    assert filet['niveau'] == 'filet'
    assert all(o['code'] != CODE_FILET for o in out if o['niveau'] in BANDES)


def test_plafond_de_repetition_sur_une_journee():
    """Onze fois le même pari en une journée : ce que la v4 doit rendre impossible."""
    analyses = journee_analysee()
    classer_journee(analyses)
    formes = Counter()
    for a in analyses:
        for o in a['options']:
            if o['niveau'] in BANDES:
                formes[o['forme']] += 1
    assert formes
    assert max(formes.values()) <= 6  # plancher 3, relevé à 15 % des 36 options


def test_recommandee_ne_se_repete_pas():
    """« Moins de 5 buts » sortait en Recommandée 58 % du temps en v3.1."""
    analyses = journee_analysee()
    classer_journee(analyses)
    recos = [
        o['code'] for a in analyses for o in a['options']
        if o['niveau'] == 'recommandee'
    ]
    assert len(recos) >= len(analyses) - 2
    dominant, n = Counter(recos).most_common(1)[0]
    assert n <= len(recos) * 0.5, f'{dominant} monopolise {n}/{len(recos)} Recommandées'


def test_exposition_correlée_bornee():
    """Une journée ne peut pas être entièrement adossée au niveau de buts."""
    analyses = journee_analysee()
    classer_journee(analyses)
    facteurs = Counter()
    for a in analyses:
        for o in a['options']:
            if o['niveau'] in BANDES:
                facteurs[o['facteur']] += 1
    total = sum(facteurs.values())
    assert facteurs['buts'] / total <= 0.75
    assert analyses[0]['exposition_lot']['formes_distinctes'] >= 6


def test_classement_journee_pas_uniforme():
    analyses = journee_analysee()
    classer_journee(analyses)
    sels = [selections_niveaux(a['options']) for a in analyses]
    assert uniformite_excessive(sels) is False
    for s in sels:
        assert len(s) == 3
        assert len(set(s.values())) == 3


def test_detecteur_uniformite_crie_sur_un_classement_casse():
    casse = [{'prudente': 'OV_1.5', 'equilibree': 'DC_1X', 'audacieuse': 'UN_2.5'}
             for _ in range(9)]
    assert uniformite_excessive(casse) is True


def test_profils():
    assert profil_match(0.62, 0.18) == 'desequilibre'
    assert profil_match(0.34, 0.33) == 'equilibre'
    assert profil_match(0.45, 0.30) == 'moyen'


def test_forme_pari_regroupe_les_equipes():
    assert forme_pari('MRG_H_2') == forme_pari('MRG_A_2')
    assert forme_pari('1X2_1') == forme_pari('1X2_2')
    assert forme_pari('OV_2.5') != forme_pari('OV_3.5')
