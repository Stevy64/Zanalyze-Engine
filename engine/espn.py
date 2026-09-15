"""
Client ESPN (JSON public, sans clé API).

Fonctionne depuis GitHub Actions.
Fournit calendrier, scores et cotes 1X2 / OU 2.5 (DraftKings via ESPN).

Les ids d’événements ESPN sont stockés dans la colonne `sofascore_id`
(contrat snapshot v1 — nom de champ legacy, ne pas renommer).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.sofascore import nom_court, slugify_nom

BASE_SITE = 'https://site.api.espn.com/apis/site/v2/sports/soccer'
BASE_CORE = 'https://sports.core.api.espn.com/v2/sports/soccer'

# slug ESPN → meta alignée PWA
TOURNOIS: dict[str, dict[str, Any]] = {
    'uefa.champions': {
        'code': 'UCL', 'nom': 'Ligue des champions', 'pays': 'Europe',
        'ordre': 10, 'espn_league_id': 775,
    },
    # Angleterre
    'eng.1': {
        'code': 'PL', 'nom': 'Premier League', 'pays': 'Angleterre',
        'ordre': 20, 'espn_league_id': 700,
    },
    'eng.fa': {
        'code': 'FAC', 'nom': 'FA Cup', 'pays': 'Angleterre',
        'ordre': 21, 'espn_league_id': 3918,
    },
    'eng.league_cup': {
        'code': 'EFL', 'nom': 'EFL Cup', 'pays': 'Angleterre',
        'ordre': 22, 'espn_league_id': 3920,
    },
    # Espagne
    'esp.1': {
        'code': 'LIGA', 'nom': 'LaLiga', 'pays': 'Espagne',
        'ordre': 30, 'espn_league_id': 701,
    },
    'esp.copa_del_rey': {
        'code': 'CDR', 'nom': 'Copa del Rey', 'pays': 'Espagne',
        'ordre': 31, 'espn_league_id': 3951,
    },
    # Allemagne
    'ger.1': {
        'code': 'BL', 'nom': 'Bundesliga', 'pays': 'Allemagne',
        'ordre': 35, 'espn_league_id': 720,
    },
    'ger.dfb_pokal': {
        'code': 'DFB', 'nom': 'DFB-Pokal', 'pays': 'Allemagne',
        'ordre': 36, 'espn_league_id': 3954,
    },
    # France
    'fra.1': {
        'code': 'L1', 'nom': 'Ligue 1', 'pays': 'France',
        'ordre': 40, 'espn_league_id': 710,
    },
    'fra.coupe_de_france': {
        'code': 'CDF', 'nom': 'Coupe de France', 'pays': 'France',
        'ordre': 41, 'espn_league_id': 3952,
    },
    # Italie
    'ita.1': {
        'code': 'SA', 'nom': 'Serie A', 'pays': 'Italie',
        'ordre': 50, 'espn_league_id': 702,
    },
    'ita.coppa_italia': {
        'code': 'CI', 'nom': 'Coppa Italia', 'pays': 'Italie',
        'ordre': 51, 'espn_league_id': 3956,
    },
    # Portugal
    'por.1': {
        'code': 'LP', 'nom': 'Liga Portugal', 'pays': 'Portugal',
        'ordre': 60, 'espn_league_id': 715,
    },
    'por.taca.portugal': {
        'code': 'TDP', 'nom': 'Taça de Portugal', 'pays': 'Portugal',
        'ordre': 61, 'espn_league_id': 20922,
    },
}

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; ZanalyzeEngine/1.0; '
        '+https://github.com/Stevy64/Zanalyze-Engine)'
    ),
    'Accept': 'application/json',
}


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


def _statut_espn(status_type: dict | None) -> str:
    if not status_type:
        return 'a_venir'
    state = (status_type.get('state') or '').lower()
    name = (status_type.get('name') or '').lower()
    completed = bool(status_type.get('completed'))
    if completed or state == 'post' or 'final' in name:
        return 'termine'
    if state == 'in' or 'progress' in name or 'half' in name:
        return 'en_cours'
    if 'postpon' in name or 'cancel' in name or 'abandon' in name:
        return 'reporte'
    return 'a_venir'


def _parse_iso(dt: str | None) -> datetime | None:
    if not dt:
        return None
    try:
        # 2026-09-20T13:00Z
        if dt.endswith('Z'):
            dt = dt[:-1] + '+00:00'
        return datetime.fromisoformat(dt).astimezone(timezone.utc)
    except ValueError:
        return None


def _chunk_dates(debut: datetime, fin: datetime, max_jours: int = 7) -> list[str]:
    """ESPN accepte souvent une plage YYYYMMDD-YYYYMMDD (≤ ~7–10 j)."""
    out: list[str] = []
    cur = debut.date()
    end = fin.date()
    while cur <= end:
        stop = min(cur + timedelta(days=max_jours - 1), end)
        out.append(f"{cur.strftime('%Y%m%d')}-{stop.strftime('%Y%m%d')}")
        cur = stop + timedelta(days=1)
    return out


def evenements_fenetre(
    slug: str,
    *,
    jours_passes: int = 14,
    jours_futurs: int = 21,
) -> list[dict[str, Any]]:
    """Tous les matchs d’une ligue dans la fenêtre (scoreboard ESPN)."""
    now = datetime.now(timezone.utc)
    debut = now - timedelta(days=jours_passes)
    fin = now + timedelta(days=jours_futurs)
    by_id: dict[str, dict] = {}
    for plage in _chunk_dates(debut, fin):
        url = f'{BASE_SITE}/{slug}/scoreboard?dates={plage}'
        data = _get(url)
        for ev in data.get('events') or []:
            eid = str(ev.get('id') or '')
            if eid:
                by_id[eid] = ev
        time.sleep(0.35)
    return list(by_id.values())


def _extract_teams(comp: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    home = away = {
        'id': None, 'nom': 'Équipe', 'nom_court': 'Équipe', 'slug': 'equipe', 'logo': '',
    }
    for c in comp.get('competitors') or []:
        team = c.get('team') or {}
        bloc = {
            'id': int(team['id']) if str(team.get('id') or '').isdigit() else None,
            'nom': (team.get('displayName') or team.get('name') or 'Équipe')[:80],
            'nom_court': (
                team.get('shortDisplayName')
                or team.get('abbreviation')
                or nom_court(team.get('displayName') or 'Équipe')
            )[:24],
            'slug': slugify_nom(team.get('slug') or team.get('displayName') or 'equipe'),
            'logo': (team.get('logo') or '').strip(),
            'score': c.get('score'),
        }
        if c.get('homeAway') == 'home':
            home = bloc
        elif c.get('homeAway') == 'away':
            away = bloc
    return home, away


def _scores(comp: dict, home: dict, away: dict, statut: str):
    bd = be = bdm = bem = None
    if statut != 'termine':
        return bd, be, bdm, bem
    try:
        if home.get('score') is not None and str(home['score']).strip() != '':
            bd = int(float(home['score']))
        if away.get('score') is not None and str(away['score']).strip() != '':
            be = int(float(away['score']))
    except (TypeError, ValueError):
        pass
    # Mi-temps parfois dans details / linescores
    for side, target in (('home', 'bdm'), ('away', 'bem')):
        pass
    for c in comp.get('competitors') or []:
        lines = c.get('linescores') or []
        if len(lines) >= 1:
            try:
                val = int(float(lines[0].get('value')))
            except (TypeError, ValueError, AttributeError):
                continue
            if c.get('homeAway') == 'home':
                bdm = val
            elif c.get('homeAway') == 'away':
                bem = val
    return bd, be, bdm, bem


def cotes_depuis_event(slug: str, event_id: str, competition_id: str | None = None) -> dict[str, Any]:
    """Récupère 1X2 + OU2.5 pour un match ESPN (best effort)."""
    out: dict[str, Any] = {'odds_1x2': None, 'odds_ou25': None}
    cid = competition_id or event_id
    idx_url = (
        f'{BASE_CORE}/leagues/{slug}/events/{event_id}/'
        f'competitions/{cid}/odds'
    )
    try:
        idx = _get(idx_url)
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

    h_ml = (od.get('homeTeamOdds') or {}).get('moneyLine')
    a_ml = (od.get('awayTeamOdds') or {}).get('moneyLine')
    d_ml = (od.get('drawOdds') or {}).get('moneyLine')
    c1 = american_to_decimal(h_ml)
    c2 = american_to_decimal(a_ml)
    cn = american_to_decimal(d_ml)
    if c1 and cn and c2 and min(c1, cn, c2) >= 1.01:
        out['odds_1x2'] = (c1, cn, c2)

    # OU 2.5 — décimales dans current.over / current.under
    current = od.get('current') or {}
    over = current.get('over') or {}
    under = current.get('under') or {}
    o_dec = over.get('decimal')
    u_dec = under.get('decimal')
    if o_dec is None:
        o_dec = american_to_decimal(od.get('overOdds'))
    if u_dec is None:
        u_dec = american_to_decimal(od.get('underOdds'))
    try:
        if o_dec is not None and u_dec is not None:
            o_f, u_f = float(o_dec), float(u_dec)
            if min(o_f, u_f) >= 1.01:
                out['odds_ou25'] = (round(o_f, 3), round(u_f, 3))
    except (TypeError, ValueError):
        pass
    return out


def normaliser_event(slug: str, meta: dict[str, Any], ev: dict[str, Any]) -> dict[str, Any] | None:
    """Transforme un event scoreboard ESPN en enregistrement pipeline."""
    eid = ev.get('id')
    if not eid:
        return None
    comp = (ev.get('competitions') or [{}])[0]
    status = _statut_espn((comp.get('status') or {}).get('type') or (ev.get('status') or {}).get('type'))
    home, away = _extract_teams(comp)
    coup = _parse_iso(comp.get('date') or ev.get('date'))
    if coup is None:
        return None
    bd, be, bdm, bem = _scores(comp, home, away, status)
    journee = ''
    for note in comp.get('notes') or []:
        if note.get('type') == 'event' and note.get('headline'):
            journee = str(note['headline'])[:40]
            break
    return {
        'provider': 'espn',
        'event_id': int(eid),
        'competition_id': str(comp.get('id') or eid),
        'league_slug': slug,
        'meta': meta,
        'coup_denvoi': coup,
        'journee': journee,
        'statut': status,
        'home': home,
        'away': away,
        'buts_dom': bd,
        'buts_ext': be,
        'buts_dom_mt': bdm,
        'buts_ext_mt': bem,
    }


def collecter_matchs(
    *,
    jours_passes: int = 14,
    jours_futurs: int = 21,
    avec_cotes: bool = True,
) -> list[dict[str, Any]]:
    """Liste normalisée + cotes pour toutes les ligues suivies."""
    out: list[dict[str, Any]] = []
    for slug, meta in TOURNOIS.items():
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
            if avec_cotes and norm['statut'] in ('a_venir', 'en_cours', 'termine'):
                try:
                    cotes = cotes_depuis_event(
                        slug, str(norm['event_id']), norm.get('competition_id'),
                    )
                    norm.update(cotes)
                    time.sleep(0.25)
                except Exception:  # noqa: BLE001
                    norm['odds_1x2'] = None
                    norm['odds_ou25'] = None
            else:
                norm.setdefault('odds_1x2', None)
                norm.setdefault('odds_ou25', None)
            out.append(norm)
        time.sleep(0.4)
    return out
