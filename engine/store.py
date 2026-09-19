"""
Persistance SQLite — schéma v2.

Ce que le schéma v1 ne permettait pas
-------------------------------------
1. Une seule colonne `sofascore_id` servait d'identifiant unique aux deux
   fournisseurs. Les espaces d'ids se recouvrant, un club en écrasait un autre.
   → v2 : une colonne par fournisseur, et une **clé canonique** comme identité.
2. Les cotes étaient écrasées à chaque relevé : ni ouverture, ni clôture, ni
   mouvement. → v2 : `cotes` (état courant) + `cotes_historique` (ajout seul).
3. Les analyses étaient recalculées indéfiniment, y compris pendant le match.
   → v2 : `predictions` en ajout seul, horodatées, jamais réécrites.
4. Aucun résultat conservé au-delà de la fenêtre d'export.
   → v2 : `resultats` durable, socle de l'apprentissage.

Pas d'ORM : des upserts idempotents, pour qu'un refresh puisse être rejoué.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.identite import cle_equipe, jetons, nom_court, slug
from engine.paths import DB_PATH, ensure_dirs

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  cle TEXT PRIMARY KEY,
  valeur TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitions (
  code TEXT PRIMARY KEY,
  nom TEXT NOT NULL,
  pays TEXT DEFAULT '',
  ordre INTEGER DEFAULT 100,
  actif INTEGER DEFAULT 1,
  sofascore_id INTEGER
);
CREATE TABLE IF NOT EXISTS equipes (
  cle TEXT PRIMARY KEY,
  nom TEXT NOT NULL,
  nom_court TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  logo_externe TEXT DEFAULT '',
  espn_id INTEGER UNIQUE,
  sofascore_id INTEGER UNIQUE,
  thesportsdb_id INTEGER,
  jetons TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS matchs (
  cle TEXT PRIMARY KEY,
  competition_code TEXT NOT NULL,
  domicile_cle TEXT NOT NULL,
  exterieur_cle TEXT NOT NULL,
  coup_denvoi TEXT NOT NULL,
  journee TEXT DEFAULT '',
  statut TEXT DEFAULT 'a_venir',
  buts_dom INTEGER,
  buts_ext INTEGER,
  buts_dom_mt INTEGER,
  buts_ext_mt INTEGER,
  espn_id INTEGER,
  sofascore_id INTEGER,
  suspect INTEGER DEFAULT 0,
  anomalies TEXT DEFAULT '',
  maj_le TEXT,
  FOREIGN KEY (competition_code) REFERENCES competitions(code)
);
CREATE INDEX IF NOT EXISTS idx_matchs_kickoff ON matchs(coup_denvoi);
CREATE INDEX IF NOT EXISTS idx_matchs_statut ON matchs(statut);
CREATE TABLE IF NOT EXISTS cotes (
  cle TEXT NOT NULL,
  bookmaker TEXT NOT NULL,
  marche TEXT NOT NULL,
  selection TEXT NOT NULL,
  ligne REAL,
  valeur REAL NOT NULL,
  phase TEXT NOT NULL DEFAULT 'courante',
  releve_le TEXT NOT NULL,
  PRIMARY KEY (cle, bookmaker, marche, selection, ligne, phase)
);
CREATE TABLE IF NOT EXISTS cotes_historique (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cle TEXT NOT NULL,
  bookmaker TEXT NOT NULL,
  marche TEXT NOT NULL,
  selection TEXT NOT NULL,
  ligne REAL,
  valeur REAL NOT NULL,
  releve_le TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_cle ON cotes_historique(cle);
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cle TEXT NOT NULL,
  version_moteur TEXT NOT NULL,
  calcule_le TEXT NOT NULL,
  minutes_avant INTEGER,
  avant_match INTEGER DEFAULT 1,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pred_cle ON predictions(cle);
CREATE TABLE IF NOT EXISTS analyses (
  cle TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  version_moteur TEXT,
  calcule_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resultats (
  cle TEXT PRIMARY KEY,
  competition_code TEXT,
  coup_denvoi TEXT,
  domicile_cle TEXT,
  exterieur_cle TEXT,
  buts_dom INTEGER,
  buts_ext INTEGER,
  buts_dom_mt INTEGER,
  buts_ext_mt INTEGER,
  enregistre_le TEXT
);
CREATE TABLE IF NOT EXISTS contextes (
  cle TEXT PRIMARY KEY,
  forme_dom TEXT DEFAULT '',
  forme_ext TEXT DEFAULT '',
  absents_dom TEXT DEFAULT '',
  absents_ext TEXT DEFAULT '',
  tendance_buts TEXT DEFAULT '',
  a_savoir TEXT DEFAULT '',
  confrontations TEXT DEFAULT '',
  fiabilite TEXT DEFAULT 'bonne'
);
"""

# Tables du schéma v1, incompatibles : renommées puis ignorées.
_TABLES_V1 = ('matchs', 'equipes', 'cotes', 'analyses', 'contextes')


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _version_schema(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT valeur FROM meta WHERE cle='schema'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row['valeur']) if row else 0


def _est_schema_v1(conn: sqlite3.Connection) -> bool:
    """Le v1 se reconnaît à `matchs.sofascore_id` en clé primaire."""
    try:
        cols = conn.execute('PRAGMA table_info(matchs)').fetchall()
    except sqlite3.OperationalError:
        return False
    if not cols:
        return False
    return any(c['name'] == 'sofascore_id' and c['pk'] for c in cols)


def migrer_depuis_v1(conn: sqlite3.Connection) -> dict[str, int]:
    """Met de côté les tables v1 et récupère ce qui est sûr.

    Les identités d'équipes v1 sont corrompues (collision d'ids entre
    fournisseurs) : on ne les reprend pas. Les **scores**, eux, sont fiables
    et alimentent `resultats`, socle de l'apprentissage. Les cotes et analyses
    v1 sont abandonnées : elles reposent sur une ligne de totaux erronée.
    """
    stats = {'resultats_repris': 0, 'tables_archivees': 0}
    horodatage = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')

    anciens: list[sqlite3.Row] = []
    try:
        anciens = conn.execute(
            """SELECT m.sofascore_id, m.competition_code, m.coup_denvoi,
                      m.buts_dom, m.buts_ext, m.buts_dom_mt, m.buts_ext_mt,
                      d.nom AS nom_dom, e.nom AS nom_ext
               FROM matchs m
               LEFT JOIN equipes d ON d.slug = m.domicile_slug
               LEFT JOIN equipes e ON e.slug = m.exterieur_slug
               WHERE m.statut = 'termine'
                 AND m.buts_dom IS NOT NULL AND m.buts_ext IS NOT NULL"""
        ).fetchall()
    except sqlite3.OperationalError:
        anciens = []

    for t in _TABLES_V1:
        try:
            conn.execute(f'ALTER TABLE {t} RENAME TO {t}_v1_{horodatage}')
            stats['tables_archivees'] += 1
        except sqlite3.OperationalError:
            pass
    conn.executescript(SCHEMA)

    # Les noms v1 peuvent avoir été mélangés par la collision d'ids : on ne
    # reprend un résultat que si les deux noms sont exploitables.
    from engine.identite import cle_match
    for r in anciens:
        nd, ne = (r['nom_dom'] or '').strip(), (r['nom_ext'] or '').strip()
        if not nd or not ne or '(ancien' in nd or '(ancien' in ne:
            continue
        cd, ce = cle_equipe(nd), cle_equipe(ne)
        if cd == ce:
            continue
        cle = cle_match(r['competition_code'], r['coup_denvoi'], cd, ce)
        conn.execute(
            """INSERT OR IGNORE INTO resultats(
                 cle, competition_code, coup_denvoi, domicile_cle, exterieur_cle,
                 buts_dom, buts_ext, buts_dom_mt, buts_ext_mt, enregistre_le)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                cle, r['competition_code'], r['coup_denvoi'], cd, ce,
                r['buts_dom'], r['buts_ext'], r['buts_dom_mt'], r['buts_ext_mt'],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        stats['resultats_repris'] += 1
    return stats


def init_db() -> dict[str, int]:
    """Crée ou met à niveau le schéma. Idempotent."""
    stats: dict[str, int] = {}
    with connect() as conn:
        if _est_schema_v1(conn):
            stats = migrer_depuis_v1(conn)
        else:
            conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO meta(cle, valeur) VALUES ('schema', ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
            (str(SCHEMA_VERSION),),
        )
    return stats


# --------------------------------------------------------------------------
# Compétitions et équipes
# --------------------------------------------------------------------------

def upsert_competition(conn: sqlite3.Connection, *, code: str, nom: str,
                       pays: str, ordre: int, sofascore_id: int | None) -> None:
    conn.execute(
        """INSERT INTO competitions(code, nom, pays, ordre, actif, sofascore_id)
           VALUES (?, ?, ?, ?, 1, ?)
           ON CONFLICT(code) DO UPDATE SET
             nom=excluded.nom, pays=excluded.pays, ordre=excluded.ordre,
             sofascore_id=excluded.sofascore_id, actif=1""",
        (code, nom, pays, ordre, sofascore_id),
    )


def _slug_libre(conn: sqlite3.Connection, souhaite: str, cle: str) -> str:
    """Slug unique, sans jamais voler celui d'une autre équipe."""
    base = souhaite or 'equipe'
    candidat = base
    n = 2
    while True:
        row = conn.execute(
            'SELECT cle FROM equipes WHERE slug = ?', (candidat,)
        ).fetchone()
        if row is None or row['cle'] == cle:
            return candidat
        candidat = f'{base}-{n}'[:48]
        n += 1


def resoudre_equipe(
    conn: sqlite3.Connection,
    *,
    nom: str,
    provider: str,
    provider_id: int | None = None,
    logo: str = '',
    nom_court_fourni: str = '',
) -> str:
    """Renvoie la clé canonique du club, en le créant au besoin.

    L'identité vient **du nom**, jamais de l'identifiant du fournisseur :
    c'est ce qui empêche un id ESPN d'écraser un club SofaScore. Les ids sont
    conservés, chacun dans sa colonne, à titre documentaire.
    """
    colonne = 'espn_id' if provider == 'espn' else 'sofascore_id'
    cle = cle_equipe(nom)

    # L'identité repose sur la clé canonique seule. Rapprocher deux clubs
    # parce qu'un nom est inclus dans l'autre fusionnerait « Arsenal » et
    # « Arsenal Tula » : les rapprochements sûrs passent par la table ALIAS.
    row = conn.execute('SELECT cle FROM equipes WHERE cle = ?', (cle,)).fetchone()

    j = ' '.join(jetons(nom))
    court = (nom_court_fourni or nom_court(nom))[:24]

    if row is None:
        conn.execute(
            """INSERT INTO equipes(cle, nom, nom_court, slug, logo_externe, jetons)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cle, nom[:80], court, _slug_libre(conn, slug(cle), cle), logo or '', j),
        )
    else:
        # On garde le libellé le plus complet : « Feyenoord Rotterdam » plutôt
        # que « Feyenoord ». Le slug, lui, ne bouge jamais.
        conn.execute(
            """UPDATE equipes SET
                  nom = CASE WHEN length(?) > length(nom) THEN ? ELSE nom END,
                  nom_court = COALESCE(NULLIF(?, ''), nom_court),
                  logo_externe = COALESCE(NULLIF(?, ''), logo_externe),
                  jetons = COALESCE(NULLIF(?, ''), jetons)
                WHERE cle = ?""",
            (nom[:80], nom[:80], court, logo or '', j, cle),
        )

    if provider_id:
        # L'id ne s'installe que s'il est libre : jamais au prix d'un autre club.
        occupe = conn.execute(
            f'SELECT cle FROM equipes WHERE {colonne} = ?', (int(provider_id),)
        ).fetchone()
        if occupe is None:
            conn.execute(
                f'UPDATE equipes SET {colonne} = ? WHERE cle = ? AND {colonne} IS NULL',
                (int(provider_id), cle),
            )
    return cle


def equipe(conn: sqlite3.Connection, cle: str) -> sqlite3.Row | None:
    return conn.execute('SELECT * FROM equipes WHERE cle = ?', (cle,)).fetchone()


# --------------------------------------------------------------------------
# Matchs
# --------------------------------------------------------------------------

def upsert_match(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """Écrit une rencontre. Un statut « terminé » ne redevient jamais « à venir »."""
    existant = conn.execute(
        'SELECT statut, espn_id, sofascore_id FROM matchs WHERE cle = ?',
        (data['cle'],),
    ).fetchone()
    statut = data.get('statut') or 'a_venir'
    if existant and existant['statut'] == 'termine' and statut == 'a_venir':
        statut = 'termine'
    espn_id = data.get('espn_id') or (existant['espn_id'] if existant else None)
    sofa_id = data.get('sofascore_id') or (existant['sofascore_id'] if existant else None)

    conn.execute(
        """INSERT INTO matchs(
             cle, competition_code, domicile_cle, exterieur_cle, coup_denvoi,
             journee, statut, buts_dom, buts_ext, buts_dom_mt, buts_ext_mt,
             espn_id, sofascore_id, suspect, anomalies, maj_le)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(cle) DO UPDATE SET
             competition_code=excluded.competition_code,
             domicile_cle=excluded.domicile_cle,
             exterieur_cle=excluded.exterieur_cle,
             coup_denvoi=excluded.coup_denvoi,
             journee=COALESCE(NULLIF(excluded.journee,''), matchs.journee),
             statut=excluded.statut,
             buts_dom=COALESCE(excluded.buts_dom, matchs.buts_dom),
             buts_ext=COALESCE(excluded.buts_ext, matchs.buts_ext),
             buts_dom_mt=COALESCE(excluded.buts_dom_mt, matchs.buts_dom_mt),
             buts_ext_mt=COALESCE(excluded.buts_ext_mt, matchs.buts_ext_mt),
             espn_id=COALESCE(excluded.espn_id, matchs.espn_id),
             sofascore_id=COALESCE(excluded.sofascore_id, matchs.sofascore_id),
             maj_le=excluded.maj_le""",
        (
            data['cle'], data['competition_code'], data['domicile_cle'],
            data['exterieur_cle'], data['coup_denvoi'], data.get('journee') or '',
            statut, data.get('buts_dom'), data.get('buts_ext'),
            data.get('buts_dom_mt'), data.get('buts_ext_mt'),
            espn_id, sofa_id, int(data.get('suspect') or 0),
            data.get('anomalies') or '', datetime.now(timezone.utc).isoformat(),
        ),
    )
    if statut == 'termine' and data.get('buts_dom') is not None:
        enregistrer_resultat(conn, data['cle'])


def marquer_suspect(conn: sqlite3.Connection, cle: str, anomalies: str) -> None:
    conn.execute(
        'UPDATE matchs SET suspect = 1, anomalies = ? WHERE cle = ?', (anomalies, cle)
    )


def reinitialiser_suspects(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE matchs SET suspect = 0, anomalies = ''")


def enregistrer_resultat(conn: sqlite3.Connection, cle: str) -> None:
    """Copie un score terminé dans l'historique durable."""
    conn.execute(
        """INSERT INTO resultats(
             cle, competition_code, coup_denvoi, domicile_cle, exterieur_cle,
             buts_dom, buts_ext, buts_dom_mt, buts_ext_mt, enregistre_le)
           SELECT cle, competition_code, coup_denvoi, domicile_cle, exterieur_cle,
                  buts_dom, buts_ext, buts_dom_mt, buts_ext_mt, ?
             FROM matchs WHERE cle = ? AND buts_dom IS NOT NULL AND buts_ext IS NOT NULL
           ON CONFLICT(cle) DO UPDATE SET
             buts_dom=excluded.buts_dom, buts_ext=excluded.buts_ext,
             buts_dom_mt=COALESCE(excluded.buts_dom_mt, resultats.buts_dom_mt),
             buts_ext_mt=COALESCE(excluded.buts_ext_mt, resultats.buts_ext_mt)""",
        (datetime.now(timezone.utc).isoformat(), cle),
    )


# --------------------------------------------------------------------------
# Cotes : état courant + historique en ajout seul
# --------------------------------------------------------------------------

def upsert_cotes(
    conn: sqlite3.Connection,
    cle: str,
    bookmaker: str,
    marche: str,
    selections: list[tuple[str, float]],
    *,
    ligne: float | None = None,
    phase: str = 'courante',
) -> None:
    """Enregistre un relevé. `phase` : ouverture / courante / cloture.

    L'état courant est mis à jour, et **chaque** relevé est ajouté à
    l'historique : c'est lui qui rend le mouvement de cote observable.
    """
    now = datetime.now(timezone.utc).isoformat()
    lg = float(ligne) if ligne is not None else None
    for sel, val in selections:
        if val is None:
            continue
        v = round(float(val), 3)
        conn.execute(
            """INSERT INTO cotes(cle, bookmaker, marche, selection, ligne, valeur, phase, releve_le)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(cle, bookmaker, marche, selection, ligne, phase) DO UPDATE SET
                 valeur=excluded.valeur, releve_le=excluded.releve_le""",
            (cle, bookmaker, marche, sel, lg, v, phase, now),
        )
        derniere = conn.execute(
            """SELECT valeur FROM cotes_historique
               WHERE cle=? AND bookmaker=? AND marche=? AND selection=?
                 AND (ligne IS ?)
               ORDER BY id DESC LIMIT 1""",
            (cle, bookmaker, marche, sel, lg),
        ).fetchone()
        if derniere is None or abs(float(derniere['valeur']) - v) > 1e-9:
            conn.execute(
                """INSERT INTO cotes_historique(
                     cle, bookmaker, marche, selection, ligne, valeur, releve_le)
                   VALUES (?,?,?,?,?,?,?)""",
                (cle, bookmaker, marche, sel, lg, v, now),
            )


# Priorité des fournisseurs à qualité égale. Plus bas = préféré.
PRIORITE_BOOKMAKER = {'espn': 0, 'sofascore': 1, 'consensus': 2}


def cotes_utilisables(conn: sqlite3.Connection, cle: str) -> dict[str, Any]:
    """Meilleur jeu de cotes disponible pour une rencontre.

    Renvoie `{'1x2': (c1, cn, c2), 'totaux': (over, under, ligne),
    'mouvement': {...}, 'source': str}`. Les deux marchés viennent du **même**
    fournisseur : mélanger un 1X2 et des totaux de deux books introduirait une
    incohérence que le modèle prendrait pour de l'information.
    """
    lignes = conn.execute(
        """SELECT bookmaker, marche, selection, ligne, valeur, phase
             FROM cotes WHERE cle = ?""",
        (cle,),
    ).fetchall()
    if not lignes:
        return {}

    par_book: dict[str, dict[str, Any]] = {}
    for r in lignes:
        book = par_book.setdefault(r['bookmaker'], {'1x2': {}, 'totaux': {}})
        if r['marche'] == '1X2' and r['phase'] == 'courante':
            book['1x2'][r['selection']] = float(r['valeur'])
        elif r['marche'] == 'TOTAUX' and r['phase'] == 'courante':
            book['totaux'].setdefault(r['ligne'], {})[r['selection']] = float(r['valeur'])

    meilleurs = sorted(par_book.items(), key=lambda kv: PRIORITE_BOOKMAKER.get(kv[0], 9))
    for book, data in meilleurs:
        trio = data['1x2']
        if not all(k in trio for k in ('1', 'N', '2')):
            continue
        totaux = None
        for lg, prix in sorted(data['totaux'].items(), key=lambda kv: (kv[0] is None, kv[0])):
            if lg is not None and 'over' in prix and 'under' in prix:
                totaux = (prix['over'], prix['under'], float(lg))
                break
        return {
            '1x2': (trio['1'], trio['N'], trio['2']),
            'totaux': totaux,
            'source': book,
            'mouvement': mouvement_cotes(conn, cle, book),
        }
    return {}


def mouvement_cotes(conn: sqlite3.Connection, cle: str, bookmaker: str) -> dict[str, float]:
    """Variation ouverture → courante du 1X2, en probabilité implicite.

    Seule information de marché qui ne soit pas déjà dans le prix courant :
    elle dit dans quel sens l'argent est allé.
    """
    ouv = conn.execute(
        """SELECT selection, valeur FROM cotes
             WHERE cle=? AND bookmaker=? AND marche='1X2' AND phase='ouverture'""",
        (cle, bookmaker),
    ).fetchall()
    cur = conn.execute(
        """SELECT selection, valeur FROM cotes
             WHERE cle=? AND bookmaker=? AND marche='1X2' AND phase='courante'""",
        (cle, bookmaker),
    ).fetchall()
    if len(ouv) < 3 or len(cur) < 3:
        return {}
    a = {r['selection']: float(r['valeur']) for r in ouv}
    b = {r['selection']: float(r['valeur']) for r in cur}
    out: dict[str, float] = {}
    for sel in ('1', 'N', '2'):
        if sel in a and sel in b and a[sel] > 0 and b[sel] > 0:
            out[sel] = (1 / b[sel]) - (1 / a[sel])
    return out


# --------------------------------------------------------------------------
# Prédictions (ajout seul) et vue courante
# --------------------------------------------------------------------------

def enregistrer_prediction(
    conn: sqlite3.Connection,
    cle: str,
    payload: dict[str, Any],
    *,
    minutes_avant: int | None,
    avant_match: bool,
) -> None:
    """Ajoute une prédiction à l'historique. Jamais de réécriture."""
    conn.execute(
        """INSERT INTO predictions(
             cle, version_moteur, calcule_le, minutes_avant, avant_match, payload)
           VALUES (?,?,?,?,?,?)""",
        (
            cle, payload.get('version_moteur') or '?',
            datetime.now(timezone.utc).isoformat(),
            minutes_avant, 1 if avant_match else 0,
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def save_analyse(conn: sqlite3.Connection, cle: str, payload: dict[str, Any]) -> None:
    """Vue courante servie au snapshot."""
    conn.execute(
        """INSERT INTO analyses(cle, payload, version_moteur, calcule_le)
           VALUES (?,?,?,?)
           ON CONFLICT(cle) DO UPDATE SET
             payload=excluded.payload, version_moteur=excluded.version_moteur,
             calcule_le=excluded.calcule_le""",
        (
            cle, json.dumps(payload, ensure_ascii=False),
            payload.get('version_moteur'), datetime.now(timezone.utc).isoformat(),
        ),
    )


def analyse(conn: sqlite3.Connection, cle: str) -> dict[str, Any] | None:
    row = conn.execute('SELECT payload FROM analyses WHERE cle = ?', (cle,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['payload'])
    except json.JSONDecodeError:
        return None


def prediction_officielle(conn: sqlite3.Connection, cle: str) -> dict[str, Any] | None:
    """Dernière prédiction produite **avant** le coup d'envoi.

    C'est elle, et elle seule, qui compte pour juger le moteur.
    """
    row = conn.execute(
        """SELECT payload FROM predictions
             WHERE cle = ? AND avant_match = 1
             ORDER BY calcule_le DESC LIMIT 1""",
        (cle,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['payload'])
    except json.JSONDecodeError:
        return None


def a_prediction_avant_match(conn: sqlite3.Connection, cle: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM predictions WHERE cle = ? AND avant_match = 1 LIMIT 1', (cle,)
    ).fetchone()
    return row is not None


# --------------------------------------------------------------------------
# Lectures
# --------------------------------------------------------------------------

def matchs_a_analyser(conn: sqlite3.Connection, jour: str | None = None) -> list[sqlite3.Row]:
    """Rencontres à venir, non suspectes, qui n'ont pas encore commencé.

    Un match commencé n'est **jamais** recalculé : ses cotes intègrent déjà
    ce qui se passe sur le terrain.
    """
    maintenant = datetime.now(timezone.utc).isoformat()
    sql = """
      SELECT * FROM matchs
       WHERE suspect = 0
         AND statut = 'a_venir'
         AND coup_denvoi > ?
    """
    params: list[Any] = [maintenant]
    if jour:
        sql += ' AND substr(coup_denvoi, 1, 10) = ?'
        params.append(jour)
    sql += ' ORDER BY coup_denvoi'
    return list(conn.execute(sql, params).fetchall())


def matchs_fenetre(conn: sqlite3.Connection, debut: str, fin: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT * FROM matchs
            WHERE coup_denvoi >= ? AND coup_denvoi <= ? AND suspect = 0
            ORDER BY coup_denvoi""",
        (debut, fin),
    ).fetchall())


def fiche_equipe_locale(conn: sqlite3.Connection, cle: str) -> dict[str, Any]:
    """Forme récente, reconstruite depuis l'historique durable."""
    rows = conn.execute(
        """SELECT * FROM resultats
            WHERE (domicile_cle = ? OR exterieur_cle = ?)
            ORDER BY coup_denvoi DESC LIMIT 8""",
        (cle, cle),
    ).fetchall()
    eq = equipe(conn, cle)
    forme: list[str] = []
    recents: list[dict[str, Any]] = []
    for m in rows:
        chez_soi = m['domicile_cle'] == cle
        hs, aw = int(m['buts_dom']), int(m['buts_ext'])
        if chez_soi:
            res = 'W' if hs > aw else ('L' if hs < aw else 'D')
            adv_cle = m['exterieur_cle']
        else:
            res = 'W' if aw > hs else ('L' if aw < hs else 'D')
            adv_cle = m['domicile_cle']
        adv = equipe(conn, adv_cle)
        forme.append(res)
        recents.append({
            'adversaire': (adv['nom_court'] if adv else adv_cle),
            'score': f'{hs}-{aw}',
            'domicile': chez_soi,
            'resultat': res,
            'coup_denvoi': m['coup_denvoi'],
        })
    return {
        'nom': eq['nom'] if eq else cle,
        'nom_court': eq['nom_court'] if eq else cle,
        'forme': list(reversed(forme[:5])),
        'position': None,
        'note_moyenne': None,
        'classement': None,
        'recents': recents,
    }
