"""Store SQLite du moteur (matchs, cotes, analyses)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS competitions (
  code TEXT PRIMARY KEY,
  nom TEXT NOT NULL,
  pays TEXT DEFAULT '',
  ordre INTEGER DEFAULT 100,
  actif INTEGER DEFAULT 1,
  sofascore_id INTEGER UNIQUE
);
CREATE TABLE IF NOT EXISTS equipes (
  slug TEXT PRIMARY KEY,
  nom TEXT NOT NULL,
  nom_court TEXT NOT NULL,
  sofascore_id INTEGER UNIQUE,
  thesportsdb_id INTEGER,
  logo_externe TEXT DEFAULT '',
  fiche_club TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS matchs (
  sofascore_id INTEGER PRIMARY KEY,
  competition_code TEXT NOT NULL,
  domicile_slug TEXT NOT NULL,
  exterieur_slug TEXT NOT NULL,
  coup_denvoi TEXT NOT NULL,
  journee TEXT DEFAULT '',
  statut TEXT DEFAULT 'a_venir',
  buts_dom INTEGER,
  buts_ext INTEGER,
  buts_dom_mt INTEGER,
  buts_ext_mt INTEGER,
  FOREIGN KEY (competition_code) REFERENCES competitions(code)
);
CREATE INDEX IF NOT EXISTS idx_matchs_kickoff ON matchs(coup_denvoi);
CREATE INDEX IF NOT EXISTS idx_matchs_statut ON matchs(statut);
CREATE TABLE IF NOT EXISTS cotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sofascore_id INTEGER NOT NULL,
  bookmaker TEXT NOT NULL,
  marche TEXT NOT NULL,
  selection TEXT NOT NULL,
  valeur REAL NOT NULL,
  nb_sources INTEGER DEFAULT 1,
  releve_le TEXT NOT NULL,
  UNIQUE (sofascore_id, bookmaker, marche, selection)
);
CREATE TABLE IF NOT EXISTS contextes (
  sofascore_id INTEGER PRIMARY KEY,
  forme_dom TEXT DEFAULT '',
  forme_ext TEXT DEFAULT '',
  absents_dom TEXT DEFAULT '',
  absents_ext TEXT DEFAULT '',
  tendance_buts TEXT DEFAULT '',
  a_savoir TEXT DEFAULT '',
  confrontations TEXT DEFAULT '',
  fiabilite TEXT DEFAULT 'bonne'
);
CREATE TABLE IF NOT EXISTS analyses (
  sofascore_id INTEGER PRIMARY KEY,
  payload TEXT NOT NULL,
  version_moteur TEXT,
  calcule_le TEXT NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def upsert_competition(conn: sqlite3.Connection, *, code: str, nom: str,
                       pays: str, ordre: int, sofascore_id: int) -> None:
    conn.execute(
        """INSERT INTO competitions(code, nom, pays, ordre, actif, sofascore_id)
           VALUES (?, ?, ?, ?, 1, ?)
           ON CONFLICT(code) DO UPDATE SET
             nom=excluded.nom, pays=excluded.pays, ordre=excluded.ordre,
             sofascore_id=excluded.sofascore_id, actif=1""",
        (code, nom, pays, ordre, sofascore_id),
    )


def upsert_equipe(
    conn: sqlite3.Connection,
    *,
    slug: str,
    nom: str,
    nom_court: str,
    sofascore_id: int | None,
    logo_externe: str = '',
) -> str:
    if sofascore_id:
        row = conn.execute(
            'SELECT slug FROM equipes WHERE sofascore_id = ?', (sofascore_id,)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE equipes SET nom=?, nom_court=?, logo_externe=COALESCE(NULLIF(?, ''), logo_externe)
                   WHERE sofascore_id=?""",
                (nom, nom_court, logo_externe, sofascore_id),
            )
            return row['slug']
    row = conn.execute('SELECT slug FROM equipes WHERE slug = ?', (slug,)).fetchone()
    if row:
        conn.execute(
            """UPDATE equipes SET nom=?, nom_court=?, sofascore_id=COALESCE(?, sofascore_id),
               logo_externe=COALESCE(NULLIF(?, ''), logo_externe) WHERE slug=?""",
            (nom, nom_court, sofascore_id, logo_externe, slug),
        )
        return slug
    base = slug
    i = 2
    while conn.execute('SELECT 1 FROM equipes WHERE slug = ?', (slug,)).fetchone():
        slug = f'{base}-{i}'
        i += 1
    conn.execute(
        """INSERT INTO equipes(slug, nom, nom_court, sofascore_id, logo_externe, fiche_club)
           VALUES (?, ?, ?, ?, ?, '{}')""",
        (slug, nom, nom_court, sofascore_id, logo_externe),
    )
    return slug


def upsert_match(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    existing = conn.execute(
        'SELECT statut FROM matchs WHERE sofascore_id = ?',
        (data['sofascore_id'],),
    ).fetchone()
    statut = data['statut']
    if existing and existing['statut'] == 'termine' and statut == 'a_venir':
        statut = 'termine'
    conn.execute(
        """INSERT INTO matchs(
             sofascore_id, competition_code, domicile_slug, exterieur_slug,
             coup_denvoi, journee, statut, buts_dom, buts_ext, buts_dom_mt, buts_ext_mt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(sofascore_id) DO UPDATE SET
             competition_code=excluded.competition_code,
             domicile_slug=excluded.domicile_slug,
             exterieur_slug=excluded.exterieur_slug,
             coup_denvoi=excluded.coup_denvoi,
             journee=excluded.journee,
             statut=?,
             buts_dom=COALESCE(excluded.buts_dom, matchs.buts_dom),
             buts_ext=COALESCE(excluded.buts_ext, matchs.buts_ext),
             buts_dom_mt=COALESCE(excluded.buts_dom_mt, matchs.buts_dom_mt),
             buts_ext_mt=COALESCE(excluded.buts_ext_mt, matchs.buts_ext_mt)
        """,
        (
            data['sofascore_id'], data['competition_code'], data['domicile_slug'],
            data['exterieur_slug'], data['coup_denvoi'], data.get('journee') or '',
            statut, data.get('buts_dom'), data.get('buts_ext'),
            data.get('buts_dom_mt'), data.get('buts_ext_mt'),
            statut,
        ),
    )


def upsert_cotes(
    conn: sqlite3.Connection,
    sofascore_id: int,
    bookmaker: str,
    marche: str,
    selections: list[tuple[str, float]],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for sel, val in selections:
        conn.execute(
            """INSERT INTO cotes(sofascore_id, bookmaker, marche, selection, valeur, nb_sources, releve_le)
               VALUES (?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(sofascore_id, bookmaker, marche, selection) DO UPDATE SET
                 valeur=excluded.valeur, releve_le=excluded.releve_le""",
            (sofascore_id, bookmaker, marche, sel, round(float(val), 3), now),
        )


def upsert_contexte(conn: sqlite3.Connection, sofascore_id: int, ctx: dict[str, str]) -> None:
    if not any(ctx.values()):
        return
    conn.execute(
        """INSERT INTO contextes(sofascore_id, forme_dom, forme_ext, absents_dom, absents_ext,
             tendance_buts, a_savoir, confrontations, fiabilite)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(sofascore_id) DO UPDATE SET
             forme_dom=excluded.forme_dom, forme_ext=excluded.forme_ext,
             absents_dom=excluded.absents_dom, absents_ext=excluded.absents_ext,
             tendance_buts=excluded.tendance_buts, a_savoir=excluded.a_savoir,
             confrontations=excluded.confrontations, fiabilite=excluded.fiabilite""",
        (
            sofascore_id,
            ctx.get('forme_dom') or '',
            ctx.get('forme_ext') or '',
            ctx.get('absents_dom') or '',
            ctx.get('absents_ext') or '',
            ctx.get('tendance_buts') or '',
            ctx.get('a_savoir') or '',
            ctx.get('confrontations') or '',
            ctx.get('fiabilite') or 'bonne',
        ),
    )


def save_analyse(conn: sqlite3.Connection, sofascore_id: int, payload: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO analyses(sofascore_id, payload, version_moteur, calcule_le)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(sofascore_id) DO UPDATE SET
             payload=excluded.payload, version_moteur=excluded.version_moteur,
             calcule_le=excluded.calcule_le""",
        (
            sofascore_id,
            json.dumps(payload, ensure_ascii=False),
            payload.get('version_moteur'),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def matchs_a_analyser(conn: sqlite3.Connection, jour: str | None = None) -> list[sqlite3.Row]:
    sql = """
      SELECT m.* FROM matchs m
      WHERE m.statut NOT IN ('termine', 'reporte')
    """
    params: list[Any] = []
    if jour:
        sql += " AND substr(m.coup_denvoi, 1, 10) = ?"
        params.append(jour)
    sql += " ORDER BY m.coup_denvoi"
    return list(conn.execute(sql, params).fetchall())


def cotes_prioritaires(conn: sqlite3.Connection, sofascore_id: int) -> dict[tuple[str, str], float]:
    prio = {'sofascore': 0, 'consensus': 1, 'PMUG': 2}
    meilleurs: dict[tuple[str, str], tuple[int, str, float]] = {}
    for row in conn.execute(
        'SELECT bookmaker, marche, selection, valeur, releve_le FROM cotes WHERE sofascore_id=?',
        (sofascore_id,),
    ):
        key = (row['marche'], row['selection'])
        pr = prio.get(row['bookmaker'], 9)
        cand = (pr, row['releve_le'] or '', float(row['valeur']))
        prev = meilleurs.get(key)
        if prev is None or cand[0] < prev[0] or (cand[0] == prev[0] and cand[1] > prev[1]):
            meilleurs[key] = cand
    return {k: v[2] for k, v in meilleurs.items()}


def fiche_equipe_locale(conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT * FROM matchs
           WHERE (domicile_slug=? OR exterieur_slug=?) AND statut='termine'
             AND buts_dom IS NOT NULL AND buts_ext IS NOT NULL
           ORDER BY coup_denvoi DESC LIMIT 8""",
        (slug, slug),
    ).fetchall()
    recents = []
    forme: list[str] = []
    eq = conn.execute('SELECT nom, nom_court FROM equipes WHERE slug=?', (slug,)).fetchone()
    for m in rows:
        is_home = m['domicile_slug'] == slug
        hs, aw = int(m['buts_dom']), int(m['buts_ext'])
        if is_home:
            res = 'W' if hs > aw else ('L' if hs < aw else 'D')
            adv_slug = m['exterieur_slug']
        else:
            res = 'W' if aw > hs else ('L' if aw < hs else 'D')
            adv_slug = m['domicile_slug']
        adv = conn.execute('SELECT nom_court, nom FROM equipes WHERE slug=?', (adv_slug,)).fetchone()
        adversaire = (adv['nom_court'] or adv['nom']) if adv else adv_slug
        forme.append(res)
        recents.append({
            'adversaire': adversaire,
            'score': f'{hs}-{aw}',
            'domicile': is_home,
            'resultat': res,
            'coup_denvoi': m['coup_denvoi'],
        })
    return {
        'nom': eq['nom'] if eq else slug,
        'nom_court': eq['nom_court'] if eq else slug,
        'forme': list(reversed(forme[:5])),
        'position': None,
        'note_moyenne': None,
        'classement': None,
        'recents': recents,
    }
