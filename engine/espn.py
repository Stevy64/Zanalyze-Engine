"""
Client ESPN (JSON public, sans clé API) — fournisseur par défaut.

Corrections v4 par rapport à v3.1
---------------------------------
* **La ligne de totaux est lue.** v3.1 rangeait tous les prix over/under sous
  « OU25 » sans consulter `overUnder`. Or DraftKings cote Bayern – Bodø sur
  5,5 buts et PSG – Slovan sur 4,5. Le moteur croyait lire « plus de 2,5 buts
  à 43 % » là où le marché disait « plus de 5,5 buts à 43 % ». Tous les
  marchés dérivés en héritaient.
* **L'ouverture et la clôture sont conservées** (`open` / `close` / `current`),
  ce qui rend le mouvement de cote observable.
* **Le score à la mi-temps est reconstruit** depuis les buts horodatés :
  `linescores` est vide en football chez ESPN, ce qui laissait toutes les
  options mi-temps éternellement « en attente ».
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.identite import cle_equipe, cle_match, nom_court

BASE_SITE = 'https://site.api.espn.com/apis/site/v2/sports/soccer'
BASE_CORE = 'https://sports.core.api.espn.com/v2/sports/soccer'

# slug ESPN → meta alignée sur les codes de la PWA
TOURNOIS: dict[str, dict[str, Any]] = {
    'uefa.champions': {'code': 'UCL', 'nom': 'Ligue des champions', 'pays': 'Europe',
                       'ordre': 10, 'espn_league_id': 775},
    'uefa.europa': {'code': 'UEL', 'nom': 'Ligue Europa', 'pays': 'Europe',
                    'ordre': 11, 'espn_league_id': 776},
    'eng.1': {'code': 'PL', 'nom': 'Premier League', 'pays': 'Angleterre',
              'ordre': 20, 'espn_league_id': 700},
    'eng.fa': {'code': 'FAC', 'nom': 'FA Cup', 'pays': 'Angleterre',
               'ordre': 21, 'espn_league_id': 3918},
    'eng.league_cup': {'code': 'EFL', 'nom': 'EFL Cup', 'pays': 'Angleterre',
                       'ordre': 22, 'espn_league_id': 3920},
    'esp.1': {'code': 'LIGA', 'nom': 'LaLiga', 'pays': 'Espagne',
              'ordre': 30, 'espn_league_id': 701},
    'esp.copa_del_rey': {'code': 'CDR', 'nom': 'Copa del Rey', 'pays': 'Espagne',
                         'ordre': 31, 'espn_league_id': 3951},
    'ger.1': {'code': 'BL', 'nom': 'Bundesliga', 'pays': 'Allemagne',
              'ordre': 35, 'espn_league_id': 720},
    'ger.dfb_pokal': {'code': 'DFB', 'nom': 'DFB-Pokal', 'pays': 'Allemagne',
                      'ordre': 36, 'espn_league_id': 3954},
    'fra.1': {'code': 'L1', 'nom': 'Ligue 1', 'pays': 'France',
              'ordre': 40, 'espn_league_id': 710},
    'fra.coupe_de_france': {'code': 'CDF', 'nom': 'Coupe de France', 'pays': 'France',
                            'ordre': 41, 'espn_league_id': 3952},
    'ita.1': {'code': 'SA', 'nom': 'Serie A', 'pays': 'Italie',
              'ordre': 50, 'espn_league_id': 702},
    'ita.coppa_italia': {'code': 'CI', 'nom': 'Coppa Italia', 'pays': 'Italie',
                         'ordre': 51, 'espn_league_id': 3956},
    'por.1': {'code': 'LP', 'nom': 'Liga Portugal', 'pays': 'Portugal',
              'ordre': 60, 'espn_league_id': 715},
    'por.taca.portugal': {'code': 'TDP', 'nom': 'Taça de Portugal', 'pays': 'Portugal',
                          'ordre': 61, 'espn_league_id': 20922},
}

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; ZanalyzeEngine/4.0; '
        '+https://github.com/Stevy64/Zanalyze-Engine)'
    ),
    'Accept': 'application/json',
}

_MINUTE = re.compile(r'(\d+)')


class EspnErreur(RuntimeError):
    pass


def _get(url: str, *, timeout: float = 25) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise EspnErreur(f'ESPN {exc.code} sur {url}') from exc
    except urllib.error.URLError as exc:
        raise EspnErreur(f'ESPN indisponible : {exc.reason}') from exc


def american_to_decimal(ml: float | int | None) -> float | None:
    """Cote américaine → décimale européenne."""
    if ml is None:
        return None
    try:
        v = float(ml)
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    if v > 0:
        return round(1.0 + v / 100.0, 3)
    return round(1.0 + 100.0 / abs(v), 3)


def _decimale(bloc: Any) -> float | None:
    """Prix décimal d'un bloc ESPN (`{'decimal': 1.83, 'american': '-120'}`)."""
    if bloc is None:
        return None
    if isinstance(bloc, (int, float)):
        return float(bloc)
    if isinstance(bloc, dict):
        for champ in ('decimal', 'value'):
            v = bloc.get(champ)
            if isinstance(v, (int, float)) and v >= 1.01:
                return float(v)
        am = bloc.get('american')
        if am is not None:
            try:
                return american_to_decimal(float(str(am).replace('+', '')))
            except (TypeError, ValueError):
                return None
    return None


def _ligne(bloc: Any, repli: Any = None) -> float | None:
    """Ligne de totaux (`total`), en nombre."""
    for source in (bloc, repli):
        if isinstance(source, dict):
            for champ in ('total', 'line', 'overUnder'):
                v = source.get(champ)
                if v is None:
                    continue
                try:
                    return float(str(v))
                except (TypeError, ValueError):
                    continue
        elif source is not None:
            try:
                return float(str(source))
            except (TypeError, ValueError):
                continue
    return None


def _statut_espn(status_type: dict | None) -> str:
    if not status_type:
        return 'a_venir'
    state = (status_type.get('state') or '').lower()
    name = (status_type.get('name') or '').lower()
    if 'postpon' in name or 'cancel' in name or 'abandon' in name or 'forfeit' in name:
        return 'reporte'
    if bool(status_type.get('completed')) or state == 'post' or 'final' in name:
        return 'termine'
    if state == 'in' or 'progress' in name or 'half' in name:
        return 'en_cours'
    return 'a_venir'


def _parse_iso(dt: str | None) -> datetime | None:
    if not dt:
        return None
    try:
        if dt.endswith('Z'):
            dt = dt[:-1] + '+00:00'
        return datetime.fromisoformat(dt).astimezone(timezone.utc)
    except ValueError:
        return None


def _dates(debut: datetime, fin: datetime) -> list[str]:
    """ESPN accepte une date AAAAMMJJ ; les plages renvoient souvent 400."""
    out: list[str] = []
    cur, end = debut.date(), fin.date()
    while cur <= end:
        out.append(cur.strftime('%Y%m%d'))
        cur += timedelta(days=1)
    return out


def evenements_fenetre(
    slug: str, *, jours_passes: int = 14, jours_futurs: int = 21,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    by_id: dict[str, dict] = {}
    for jour in _dates(now - timedelta(days=jours_passes), now + timedelta(days=jours_futurs)):
        try:
            data = _get(f'{BASE_SITE}/{slug}/scoreboard?dates={jour}')
        except EspnErreur:
            continue  # ligue sans calendrier ce jour-là
        for ev in data.get('events') or []:
            eid = str(ev.get('id') or '')
            if eid:
                by_id[eid] = ev
        time.sleep(0.2)
    return list(by_id.values())


def _equipes(comp: dict) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    home = away = None
    for c in comp.get('competitors') or []:
        team = c.get('team') or {}
        nom = (team.get('displayName') or team.get('name') or '').strip()
        if not nom:
            continue
        bloc = {
            'id': int(team['id']) if str(team.get('id') or '').isdigit() else None,
            'nom': nom[:80],
            'nom_court': (
                team.get('shortDisplayName') or team.get('abbreviation') or nom_court(nom)
            )[:24],
            'logo': (team.get('logo') or '').strip(),
            'score': c.get('score'),
        }
        if c.get('homeAway') == 'home':
            home = bloc
        elif c.get('homeAway') == 'away':
            away = bloc
    return home, away


def score_mi_temps(comp: dict, id_dom: int | None, id_ext: int | None):
    """Reconstruit le score à la pause depuis les buts horodatés.

    `linescores` est vide en football chez ESPN : sans cette reconstruction,
    les sept options de mi-temps d'un match restent « en attente » pour
    toujours et ne peuvent jamais être recalibrées.

    Un but contre son camp compte pour l'équipe adverse.
    """
    plays = comp.get('details') or comp.get('scoringPlays') or []
    if not plays or (id_dom is None and id_ext is None):
        return None, None
    dom = ext = 0
    vu = False
    for p in plays:
        if not (p.get('scoringPlay') or p.get('scoreValue')):
            continue
        type_info = p.get('type') or {}
        libelle = f"{type_info.get('text') or ''} {p.get('text') or ''}".lower()
        if 'penalty shootout' in libelle or 'shootout' in libelle:
            continue
        horloge = (p.get('clock') or {}).get('displayValue') or ''
        m = _MINUTE.search(str(horloge))
        if not m:
            continue
        minute = int(m.group(1))
        vu = True
        if minute > 45:
            continue
        equipe_id = (p.get('team') or {}).get('id')
        try:
            equipe_id = int(equipe_id)
        except (TypeError, ValueError):
            continue
        contre_son_camp = bool(p.get('ownGoal')) or 'own goal' in libelle
        marque_pour_dom = (equipe_id == id_dom) != contre_son_camp
        if marque_pour_dom:
            dom += 1
        else:
            ext += 1
    if not vu:
        return None, None
    return dom, ext


def _scores(comp: dict, home: dict, away: dict, statut: str):
    if statut != 'termine':
        return None, None, None, None
    bd = be = None
    for bloc, cible in ((home, 'bd'), (away, 'be')):
        val = bloc.get('score')
        if val is None or str(val).strip() == '':
            continue
        try:
            n = int(float(val))
        except (TypeError, ValueError):
            continue
        if cible == 'bd':
            bd = n
        else:
            be = n
    bdm, bem = score_mi_temps(comp, home.get('id'), away.get('id'))
    if bd is not None and bdm is not None and bdm > bd:
        bdm = bem = None
    if be is not None and bem is not None and bem > be:
        bdm = bem = None
    return bd, be, bdm, bem


def cotes_depuis_event(
    slug: str, event_id: str, competition_id: str | None = None,
) -> dict[str, Any]:
    """1X2 et totaux d'un match, avec **la ligne** et l'ouverture.

    Renvoie `{'1x2', 'totaux', 'ouverture_1x2', 'ouverture_totaux', 'book'}`.
    `totaux` vaut `(over, under, ligne)` ou None. Sans ligne exploitable, on
    ne renvoie **aucun** total : mieux vaut ajuster sur le seul 1X2 que sur
    un prix rattaché à la mauvaise ligne.
    """
    out: dict[str, Any] = {
        '1x2': None, 'totaux': None,
        'ouverture_1x2': None, 'ouverture_totaux': None, 'book': 'espn',
    }
    cid = competition_id or event_id
    try:
        idx = _get(f'{BASE_CORE}/leagues/{slug}/events/{event_id}/competitions/{cid}/odds')
    except EspnErreur:
        return out
    items = idx.get('items') or []
    if not items:
        return out
    ref = (items[0].get('$ref') or '').replace('http://', 'https://')
    if not ref:
        return out
    try:
        od = _get(ref)
    except EspnErreur:
        return out

    fournisseur = ((od.get('provider') or {}).get('name') or 'espn').lower()
    out['book'] = 'espn' if fournisseur in ('', 'espn') else fournisseur[:40]

    def trio(bloc: dict | None, racine: dict) -> tuple[float, float, float] | None:
        if isinstance(bloc, dict) and bloc:
            c1 = _decimale(bloc.get('homeOdds') or bloc.get('home'))
            c2 = _decimale(bloc.get('awayOdds') or bloc.get('away'))
            cn = _decimale(bloc.get('drawOdds') or bloc.get('draw'))
            if c1 and cn and c2:
                return (c1, cn, c2)
        c1 = american_to_decimal((racine.get('homeTeamOdds') or {}).get('moneyLine'))
        c2 = american_to_decimal((racine.get('awayTeamOdds') or {}).get('moneyLine'))
        cn = american_to_decimal((racine.get('drawOdds') or {}).get('moneyLine'))
        if c1 and cn and c2:
            return (c1, cn, c2)
        return None

    def totaux(bloc: dict | None, racine: dict):
        if isinstance(bloc, dict) and bloc:
            o = _decimale(bloc.get('over'))
            u = _decimale(bloc.get('under'))
            lg = _ligne(bloc, racine.get('overUnder'))
            if o and u and lg is not None:
                return (o, u, lg)
        o = _decimale(racine.get('overOdds'))
        u = _decimale(racine.get('underOdds'))
        if o is None:
            o = american_to_decimal(racine.get('overOdds'))
        if u is None:
            u = american_to_decimal(racine.get('underOdds'))
        lg = _ligne(racine.get('overUnder'))
        if o and u and lg is not None:
            return (o, u, lg)
        return None

    courant = od.get('current') or od.get('close') or {}
    ouverture = od.get('open') or {}
    out['1x2'] = trio(courant, od)
    out['totaux'] = totaux(courant, od)
    out['ouverture_1x2'] = trio(ouverture, {})
    out['ouverture_totaux'] = totaux(ouverture, {})
    return out


def normaliser_event(
    slug: str, meta: dict[str, Any], ev: dict[str, Any],
) -> dict[str, Any] | None:
    """Event ESPN → enregistrement canonique prêt pour la persistance."""
    eid = ev.get('id')
    if not eid:
        return None
    comp = (ev.get('competitions') or [{}])[0]
    statut = _statut_espn(
        (comp.get('status') or {}).get('type') or (ev.get('status') or {}).get('type')
    )
    home, away = _equipes(comp)
    if not home or not away:
        return None
    coup = _parse_iso(comp.get('date') or ev.get('date'))
    if coup is None:
        return None
    bd, be, bdm, bem = _scores(comp, home, away, statut)

    journee = ''
    for note in comp.get('notes') or []:
        if note.get('headline'):
            journee = str(note['headline'])[:40]
            break

    cle_dom, cle_ext = cle_equipe(home['nom']), cle_equipe(away['nom'])
    return {
        'provider': 'espn',
        'event_id': int(eid),
        'competition_id': str(comp.get('id') or eid),
        'league_slug': slug,
        'meta': meta,
        'cle': cle_match(meta['code'], coup, cle_dom, cle_ext),
        'coup_denvoi': coup,
        'journee': journee,
        'statut': statut,
        'home': home,
        'away': away,
        'buts_dom': bd,
        'buts_ext': be,
        'buts_dom_mt': bdm,
        'buts_ext_mt': bem,
        'cotes': None,
    }


def collecter_matchs(
    *, jours_passes: int = 14, jours_futurs: int = 21, avec_cotes: bool = True,
    tournois: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Liste canonique des rencontres, cotes comprises."""
    out: list[dict[str, Any]] = []
    for slug, meta in (tournois or TOURNOIS).items():
        try:
            events = evenements_fenetre(
                slug, jours_passes=jours_passes, jours_futurs=jours_futurs,
            )
        except EspnErreur:
            continue
        for ev in events:
            norm = normaliser_event(slug, meta, ev)
            if not norm:
                continue
            # Les cotes n'ont de sens que tant que le match n'a pas commencé.
            if avec_cotes and norm['statut'] == 'a_venir':
                try:
                    norm['cotes'] = cotes_depuis_event(
                        slug, str(norm['event_id']), norm.get('competition_id'),
                    )
                    time.sleep(0.25)
                except Exception:  # noqa: BLE001 — une cote absente n'arrête rien
                    norm['cotes'] = None
            out.append(norm)
        time.sleep(0.4)
    return out
