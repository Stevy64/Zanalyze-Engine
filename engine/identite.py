"""
Identité canonique des équipes et des matchs.

Raison d'être
-------------
Avant la v4, l'identifiant d'une équipe était stocké dans une seule colonne
`sofascore_id` **partagée par les deux fournisseurs**, avec une contrainte
d'unicité. Les espaces d'identifiants ESPN et SofaScore se recouvrent : l'id
ESPN de Le Mans valait l'id SofaScore de l'Inter, si bien que l'Inter héritait
du slug `le-mans`. D'où des rencontres absurdes (« Real Madrid – Le Mans » en
Ligue des champions) et des doublons de matchs.

Ce module donne à chaque club **une clé canonique indépendante du fournisseur**,
dérivée de son nom, et à chaque rencontre une **clé de match** stable. Deux
fournisseurs qui décrivent la même rencontre écrivent donc dans la même ligne.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime

# Jetons purement décoratifs : présents chez un fournisseur, absents chez l'autre.
# « real », « inter », « athletic »… n'y figurent jamais : ils portent le sens.
BRUIT = frozenset({
    'fc', 'cf', 'afc', 'sc', 'ac', 'as', 'ss', 'ssc', 'us', 'ud', 'sd', 'cd',
    'ca', 'sv', 'tsv', 'tsg', 'vfb', 'vfl', 'bsc', 'fsv', 'sk', 'kv', 'rc',
    'rcd', 'ogc', 'losc', 'aj', 'ea', 'sco', 'fk', 'nk', 'hnk', 'bk', 'if',
    'il', 'gf', 'cfr', 'kaa', 'krc', 'rsc', 'spvgg', 'club', 'calcio',
})
# Les articles ne sont PAS du bruit : « Le Mans » n'est pas « Mans ».

# Variantes linguistiques d'un même lieu ou club.
TRADUCTION = {
    'munchen': 'munich', 'muenchen': 'munich', 'monaco-di-baviera': 'munich',
    'praha': 'prague', 'wien': 'vienna', 'milano': 'milan', 'torino': 'turin',
    'roma': 'rome', 'napoli': 'naples', 'firenze': 'florence',
    'koln': 'cologne', 'koeln': 'cologne', 'genova': 'genoa',
    'lisboa': 'lisbon', 'sevilla': 'seville', 'moskva': 'moscow',
    'eindhoven': 'eindhoven', 'bruxelles': 'brussels', 'brugge': 'bruges',
    'antwerpen': 'antwerp', 'gent': 'ghent', 'donetsk': 'donetsk',
    'zurich': 'zurich', 'bucuresti': 'bucharest', 'istanbul': 'istanbul',
    'athens': 'athens', 'athinai': 'athens', 'krakow': 'krakow',
    'beograd': 'belgrade', 'kobenhavn': 'copenhagen', 'goteborg': 'gothenburg',
}

# Clubs dont les deux fournisseurs ne partagent aucun jeton commun.
# Clé canonique explicite : c'est la seule table à tenir à la main.
ALIAS = {
    'internazionale': 'inter',
    'inter-milan': 'inter',
    'inter-milano': 'inter',
    'fc-internazionale-milano': 'inter',
    'spurs': 'tottenham',
    'tottenham-hotspur': 'tottenham',
    'wolves': 'wolverhampton',
    'wolverhampton-wanderers': 'wolverhampton',
    'man-utd': 'manchester-united',
    'man-united': 'manchester-united',
    'man-city': 'manchester-city',
    'psg': 'paris-saint-germain',
    'paris-sg': 'paris-saint-germain',
    'atletico': 'atletico-madrid',
    'atleti': 'atletico-madrid',
    'athletic-club': 'athletic-bilbao',
    'betis': 'real-betis',
    'gladbach': 'borussia-monchengladbach',
    'monchengladbach': 'borussia-monchengladbach',
    'dortmund': 'borussia-dortmund',
    'bayern': 'bayern-munich',
    'bayern-munchen': 'bayern-munich',
    'leverkusen': 'bayer-leverkusen',
    'leipzig': 'rb-leipzig',
    'salzburg': 'red-bull-salzburg',
    'psv': 'psv-eindhoven',
    'ajax': 'ajax-amsterdam',
    'sporting': 'sporting-cp',
    'sporting-lisbon': 'sporting-cp',
    'porto': 'porto',
    'benfica': 'benfica',
    'olympiacos': 'olympiakos',
    'zenit': 'zenit-saint-petersburg',
    'shakhtar': 'shakhtar-donetsk',
    'dynamo-kiev': 'dynamo-kyiv',
    'bodoglimt': 'bodo-glimt',
    'bodo': 'bodo-glimt',
    'feyenoord-rotterdam': 'feyenoord',
    'lask': 'lask-linz',
    'slavia': 'slavia-prague',
    'sparta': 'sparta-prague',
    'slovan': 'slovan-bratislava',
    'crvena-zvezda': 'red-star-belgrade',
    'st-etienne': 'saint-etienne',
    'nott-m-forest': 'nottingham-forest',
    'brighton-hove-albion': 'brighton',
    'west-ham-united': 'west-ham',
    'newcastle-united': 'newcastle',
    'leeds-united': 'leeds',
    'deportivo-alaves': 'alaves',
    'deportivo-alaves-sad': 'alaves',
    'rc-lens': 'lens',
    'racing-club-de-lens': 'lens',
    'rc-strasbourg': 'strasbourg',
    'rc-strasbourg-alsace': 'strasbourg',
    'stade-brestois': 'brest',
    'stade-brestois-29': 'brest',
    'racing-de-santander': 'racing-santander',
    'hull-city': 'hull',
}

_NETTOYAGE = re.compile(r'[^a-z0-9]+')
_NUM_PREFIXE = re.compile(r'^(\d+)\s*[.\-]?\s*')

# NFKD ne décompose ni ø ni æ ni ß : sans cette table, « Bodø/Glimt »
# deviendrait « bod-glimt » et ne s'apparierait plus à « Bodo/Glimt ».
_LETTRES = str.maketrans({
    'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'å': 'a', 'Å': 'a',
    'ß': 'ss', 'ð': 'd', 'Ð': 'd', 'þ': 'th', 'Þ': 'th',
    'đ': 'd', 'Đ': 'd', 'ł': 'l', 'Ł': 'l', 'ı': 'i', 'œ': 'oe', 'Œ': 'oe',
})


def _ascii(texte: str) -> str:
    pretraite = (texte or '').translate(_LETTRES)
    sans_accent = unicodedata.normalize('NFKD', pretraite)
    return sans_accent.encode('ascii', 'ignore').decode('ascii').lower()


def jetons(nom: str) -> tuple[str, ...]:
    """Jetons porteurs de sens d'un nom de club, fournisseur-indépendants.

    >>> jetons('FC Bayern München')
    ('bayern', 'munich')
    >>> jetons('Bayern Munich')
    ('bayern', 'munich')
    >>> jetons('1. FC Union Berlin')
    ('union', 'berlin')
    """
    brut = _NUM_PREFIXE.sub('', _ascii(nom))
    mots = [m for m in _NETTOYAGE.split(brut) if m]
    mots = [TRADUCTION.get(m, m) for m in mots]
    utiles = [m for m in mots if m not in BRUIT and not m.isdigit()]
    # Un nom entièrement fait de bruit (« FC ») garde ses mots d'origine.
    return tuple(utiles or mots)


def cle_equipe(nom: str) -> str:
    """Clé canonique d'un club. Même clé chez ESPN et chez SofaScore."""
    j = jetons(nom)
    brute = '-'.join(j)
    if brute in ALIAS:
        return ALIAS[brute]
    # Alias posés sur un nom complet non normalisé (« inter-milan »).
    plat = _NETTOYAGE.sub('-', _ascii(nom)).strip('-')
    if plat in ALIAS:
        return ALIAS[plat]
    return brute or 'equipe'


def _distinctif(j: tuple[str, ...]) -> frozenset[str]:
    """Jetons qui identifient vraiment le club (on retire les lieux banals)."""
    banals = {'united', 'city', 'town', 'rovers', 'athletic', 'sporting'}
    fort = frozenset(j) - banals
    return fort or frozenset(j)


def meme_club(nom_a: str, nom_b: str) -> bool:
    """Deux libellés désignent-ils le même club ?

    Après normalisation, un fournisseur ajoute souvent la ville
    (« Feyenoord » / « Feyenoord Rotterdam ») ou un préfixe
    (« Sk Slovan Bratislava » / « Slovan Bratislava »). L'inclusion des
    jetons tranche ces cas — **seulement à partir de 2 jetons**, sinon
    « Paris » ⊂ « Paris Saint-Germain » fusionnerait Paris FC et le PSG.
    Les cas à un jeton passent par `ALIAS` / `cle_equipe`.
    """
    ka, kb = cle_equipe(nom_a), cle_equipe(nom_b)
    if ka == kb:
        return True
    ja, jb = frozenset(jetons(nom_a)), frozenset(jetons(nom_b))
    if not ja or not jb:
        return False
    if ja == jb:
        return True
    if ja < jb and len(ja) >= 2:
        return True
    if jb < ja and len(jb) >= 2:
        return True
    # « bodoglimt » vs « bodo glimt » : comparer aussi la forme accolée.
    return ''.join(sorted(ja)) == ''.join(sorted(jb))


def apparier(nom: str, cles_connues: dict[str, tuple[str, ...]]) -> str | None:
    """Club déjà connu qui *pourrait* être le même, ou None.

    **Cette fonction ne sert qu'à signaler, jamais à fusionner.** Un nom
    inclus dans un autre ne prouve rien : « Arsenal » et « Arsenal Tula »
    sont deux clubs, « Sporting CP » et « Sporting Gijón » aussi. Fusionner
    sur cette base recréerait exactement la confusion d'identités que la v4
    corrige. Les rapprochements sûrs passent par `ALIAS`.

    `cles_connues` : clé canonique → jetons enregistrés.
    """
    cible = cle_equipe(nom)
    if cible in cles_connues:
        return cible
    j = frozenset(jetons(nom))
    if not j:
        return None
    candidats = []
    for cle, jt in cles_connues.items():
        s = frozenset(jt)
        if not s:
            continue
        if j <= s or s <= j:
            candidats.append((len(j & s), cle))
        elif ''.join(sorted(j)) == ''.join(sorted(s)):
            candidats.append((len(j), cle))
    if not candidats:
        return None
    # Le recouvrement le plus large gagne ; à égalité, ordre alphabétique stable.
    candidats.sort(key=lambda t: (-t[0], t[1]))
    meilleur = candidats[0][1]
    # Un seul jeton commun et rien de distinctif partagé : on refuse.
    if candidats[0][0] == 1:
        if not (_distinctif(jetons(nom)) & _distinctif(cles_connues[meilleur])):
            return None
    return meilleur


def nom_court(nom: str, maxi: int = 24) -> str:
    """Libellé compact pour l'interface (contrat PWA : 24 caractères)."""
    n = (nom or '?').strip()
    for suffixe in (' FC', ' CF', ' AFC', ' SC', ' FK', ' SK'):
        if n.endswith(suffixe):
            n = n[: -len(suffixe)]
    n = re.sub(r'^(FC|AC|AS|SS|SC|SV|VfB|VfL|RC|CD|UD|SD|NK|FK|SK)\s+', '', n)
    if len(n) <= maxi:
        return n
    mots = n.split()
    if len(mots) >= 2:
        court = mots[-1]
        if len(court) <= maxi:
            return court
    return n[:maxi]


def slug(cle: str) -> str:
    """Slug exporté vers la PWA (SlugField unique). Dérivé de la clé canonique."""
    s = _NETTOYAGE.sub('-', _ascii(cle)).strip('-')
    return (s or 'equipe')[:48]


def jour_utc(coup_denvoi: str | datetime) -> str:
    if isinstance(coup_denvoi, datetime):
        return coup_denvoi.date().isoformat()
    return str(coup_denvoi)[:10]


def cle_match(competition_code: str, coup_denvoi, cle_dom: str, cle_ext: str) -> str:
    """Clé canonique d'une rencontre, identique d'un fournisseur à l'autre.

    La **date** sert de repère, pas l'heure : deux fournisseurs diffèrent
    parfois de quelques minutes sur le coup d'envoi. Deux fois la même
    affiche le même jour dans la même compétition n'existe pas.
    """
    return f'{competition_code}:{jour_utc(coup_denvoi)}:{cle_dom}:{cle_ext}'


def id_export(cle: str) -> int:
    """Entier positif stable dérivé de la clé, pour le contrat snapshot v1.

    Utilisé seulement quand aucun identifiant de fournisseur n'est
    disponible. Reste bien sous 2^31 (PositiveIntegerField Django).
    """
    digest = hashlib.blake2b(cle.encode('utf-8'), digest_size=4).digest()
    return 1_000_000_000 + (int.from_bytes(digest, 'big') % 1_000_000_000)
