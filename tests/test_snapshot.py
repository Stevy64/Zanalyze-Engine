"""Contrat snapshot v1 (sans réseau)."""
from __future__ import annotations

import json
from pathlib import Path

from engine.store import (
    connect,
    init_db,
    upsert_competition,
    upsert_cotes,
    upsert_equipe,
    upsert_match,
    save_analyse,
)
from engine.snapshot import SNAPSHOT_VERSION, exporter_snapshot, ecrire_snapshot


def test_snapshot_v1_shape(tmp_path, monkeypatch):
    db = tmp_path / 'e.sqlite3'
    monkeypatch.setenv('ENGINE_DB_PATH', str(db))
    monkeypatch.setenv('ENGINE_DATA_DIR', str(tmp_path))
    from engine import paths, store
    paths.DB_PATH = db
    store.DB_PATH = db
    init_db()
    with connect() as conn:
        upsert_competition(
            conn, code='PL', nom='Premier League', pays='Angleterre',
            ordre=20, sofascore_id=17,
        )
        upsert_equipe(
            conn, slug='arsenal', nom='Arsenal', nom_court='Arsenal',
            sofascore_id=1, logo_externe='https://img.sofascore.com/api/v1/team/1/image',
        )
        upsert_equipe(
            conn, slug='chelsea', nom='Chelsea', nom_court='Chelsea',
            sofascore_id=2,
        )
        upsert_match(conn, {
            'sofascore_id': 99,
            'competition_code': 'PL',
            'domicile_slug': 'arsenal',
            'exterieur_slug': 'chelsea',
            'coup_denvoi': '2026-09-20T15:00:00+00:00',
            'journee': '5',
            'statut': 'a_venir',
            'buts_dom': None,
            'buts_ext': None,
            'buts_dom_mt': None,
            'buts_ext_mt': None,
        })
        upsert_cotes(conn, 99, 'sofascore', '1X2', [('1', 1.9), ('N', 3.5), ('2', 4.0)])
        save_analyse(conn, 99, {
            'buts_dom_attendus': 1.4,
            'buts_ext_attendus': 1.1,
            'p1': 0.45, 'pn': 0.28, 'p2': 0.27,
            'score_probable': '1-1',
            'profil': 'moyen',
            'marge_marche': 0.05,
            'residu': 0.01,
            'version_moteur': '3.1.0',
            'options': [{
                'famille': '1X2', 'code': '1X2_1', 'libelle': 'Arsenal',
                'probabilite': 0.45, 'cote_juste': 2.22, 'niveau': 'prudente',
                'origine': 'marche', 'resultat': 'attente',
            }],
        })

    data = exporter_snapshot(jours=365)
    assert data['version'] == SNAPSHOT_VERSION
    assert data['competitions'][0]['code'] == 'PL'
    assert data['equipes'][0]['slug'] in ('arsenal', 'chelsea')
    m = data['matchs'][0]
    assert m['sofascore_id'] == 99
    assert m['cotes']
    assert m['analyse']['options'][0]['code'] == '1X2_1'
    out = tmp_path / 'matchs.json'
    ecrire_snapshot(jours=365, path=out)
    loaded = json.loads(Path(out).read_text(encoding='utf-8'))
    assert loaded['matchs'][0]['competition_code'] == 'PL'
