"""
Archive durable des prédictions et des résultats.

Pourquoi un fichier plutôt que la base
--------------------------------------
La base SQLite du moteur vit dans le cache de GitHub Actions, qui peut être
évincé à tout moment. Une calibration apprise sur une base volatile
repartirait de zéro sans prévenir. L'archive, elle, est versionnée avec le
dépôt : c'est la mémoire longue du moteur.

Format : un CSV par mois, en ajout seul, trié, sans doublon. Lisible par un
humain, comparable d'un commit à l'autre, et assez compact pour git.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.evaluation import evaluer
from engine.paths import ARCHIVE_DIR, ensure_dirs

COLONNES = (
    'cle', 'coup_denvoi', 'competition', 'code', 'marche', 'complement',
    'p_brute', 'p_calibree', 'origine', 'resultat', 'version_moteur',
)
COLONNES_RESULTATS = (
    'cle', 'coup_denvoi', 'competition', 'domicile', 'exterieur',
    'buts_dom', 'buts_ext', 'buts_dom_mt', 'buts_ext_mt',
)


def _fichier_mois(prefixe: str, mois: str) -> Path:
    return ARCHIVE_DIR / f'{prefixe}-{mois}.csv'


def _lire(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open('r', encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def _ecrire(path: Path, colonnes: tuple[str, ...], lignes: list[dict[str, Any]]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(colonnes))
        w.writeheader()
        for ligne in lignes:
            w.writerow({c: ligne.get(c, '') for c in colonnes})


def exporter(conn: sqlite3.Connection) -> dict[str, int]:
    """Ajoute à l'archive les prédictions réglées qui n'y figurent pas encore.

    Idempotent : rejouer un export ne crée aucun doublon.
    """
    from engine.apprentissage import _cle_et_sens

    lignes = conn.execute(
        """SELECT p.cle, p.payload, p.calcule_le, p.version_moteur,
                  r.buts_dom, r.buts_ext, r.buts_dom_mt, r.buts_ext_mt,
                  r.competition_code, r.coup_denvoi
             FROM predictions p
             JOIN resultats r ON r.cle = p.cle
            WHERE p.avant_match = 1
            ORDER BY r.coup_denvoi"""
    ).fetchall()

    derniere: dict[str, sqlite3.Row] = {}
    for r in lignes:
        vue = derniere.get(r['cle'])
        if vue is None or r['calcule_le'] > vue['calcule_le']:
            derniere[r['cle']] = r

    par_mois: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in derniere.values():
        try:
            payload = json.loads(r['payload'])
        except json.JSONDecodeError:
            continue
        mois = (r['coup_denvoi'] or '')[:7]
        if len(mois) != 7:
            continue
        for o in payload.get('options') or []:
            code = o.get('code')
            if not code:
                continue
            sens = _cle_et_sens(code)
            try:
                gagne = evaluer(
                    code, r['buts_dom'], r['buts_ext'],
                    r['buts_dom_mt'], r['buts_ext_mt'],
                )
            except (ValueError, TypeError):
                continue
            if gagne is None:
                continue
            par_mois[mois].append({
                'cle': r['cle'],
                'coup_denvoi': r['coup_denvoi'],
                'competition': r['competition_code'] or '',
                'code': code,
                'marche': sens[0] if sens else '',
                'complement': '1' if (sens and sens[1]) else '0',
                'p_brute': round(float(o.get('p_brute', o.get('probabilite') or 0)), 6),
                'p_calibree': round(float(o.get('probabilite') or 0), 6),
                'origine': o.get('origine') or '',
                'resultat': 'gagne' if gagne else 'perdu',
                'version_moteur': r['version_moteur'] or '',
            })

    stats = {'mois': 0, 'ajoutees': 0, 'total': 0}
    for mois, nouvelles in sorted(par_mois.items()):
        path = _fichier_mois('observations', mois)
        existantes = _lire(path)
        vues = {(l['cle'], l['code']) for l in existantes}
        fusion = list(existantes)
        for l in nouvelles:
            if (l['cle'], l['code']) not in vues:
                fusion.append(l)
                vues.add((l['cle'], l['code']))
                stats['ajoutees'] += 1
        fusion.sort(key=lambda l: (str(l.get('coup_denvoi')), str(l.get('cle')),
                                   str(l.get('code'))))
        _ecrire(path, COLONNES, fusion)
        stats['mois'] += 1
        stats['total'] += len(fusion)
    return stats


def exporter_resultats(conn: sqlite3.Connection) -> dict[str, int]:
    """Archive les scores, socle de toute mesure ultérieure."""
    rows = conn.execute(
        """SELECT cle, coup_denvoi, competition_code, domicile_cle, exterieur_cle,
                  buts_dom, buts_ext, buts_dom_mt, buts_ext_mt
             FROM resultats ORDER BY coup_denvoi"""
    ).fetchall()
    par_mois: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        mois = (r['coup_denvoi'] or '')[:7]
        if len(mois) != 7:
            continue
        par_mois[mois].append({
            'cle': r['cle'], 'coup_denvoi': r['coup_denvoi'],
            'competition': r['competition_code'] or '',
            'domicile': r['domicile_cle'], 'exterieur': r['exterieur_cle'],
            'buts_dom': r['buts_dom'], 'buts_ext': r['buts_ext'],
            'buts_dom_mt': '' if r['buts_dom_mt'] is None else r['buts_dom_mt'],
            'buts_ext_mt': '' if r['buts_ext_mt'] is None else r['buts_ext_mt'],
        })
    stats = {'mois': 0, 'total': 0}
    for mois, nouvelles in sorted(par_mois.items()):
        path = _fichier_mois('resultats', mois)
        existantes = _lire(path)
        par_cle = {l['cle']: l for l in existantes}
        for l in nouvelles:
            par_cle[l['cle']] = l
        fusion = sorted(par_cle.values(), key=lambda l: (str(l['coup_denvoi']), str(l['cle'])))
        _ecrire(path, COLONNES_RESULTATS, fusion)
        stats['mois'] += 1
        stats['total'] += len(fusion)
    return stats


def fichiers(prefixe: str = 'observations') -> list[Path]:
    if not ARCHIVE_DIR.is_dir():
        return []
    return sorted(ARCHIVE_DIR.glob(f'{prefixe}-*.csv'))


def charger_observations(origine: str = 'calcul'):
    """Relit l'archive et reconstruit les observations d'apprentissage.

    Permet de recalibrer sur des années d'historique même si la base du
    moment ne contient que trois semaines.
    """
    from engine.apprentissage import Observation

    out: list[Observation] = []
    for path in fichiers('observations'):
        for l in _lire(path):
            if origine and (l.get('origine') or '') != origine:
                continue
            marche = l.get('marche') or ''
            if not marche:
                continue
            try:
                p = float(l['p_brute'])
            except (KeyError, TypeError, ValueError):
                continue
            out.append(Observation(
                marche=marche,
                complement=(l.get('complement') == '1'),
                ligue=l.get('competition') or '',
                p=p,
                y=1 if l.get('resultat') == 'gagne' else 0,
                quand=l.get('coup_denvoi') or '',
            ))
    out.sort(key=lambda ob: ob.quand)
    return out


def charger_resultats() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in fichiers('resultats'):
        out.extend(_lire(path))
    out.sort(key=lambda l: str(l.get('coup_denvoi')))
    return out


def statistiques() -> dict[str, Any]:
    obs = fichiers('observations')
    res = fichiers('resultats')
    n_obs = sum(max(len(_lire(p)), 0) for p in obs)
    n_res = sum(max(len(_lire(p)), 0) for p in res)
    mois = sorted({p.stem.split('-', 1)[1] for p in obs})
    return {
        'observations': n_obs,
        'resultats': n_res,
        'mois_couverts': mois,
        'premier_mois': mois[0] if mois else None,
        'dernier_mois': mois[-1] if mois else None,
        'repertoire': str(ARCHIVE_DIR),
    }


# --------------------------------------------------------------------------
# Amorçage depuis une archive publique
# --------------------------------------------------------------------------

# Codes de compétition internes → fichiers football-data.co.uk.
LIGUES_FOOTBALL_DATA = {
    'PL': 'E0', 'LIGA': 'SP1', 'BL': 'D1', 'SA': 'I1', 'L1': 'F1',
    'LP': 'P1',
}


def _saison_code(debut: int) -> str:
    """2024 → « 2425 »."""
    return f'{debut % 100:02d}{(debut + 1) % 100:02d}'


def importer_football_data(
    conn: sqlite3.Connection, *, saisons: list[int], ligues: list[str] | None = None,
    timeout: float = 40,
) -> dict[str, Any]:
    """Amorce `resultats` avec plusieurs saisons de scores réels.

    Source : football-data.co.uk, qui publie scores, scores à la pause et
    cotes de clôture pour les grands championnats. Le téléchargement n'est
    pas toujours autorisé par la politique réseau de l'hôte : dans ce cas la
    fonction le signale au lieu d'échouer, et l'archive se remplit simplement
    au fil des journées.
    """
    stats: dict[str, Any] = {'fichiers': 0, 'matchs': 0, 'echecs': [], 'ignores': 0}
    codes = ligues or list(LIGUES_FOOTBALL_DATA)
    for saison in saisons:
        for code in codes:
            fichier = LIGUES_FOOTBALL_DATA.get(code)
            if not fichier:
                continue
            url = (
                'https://www.football-data.co.uk/mmz4281/'
                f'{_saison_code(saison)}/{fichier}.csv'
            )
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    texte = resp.read().decode('latin-1')
            except Exception as e:  # noqa: BLE001
                stats['echecs'].append(f'{code} {saison} : {type(e).__name__}')
                continue
            stats['fichiers'] += 1
            stats['matchs'] += _ingerer_csv_football_data(conn, texte, code)
    return stats


def _ingerer_csv_football_data(conn: sqlite3.Connection, texte: str, code: str) -> int:
    from engine.identite import cle_equipe, cle_match

    n = 0
    for l in csv.DictReader(io.StringIO(texte)):
        dom, ext = (l.get('HomeTeam') or '').strip(), (l.get('AwayTeam') or '').strip()
        if not dom or not ext:
            continue
        date = _date_football_data(l.get('Date') or '')
        if not date:
            continue
        try:
            bd, be = int(l['FTHG']), int(l['FTAG'])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            bdm, bem = int(l['HTHG']), int(l['HTAG'])
        except (KeyError, TypeError, ValueError):
            bdm = bem = None
        cd, ce = cle_equipe(dom), cle_equipe(ext)
        if cd == ce:
            continue
        cle = cle_match(code, date, cd, ce)
        conn.execute(
            """INSERT INTO resultats(
                 cle, competition_code, coup_denvoi, domicile_cle, exterieur_cle,
                 buts_dom, buts_ext, buts_dom_mt, buts_ext_mt, enregistre_le)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(cle) DO NOTHING""",
            (cle, code, f'{date}T00:00:00+00:00', cd, ce, bd, be, bdm, bem,
             datetime.now(timezone.utc).isoformat()),
        )
        n += 1
    return n


# --------------------------------------------------------------------------
# Coupes d'Europe
# --------------------------------------------------------------------------

# openfootball publie la Ligue des champions et la Ligue Europa au format
# football.txt, scores à la pause compris. Aucune cote, mais les résultats
# suffisent à alimenter les forces d'équipe — et c'est en coupe d'Europe que
# les rencontres sans cotes sont les plus fréquentes.
BASE_OPENFOOTBALL = (
    'https://raw.githubusercontent.com/openfootball/champions-league/master'
)
COUPES_EUROPE = {'UCL': 'cl', 'UEL': 'el'}

_RX_MATCH = re.compile(
    r'^\s*(?:\d{1,2}:\d{2}\s+)?(?P<dom>.+?)\s+\((?P<pd>[A-Z]{3})\)\s+v\s+'
    r'(?P<ext>.+?)\s+\((?P<pe>[A-Z]{3})\)\s+(?P<bd>\d+)-(?P<be>\d+)'
    r'(?:\s+\((?P<bdm>\d+)-(?P<bem>\d+)\))?'
)
_RX_JOUR = re.compile(r'^\s{2}\w{3}\s+(\w{3})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$')
_MOIS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}


def lire_football_txt(texte: str, code: str) -> list[dict[str, Any]]:
    """Parse le format football.txt d'openfootball.

    Le score affiché est celui de la fin du temps réglementaire, suivi du
    score à la pause entre parenthèses : « 1-3 (1-1) ».
    """
    out: list[dict[str, Any]] = []
    annee: int | None = None
    jour: str | None = None
    for ligne in texte.splitlines():
        d = _RX_JOUR.match(ligne.rstrip())
        if d:
            mois, jj, aa = d.group(1), int(d.group(2)), d.group(3)
            if aa:
                annee = int(aa)
            if annee and mois in _MOIS:
                jour = f'{annee:04d}-{_MOIS[mois]:02d}-{jj:02d}'
            continue
        m = _RX_MATCH.match(ligne.rstrip())
        if not (m and jour):
            continue
        out.append({
            'date': jour, 'ligue': code,
            'dom': m.group('dom').strip(), 'ext': m.group('ext').strip(),
            'bd': int(m.group('bd')), 'be': int(m.group('be')),
            'bdm': int(m.group('bdm')) if m.group('bdm') else None,
            'bem': int(m.group('bem')) if m.group('bem') else None,
        })
    return out


def importer_coupes_europe(
    conn: sqlite3.Connection, *, saisons: list[str], timeout: float = 40,
) -> dict[str, Any]:
    """Amorce `resultats` avec la Ligue des champions et la Ligue Europa.

    `saisons` au format « 2023-24 ». Sans cotes, ces rencontres ne servent
    pas à la calibration : elles alimentent les forces d'équipe, qui prennent
    le relais quand aucune cote n'est disponible.
    """
    from engine.identite import cle_equipe, cle_match

    stats: dict[str, Any] = {'fichiers': 0, 'matchs': 0, 'echecs': []}
    for saison in saisons:
        for code, fichier in COUPES_EUROPE.items():
            url = f'{BASE_OPENFOOTBALL}/{saison}/{fichier}.txt'
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    texte = resp.read().decode('utf-8')
            except Exception as e:  # noqa: BLE001
                stats['echecs'].append(f'{code} {saison} : {type(e).__name__}')
                continue
            lignes = lire_football_txt(texte, code)
            if not lignes:
                continue
            stats['fichiers'] += 1
            for r in lignes:
                cd, ce = cle_equipe(r['dom']), cle_equipe(r['ext'])
                if cd == ce:
                    continue
                cle = cle_match(code, r['date'], cd, ce)
                conn.execute(
                    """INSERT INTO resultats(
                         cle, competition_code, coup_denvoi, domicile_cle,
                         exterieur_cle, buts_dom, buts_ext, buts_dom_mt,
                         buts_ext_mt, enregistre_le)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cle) DO NOTHING""",
                    (cle, code, f"{r['date']}T20:00:00+00:00", cd, ce,
                     r['bd'], r['be'], r['bdm'], r['bem'],
                     datetime.now(timezone.utc).isoformat()),
                )
                stats['matchs'] += 1
    return stats


def _date_football_data(brut: str) -> str | None:
    """« 17/08/2024 » ou « 17/08/24 » → « 2024-08-17 »."""
    morceaux = brut.strip().split('/')
    if len(morceaux) != 3:
        return None
    j, m, a = morceaux
    if len(a) == 2:
        a = f'20{a}'
    try:
        return datetime(int(a), int(m), int(j)).date().isoformat()
    except ValueError:
        return None
