"""Identité canonique : c'est elle qui empêche le retour de « Real Madrid – Le Mans »."""
from engine.identite import (
    apparier,
    cle_equipe,
    cle_match,
    id_export,
    jetons,
    meme_club,
    nom_court,
    slug,
)


def test_meme_cle_pour_les_variantes_connues():
    """Variantes traitées par normalisation ou par la table d'alias."""
    for a, b in [
        ('Feyenoord', 'Feyenoord Rotterdam'),
        ('LASK', 'LASK Linz'),
        ('Slovan Bratislava', 'SK Slovan Bratislava'),
        ('Club Brugge', 'Club Brugge KV'),
        ('Slavia Praha', 'Slavia Prague'),
        ('Bodø/Glimt', 'Bodo/Glimt'),
        ('FC Bayern München', 'Bayern Munich'),
        ('Internazionale', 'Inter Milan'),
        ('RC Lens', 'Lens'),
        ('AS Roma', 'Roma'),
    ]:
        assert cle_equipe(a) == cle_equipe(b), f'{a} et {b} devraient partager une clé'
        assert meme_club(a, b)


def test_clubs_distincts_le_restent():
    """Le bug d'origine : deux clubs sans rapport fusionnés par leur identifiant."""
    for a, b in [
        ('Le Mans', 'Internazionale'),
        ('Manchester United', 'Manchester City'),
        ('Real Madrid', 'Real Betis'),
        ('Real Madrid', 'Real Sociedad'),
        ('Borussia Dortmund', 'Borussia Mönchengladbach'),
        ('Sporting CP', 'Sporting Gijón'),
        ('Paris FC', 'Paris Saint-Germain'),
        ('Paris', 'Paris Saint-Germain'),
    ]:
        assert not meme_club(a, b), f'{a} et {b} ne sont pas le même club'
        assert cle_equipe(a) != cle_equipe(b)


def test_article_conserve():
    """« Le Mans » n'est pas « Mans » : l'article porte le nom."""
    assert cle_equipe('Le Mans') == 'le-mans'
    assert cle_equipe('Le Mans FC') == 'le-mans'


def test_lettres_nordiques():
    """NFKD ne décompose pas ø : sans table dédiée, Bodø perdrait sa lettre."""
    assert cle_equipe('Bodø/Glimt') == cle_equipe('Bodo/Glimt')
    assert 'o' in jetons('Bodø/Glimt')[0]


def test_prefixe_numerique_ignore():
    assert jetons('1. FC Union Berlin') == ('union', 'berlin')


def test_apparier_signale_sans_confondre():
    """`apparier` ne sert qu'à signaler : « United » partagé ne suffit pas."""
    connues = {'manchester-united': ('manchester', 'united')}
    assert apparier('Leeds United', connues) is None
    assert apparier('Manchester United', connues) == 'manchester-united'


def test_clubs_homonymes_restent_distincts():
    """Même base de nom, clubs différents : aucune fusion silencieuse."""
    assert cle_equipe('Arsenal') != cle_equipe('Arsenal Tula')
    assert cle_equipe('Sporting Gijón') != cle_equipe('Sporting CP')


def test_cle_match_identique_entre_fournisseurs():
    """Même rencontre, heures légèrement différentes : une seule clé."""
    a = cle_match('UCL', '2026-09-08T19:00:00+00:00', 'real-madrid', 'inter')
    b = cle_match('UCL', '2026-09-08T19:05:00+00:00', 'real-madrid', 'inter')
    assert a == b
    # Le sens compte : recevoir n'est pas se déplacer.
    assert a != cle_match('UCL', '2026-09-08T19:00:00+00:00', 'inter', 'real-madrid')


def test_id_export_stable_et_dans_les_bornes():
    """Le contrat PWA exige un entier positif tenant dans un PositiveIntegerField."""
    cle = 'UCL:2026-09-08:real-madrid:inter'
    valeur = id_export(cle)
    assert valeur == id_export(cle)
    assert 0 < valeur < 2 ** 31


def test_slug_et_nom_court_bornes():
    assert len(slug('un-nom-de-club-particulierement-long-et-bavard-oui')) <= 48
    assert len(nom_court('Borussia Mönchengladbach Fußball-Club')) <= 24
    assert nom_court('Arsenal') == 'Arsenal'
