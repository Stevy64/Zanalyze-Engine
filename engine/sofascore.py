"""Client SofaScore (API non officielle).

Préfère curl_cffi (empreinte Chrome). Timeout court pour ne pas geler un refresh Actions.
TOURNOIS : ids uniqueTournament → codes PWA (PL, LIGA, …).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

try:
    from curl_cffi import requests as _cffi_requests
except ImportError:  # pragma: no cover - environnement sans curl_cffi
    _cffi_requests = None

BASE = 'https://api.sofascore.com/api/v1'

# uniqueTournament id → code interne ZanalyZ
TOURNOIS = {
    7: {'code': 'UCL', 'nom': 'Ligue des champions', 'pays': 'Europe', 'ordre': 10},
    17: {'code': 'PL', 'nom': 'Premier League', 'pays': 'Angleterre', 'ordre': 20},
    8: {'code': 'LIGA', 'nom': 'LaLiga', 'pays': 'Espagne', 'ordre': 30},
    34: {'code': 'L1', 'nom': 'Ligue 1', 'pays': 'France', 'ordre': 40},
    23: {'code': 'SA', 'nom': 'Serie A', 'pays': 'Italie', 'ordre': 50},
}


class SofaScoreErreur(RuntimeError):
    pass


def _get(path: str, *, timeout: float = 15) -> dict[str, Any]:
    """GET JSON SofaScore. timeout court pour ne pas geler une sync entière."""
    url = BASE + path if path.startswith('/') else path
    if _cffi_requests is not None:
        r = _cffi_requests.get(url, impersonate='chrome124', timeout=timeout)
        if r.status_code != 200:
            raise SofaScoreErreur(f'SofaScore {r.status_code} sur {path}')
        return r.json()

    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, 'status', 200) != 200:
                raise SofaScoreErreur(f'SofaScore {resp.status} sur {path}')
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise SofaScoreErreur(f'SofaScore {exc.code} sur {path}') from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if '403' in reason:
            raise SofaScoreErreur(
                'Accès SofaScore refusé. Lancer Zanalyze Engine hors '
                'PythonAnywhere (GitHub Actions, VPS Oracle, PC). Détail : '
                + reason
            ) from exc
        raise SofaScoreErreur(f'SofaScore indisponible : {exc.reason}') from exc


def saison_courante(tournament_id: int) -> int:
    data = _get(f'/unique-tournament/{tournament_id}/seasons')
    seasons = data.get('seasons') or []
    if not seasons:
        raise SofaScoreErreur(f'Aucune saison pour tournoi {tournament_id}')
    return int(seasons[0]['id'])


def evenements_suivants(tournament_id: int, pages: int = 2) -> list[dict]:
    sid = saison_courante(tournament_id)
    out: list[dict] = []
    for page in range(pages):
        data = _get(
            f'/unique-tournament/{tournament_id}/season/{sid}/events/next/{page}'
        )
        events = data.get('events') or []
        out.extend(events)
        if not data.get('hasNextPage'):
            break
    return out


def evenements_passes(tournament_id: int, pages: int = 1) -> list[dict]:
    sid = saison_courante(tournament_id)
    out: list[dict] = []
    for page in range(pages):
        data = _get(
            f'/unique-tournament/{tournament_id}/season/{sid}/events/last/{page}'
        )
        events = data.get('events') or []
        out.extend(events)
        if not data.get('hasNextPage'):
            break
    return out


def statut_depuis_code(status_code: int | None) -> str:
    """Mappe les codes SofaScore vers Match.STATUT.

    Réf. usuelles : 0 not started, 6/7 live, 60 postponed, 70 cancelled,
    90–100 finished (100 ended). Les reports/annulations → reporte.
    """
    if status_code is None:
        return 'a_venir'
    if status_code == 100 or status_code in (90, 91, 92):
        return 'termine'
    if status_code in (60, 70, 80):  # postponed / cancelled / abandoned
        return 'reporte'
    if status_code in (6, 7, 31, 32, 41, 42, 45):  # live / HT / etc.
        return 'en_cours'
    if status_code == 0:
        return 'a_venir'
    return 'a_venir'


def scores_depuis_event(ev: dict) -> tuple[int | None, int | None, int | None, int | None]:
    """(buts_dom, buts_ext, buts_dom_mt, buts_ext_mt) depuis un event SofaScore."""
    hs = ev.get('homeScore') or {}
    aws = ev.get('awayScore') or {}
    ft_h = hs.get('current')
    ft_a = aws.get('current')
    mt_h = hs.get('period1')
    mt_a = aws.get('period1')
    return (
        int(ft_h) if ft_h is not None else None,
        int(ft_a) if ft_a is not None else None,
        int(mt_h) if mt_h is not None else None,
        int(mt_a) if mt_a is not None else None,
    )


def h2h(event_id: int) -> dict[str, Any]:
    try:
        return _get(f'/event/{event_id}/h2h')
    except SofaScoreErreur:
        return {}


def formater_h2h(data: dict[str, Any], nom_dom: str, nom_ext: str) -> str:
    """Résumé texte des confrontations (duel équipes) pour Contexte."""
    if not data:
        return ''
    duel = data.get('teamDuel') or {}
    hw = duel.get('homeWins')
    aw = duel.get('awayWins')
    dr = duel.get('draws')
    if hw is None and aw is None and dr is None:
        return ''
    hw = int(hw or 0)
    aw = int(aw or 0)
    dr = int(dr or 0)
    total = hw + aw + dr
    if total <= 0:
        return ''
    if hw > aw + dr:
        lecture = f'{nom_dom} a dominé ce duel récemment'
    elif aw > hw + dr:
        lecture = f'{nom_ext} a souvent pris le dessus'
    elif dr >= max(hw, aw):
        lecture = 'beaucoup de matchs serrés / nuls dans l’historique'
    else:
        lecture = 'bilan assez équilibré entre les deux'
    return (
        f'Sur {total} confrontations récentes : {nom_dom} {hw} victoire(s), '
        f'{dr} nul(s), {nom_ext} {aw} — {lecture}.'
    )


def _lettre_forme_fr(c: str) -> str:
    return {'W': 'V', 'D': 'N', 'L': 'D'}.get(c, c)


def formater_forme_equipe(infos: dict[str, Any] | None, nom: str) -> str:
    """Texte forme récente à partir de infos_equipe()."""
    if not infos:
        return ''
    forme = infos.get('forme') or []
    if not forme:
        return ''
    suite = '–'.join(_lettre_forme_fr(c) for c in forme[:5])
    wins = sum(1 for c in forme[:5] if c == 'W')
    losses = sum(1 for c in forme[:5] if c == 'L')
    pos = infos.get('position')
    bits = [f'{nom} : {suite}']
    if wins >= 3:
        bits.append('bonne dynamique')
    elif losses >= 3:
        bits.append('série compliquée')
    elif wins == 0 and losses <= 1:
        bits.append('peu de résultats tranchés')
    if pos:
        bits.append(f'{pos}ᵉ au classement')
    return ' — '.join(bits) + '.'


def _raison_absent(reason: Any) -> str:
    # Codes SofaScore usuels : 1 blessé, 2 suspendu, 3 autre / douteux
    try:
        code = int(reason)
    except (TypeError, ValueError):
        return ''
    return {1: 'blessé', 2: 'suspendu', 3: 'indisponible'}.get(code, '')


def formater_absents_lineups(data: dict[str, Any], cote: str = 'home') -> str:
    """Liste courte des absents depuis /event/{id}/lineups."""
    if not data:
        return ''
    side = data.get(cote) or {}
    missing = side.get('missingPlayers') or []
    noms: list[str] = []
    for item in missing[:6]:
        player = item.get('player') or {}
        nom = (player.get('shortName') or player.get('name') or '').strip()
        if not nom:
            continue
        motif = _raison_absent(item.get('reason'))
        noms.append(f'{nom} ({motif})' if motif else nom)
    if not noms:
        return ''
    return ', '.join(noms)


def formater_meteo_event(ev: dict[str, Any] | None) -> str:
    """Météo si présente dans le JSON event SofaScore."""
    if not ev:
        return ''
    weather = ev.get('weather') or {}
    if not weather:
        return ''
    temp = weather.get('temperature')
    if temp is None:
        temp = weather.get('temp')
    cond = (
        weather.get('condition')
        or weather.get('skyCondition')
        or weather.get('description')
        or ''
    )
    cond = str(cond).strip().lower()
    cond_fr = {
        'clear': 'ciel dégagé',
        'sunny': 'ensoleillé',
        'cloudy': 'nuageux',
        'partly-cloudy': 'partiellement nuageux',
        'overcast': 'couvert',
        'rain': 'pluie',
        'light-rain': 'pluie fine',
        'heavy-rain': 'forte pluie',
        'storm': 'orage',
        'snow': 'neige',
        'fog': 'brouillard',
        'windy': 'venteux',
    }.get(cond.replace(' ', '-'), cond.replace('-', ' ') if cond else '')
    bits = []
    if temp is not None:
        try:
            bits.append(f'{int(round(float(temp)))} °C')
        except (TypeError, ValueError):
            pass
    if cond_fr:
        bits.append(cond_fr)
    wind = weather.get('wind') or weather.get('windSpeed')
    if wind is not None:
        try:
            bits.append(f'vent ~{int(round(float(wind)))} km/h')
        except (TypeError, ValueError):
            pass
    if not bits:
        return ''
    texte = 'Conditions annoncées : ' + ', '.join(bits) + '.'
    if any(k in (cond_fr or '') for k in ('pluie', 'orage', 'neige', 'brouillard')):
        texte += ' Terrain potentiellement plus fermé / moins de buts spectaculaires.'
    elif 'ensoleillé' in (cond_fr or '') or 'dégagé' in (cond_fr or ''):
        texte += ' Cadre classique pour un match joué à plat.'
    return texte


def collecter_contexte_match(
    event_id: int,
    *,
    home_team_id: int | None,
    away_team_id: int | None,
    nom_dom: str,
    nom_ext: str,
    event: dict[str, Any] | None = None,
    tournament_id: int | None = None,
) -> dict[str, str]:
    """Agrège H2H, formes, absents et météo pour Contexte (best-effort)."""
    out = {
        'confrontations': '',
        'forme_dom': '',
        'forme_ext': '',
        'absents_dom': '',
        'absents_ext': '',
        'tendance_buts': '',
        'a_savoir': '',
    }
    try:
        out['confrontations'] = formater_h2h(h2h(event_id), nom_dom, nom_ext)
    except SofaScoreErreur:
        pass

    if home_team_id:
        try:
            out['forme_dom'] = formater_forme_equipe(
                infos_equipe(int(home_team_id), tournament_id), nom_dom,
            )
        except SofaScoreErreur:
            pass
    if away_team_id:
        try:
            out['forme_ext'] = formater_forme_equipe(
                infos_equipe(int(away_team_id), tournament_id), nom_ext,
            )
        except SofaScoreErreur:
            pass

    try:
        lineups = _get(f'/event/{int(event_id)}/lineups')
        out['absents_dom'] = formater_absents_lineups(lineups, 'home')
        out['absents_ext'] = formater_absents_lineups(lineups, 'away')
    except SofaScoreErreur:
        pass

    meteo = formater_meteo_event(event)
    if meteo:
        out['a_savoir'] = meteo

    # Tendance buts légère à partir des formes (lecture terrain, pas de maths).
    fd = out['forme_dom']
    fe = out['forme_ext']
    if 'bonne dynamique' in fd and 'bonne dynamique' in fe:
        out['tendance_buts'] = (
            'Les deux arrivent en confiance : match souvent plus ouvert, occasions des deux côtés.'
        )
    elif 'série compliquée' in fd and 'série compliquée' in fe:
        out['tendance_buts'] = (
            'Deux équipes en galère : souvent plus prudent, moins de rythme offensif.'
        )
    elif 'bonne dynamique' in fd or 'bonne dynamique' in fe:
        out['tendance_buts'] = (
            'Une équipe arrive mieux lancée : elle peut imposer son rythme et forcer le jeu.'
        )

    return out


def cotes_1x2(event_id: int) -> tuple[float, float, float] | None:
    try:
        data = _get(f'/event/{event_id}/odds/1/all')
    except SofaScoreErreur:
        return None
    for market in data.get('markets') or []:
        if market.get('marketGroup') != '1X2' and market.get('marketName') not in (
            'Full time', '1X2', 'Match Winner',
        ):
            continue
        choices = {c.get('name'): c for c in (market.get('choices') or [])}
        # Soft names: 1 / X / 2 or Home / Draw / Away
        mapping = [
            ('1', 'Home', '1'),
            ('X', 'Draw', 'X'),
            ('2', 'Away', '2'),
        ]
        vals = []
        for keys in mapping:
            ch = None
            for k in keys:
                if k in choices:
                    ch = choices[k]
                    break
            if not ch:
                break
            dec = _fraction_to_decimal(ch.get('fractionalValue') or ch.get('initialFractionalValue'))
            if dec is None:
                break
            vals.append(dec)
        if len(vals) == 3:
            return vals[0], vals[1], vals[2]
    return None


def cotes_ou25(event_id: int) -> tuple[float, float] | None:
    """Cotes Over/Under 2.5 (marché Match goals, choiceGroup 2.5)."""
    try:
        data = _get(f'/event/{event_id}/odds/1/all')
    except SofaScoreErreur:
        return None
    for market in data.get('markets') or []:
        if market.get('marketName') not in ('Match goals', 'Goals Over/Under'):
            continue
        if str(market.get('choiceGroup') or '') != '2.5':
            continue
        choices = {c.get('name'): c for c in (market.get('choices') or [])}
        over = choices.get('Over')
        under = choices.get('Under')
        if not over or not under:
            continue
        o = _fraction_to_decimal(
            over.get('fractionalValue') or over.get('initialFractionalValue')
        )
        u = _fraction_to_decimal(
            under.get('fractionalValue') or under.get('initialFractionalValue')
        )
        if o is not None and u is not None:
            return o, u
    return None


def _fraction_to_decimal(frac: str | None) -> float | None:
    if not frac or '/' not in frac:
        return None
    try:
        a, b = frac.split('/', 1)
        return 1.0 + (float(a) / float(b))
    except (ValueError, ZeroDivisionError):
        return None


def ts_to_aware(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def nom_court(nom: str, fallback: str = '') -> str:
    n = (nom or fallback or '?').strip()
    # enlever FC / CF / AS courants
    for suffix in (' FC', ' CF', ' AFC', ' SC'):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    parts = n.split()
    if len(n) <= 12:
        return n
    if len(parts) >= 2:
        return parts[-1][:12]
    return n[:12]


def slugify_nom(nom: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize('NFKD', nom).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s).strip('-').lower()
    return s[:48] or 'equipe'


def _http_get_bytes(url: str) -> tuple[bytes, str]:
    """GET binaire (logos) via curl_cffi ou urllib."""
    if _cffi_requests is not None:
        r = _cffi_requests.get(url, impersonate='chrome124', timeout=15)
        if r.status_code != 200 or not r.content:
            raise SofaScoreErreur(f'HTTP {r.status_code} sur {url}')
        ctype = (r.headers.get('content-type') or 'image/png').split(';')[0].strip()
        return r.content, ctype

    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            if not body:
                raise SofaScoreErreur(f'Logo vide sur {url}')
            ctype = resp.headers.get_content_type() if hasattr(resp.headers, 'get_content_type') else (
                (resp.headers.get('Content-Type') or 'image/png').split(';')[0].strip()
            )
            return body, ctype
    except urllib.error.HTTPError as exc:
        raise SofaScoreErreur(f'HTTP {exc.code} sur {url}') from exc
    except urllib.error.URLError as exc:
        raise SofaScoreErreur(f'Réseau : {exc.reason}') from exc


def logo_bytes(team_id: int) -> tuple[bytes, str]:
    tid = int(team_id)
    urls = [
        f'https://img.sofascore.com/api/v1/team/{tid}/image',
        f'{BASE}/team/{tid}/image',
    ]
    last_err = None
    for url in urls:
        try:
            return _http_get_bytes(url)
        except SofaScoreErreur as e:
            last_err = str(e)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    raise SofaScoreErreur(f'Logo introuvable ({last_err})')


def _norm_nom(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.casefold().strip()


_PENALITE_NOM = (
    ' legends', ' legend', ' u19', ' u18', ' u20', ' u21', ' u23',
    ' ii', ' b ', ' cyber', ' esports', ' e-sports', ' basket',
    ' handball', ' volleyball', ' futsal',
)


def chercher_equipe_id(nom: str) -> int | None:
    """Retrouve un team id SofaScore (football senior masculin de préférence)."""
    from urllib.parse import quote

    q = (nom or '').strip()
    if not q:
        return None
    # alias FR / surnoms → requête SofaScore plus fiable (clés déjà normalisées)
    aliases = {
        'bayern munich': 'Bayern München',
        'fc barcelone': 'FC Barcelona',
        'barca': 'FC Barcelona',
        'come': 'Como',
        'como 1907': 'Como',
        'inter milan': 'Inter',
        'olympique de marseille': 'Marseille',
        'olympique lyonnais': 'Lyon',
    }
    q_search = aliases.get(_norm_nom(q), q)
    try:
        data = _get(f'/search/all?q={quote(q_search)}&page=0')
    except SofaScoreErreur:
        return None
    q_norm = _norm_nom(q)
    q_search_norm = _norm_nom(q_search)
    candidats: list[tuple[int, int]] = []
    for row in data.get('results') or []:
        if row.get('type') != 'team':
            continue
        ent = row.get('entity') or {}
        tid = ent.get('id')
        if not tid:
            continue
        sport = ((ent.get('sport') or {}).get('name') or '').casefold()
        if sport and sport != 'football':
            continue
        gender = (ent.get('gender') or 'M').upper()
        if gender and gender != 'M':
            continue
        if ent.get('national'):
            continue
        name = ent.get('name') or ''
        short = ent.get('shortName') or ''
        name_n = _norm_nom(name)
        short_n = _norm_nom(short)
        score = 0
        if name_n in (q_norm, q_search_norm) or short_n in (q_norm, q_search_norm):
            score = 100
        elif q_search_norm in name_n or q_norm in name_n:
            score = 85
        elif name_n in q_search_norm or name_n in q_norm:
            score = 75
        elif q_search_norm in short_n or q_norm in short_n:
            score = 60
        else:
            score = 25
        # pénaliser réserves / jeunes / legends / homonymes
        blob = f' {name_n} {short_n} '
        for token in _PENALITE_NOM:
            if token in blob:
                score -= 50
                break
        if name_n.endswith(' ii') or name_n.endswith(' 2'):
            score -= 40
        candidats.append((score, int(tid)))
    if not candidats:
        return None
    candidats.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_id = candidats[0]
    if best_score < 30:
        return None
    return best_id


def logo_svg_placeholder(nom: str, nom_court: str = '') -> bytes:
    """SVG de repli (initiales) si le logo SofaScore est indisponible."""
    label = (nom_court or nom or '?').strip()
    parts = label.split()
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[1][0]).upper()
    else:
        initials = label[:2].upper() or '?'
    hue = sum(ord(c) for c in (nom or label)) % 360
    c1 = f'hsl({hue} 42% 38%)'
    c2 = f'hsl({(hue + 40) % 360} 48% 28%)'
    safe = (
        initials.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{c1}"/><stop offset="100%" stop-color="{c2}"/>
  </linearGradient></defs>
  <path fill="url(#g)" stroke="#fff" stroke-width="2.5"
    d="M32 4 52 12v18c0 14-8 24-20 28C20 54 12 44 12 30V12Z"/>
  <text x="32" y="38" text-anchor="middle" fill="#fff" font-size="18"
    font-family="Segoe UI,Arial,sans-serif" font-weight="700">{safe}</text>
</svg>'''
    return svg.encode('utf-8')


def infos_equipe(team_id: int, tournament_id: int | None = None) -> dict[str, Any]:
    """Forme récente, classement et derniers matchs."""
    team_id = int(team_id)
    data = _get(f'/team/{team_id}')
    team = data.get('team') or {}
    form = data.get('pregameForm') or {}

    tid = tournament_id
    if not tid:
        put = team.get('primaryUniqueTournament') or {}
        tid = put.get('id')

    classement = None
    if tid:
        try:
            sid = saison_courante(int(tid))
            st = _get(f'/unique-tournament/{int(tid)}/season/{sid}/standings/total')
            for block in st.get('standings') or []:
                for row in block.get('rows') or []:
                    if (row.get('team') or {}).get('id') == team_id:
                        classement = {
                            'position': row.get('position'),
                            'points': row.get('points'),
                            'joues': row.get('matches'),
                            'gagnes': row.get('wins'),
                            'nuls': row.get('draws'),
                            'perdus': row.get('losses'),
                            'buts_pour': row.get('scoresFor'),
                            'buts_contre': row.get('scoresAgainst'),
                            'competition': (block.get('tournament') or {}).get('name')
                                or (team.get('primaryUniqueTournament') or {}).get('name'),
                        }
                        break
                if classement:
                    break
        except SofaScoreErreur:
            classement = None

    recents = []
    try:
        last = _get(f'/team/{team_id}/events/last/0')
        for ev in (last.get('events') or [])[:8]:
            home = ev.get('homeTeam') or {}
            away = ev.get('awayTeam') or {}
            hs = (ev.get('homeScore') or {}).get('current')
            aw = (ev.get('awayScore') or {}).get('current')
            if hs is None or aw is None:
                continue
            is_home = home.get('id') == team_id
            if is_home:
                if hs > aw:
                    res = 'W'
                elif hs < aw:
                    res = 'L'
                else:
                    res = 'D'
            else:
                if aw > hs:
                    res = 'W'
                elif aw < hs:
                    res = 'L'
                else:
                    res = 'D'
            recents.append({
                'adversaire': (away if is_home else home).get('shortName')
                    or (away if is_home else home).get('name'),
                'score': f'{hs}-{aw}',
                'domicile': is_home,
                'resultat': res,
                'coup_denvoi': ts_to_aware(ev['startTimestamp']).isoformat()
                    if ev.get('startTimestamp') else None,
            })
    except SofaScoreErreur:
        pass

    colors = team.get('teamColors') or {}
    raw_form = form.get('form') or ''
    if isinstance(raw_form, str):
        forme = [c for c in raw_form if c in 'WDL']
    elif isinstance(raw_form, list):
        forme = [str(c) for c in raw_form if str(c) in 'WDL']
    else:
        forme = []

    if not forme and recents:
        forme = [m['resultat'] for m in reversed(recents[:5])]

    return {
        'id': team_id,
        'nom': team.get('name'),
        'nom_court': team.get('shortName') or team.get('nameCode'),
        'pays': (team.get('country') or {}).get('name'),
        'couleurs': {
            'primary': colors.get('primary'),
            'secondary': colors.get('secondary'),
        },
        'forme': forme,
        'position': form.get('position') or (classement or {}).get('position'),
        'note_moyenne': form.get('avgRating'),
        'classement': classement,
        'recents': recents,
    }
