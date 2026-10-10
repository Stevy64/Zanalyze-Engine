"""L'état courant d'une cote est unique ; la série, elle, est conservée.

Défaut constaté le 9 octobre 2026 : `cotes` a pour clé primaire
(cle, bookmaker, marche, selection, ligne, phase), et `ligne` vaut NULL pour
le 1X2. Or NULL n'entre jamais en conflit avec NULL dans une contrainte
d'unicité : l'`ON CONFLICT` ne se déclenchait donc jamais sur le 1X2, et
chaque cycle de deux heures ajoutait une ligne de plus.

Mesure sur l'instantané de production : 26 076 cotes 1X2 pour 210 matchs,
jusqu'à 251 pour une seule rencontre, contre 306 lignes pour les totaux — qui
portent une ligne non nulle et se mettaient donc bien à jour. L'instantané
pesait 9,4 Mo.

Ces relevés avaient malgré tout une valeur : horodatés, ils forment la série
temporelle du marché. Ils vont désormais dans `cotes_historique`.
"""
import pytest


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setenv('ENGINE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ENGINE_DB_PATH', str(tmp_path / 'e.sqlite3'))
    monkeypatch.setenv('ENGINE_ARCHIVE_DIR', str(tmp_path / 'archive'))
    from engine import paths, store
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(paths, 'DB_PATH', tmp_path / 'e.sqlite3')
    monkeypatch.setattr(paths, 'ARCHIVE_DIR', tmp_path / 'archive')
    monkeypatch.setattr(store, 'DB_PATH', paths.DB_PATH)
    store.init_db()
    return store


def _match(base, conn, cle='PL:2026-10-10:arsenal:chelsea'):
    base.upsert_competition(conn, code='PL', nom='Premier League',
                            pays='Angleterre', ordre=20, sofascore_id=None)
    dom = base.resoudre_equipe(conn, nom='Arsenal', provider='espn', provider_id=359)
    ext = base.resoudre_equipe(conn, nom='Chelsea', provider='espn', provider_id=363)
    base.upsert_match(conn, {
        'cle': cle, 'competition_code': 'PL', 'domicile_cle': dom,
        'exterieur_cle': ext, 'coup_denvoi': '2026-10-10T18:00:00+00:00',
        'journee': '', 'statut': 'a_venir',
    })
    return cle


def _compte(conn, cle, marche='1X2'):
    return conn.execute(
        'SELECT COUNT(*) FROM cotes WHERE cle = ? AND marche = ?', (cle, marche),
    ).fetchone()[0]


def test_quatre_vingt_releves_1x2_ne_font_qu_une_cote_courante(base):
    """Le défaut exact : 83 cycles donnaient 249 lignes au lieu de 3."""
    with base.connect() as conn:
        cle = _match(base, conn)
        for k in range(83):
            base.upsert_cotes(conn, cle, 'draftkings', '1X2',
                              [('1', 2.10 + k * 0.01), ('N', 3.40), ('2', 3.20)])
        assert _compte(conn, cle) == 3


def test_la_cote_courante_est_la_derniere_relevee(base):
    with base.connect() as conn:
        cle = _match(base, conn)
        for v in (2.10, 2.05, 1.98):
            base.upsert_cotes(conn, cle, 'draftkings', '1X2', [('1', v)])
        (valeur,) = conn.execute(
            "SELECT valeur FROM cotes WHERE cle=? AND marche='1X2' AND selection='1'",
            (cle,),
        ).fetchone()
        assert valeur == pytest.approx(1.98)


def test_les_totaux_restent_uniques_par_ligne(base):
    """Deux lignes de totaux sont deux marchés distincts, pas un doublon."""
    with base.connect() as conn:
        cle = _match(base, conn)
        base.upsert_cotes(conn, cle, 'draftkings', 'TOTAUX',
                          [('over', 1.90), ('under', 1.95)], ligne=2.5)
        base.upsert_cotes(conn, cle, 'draftkings', 'TOTAUX',
                          [('over', 2.60), ('under', 1.50)], ligne=3.5)
        assert _compte(conn, cle, 'TOTAUX') == 4


def test_ouverture_et_courante_coexistent(base):
    """La phase distingue deux relevés du même marché, elle ne les fusionne pas."""
    with base.connect() as conn:
        cle = _match(base, conn)
        base.upsert_cotes(conn, cle, 'draftkings', '1X2', [('1', 2.30)],
                          phase='ouverture')
        base.upsert_cotes(conn, cle, 'draftkings', '1X2', [('1', 1.98)],
                          phase='courante')
        assert _compte(conn, cle) == 2


def test_le_mouvement_est_conserve_dans_l_historique(base):
    """Ce qui fait l'intérêt du correctif : on dédoublonne sans rien perdre."""
    with base.connect() as conn:
        cle = _match(base, conn)
        for v in (2.10, 2.05, 2.05, 1.98):
            base.upsert_cotes(conn, cle, 'draftkings', '1X2', [('1', v)])
        serie = [r['valeur'] for r in conn.execute(
            """SELECT valeur FROM cotes_historique
                WHERE cle=? AND selection='1' ORDER BY id""", (cle,))]
        # Un relevé identique au précédent n'ajoute rien : seul le mouvement
        # est enregistré.
        assert serie == [2.10, 2.05, 1.98]


def test_recuperation_des_cotes_deja_accumulees(base):
    """Les 26 076 cotes déjà en base deviennent une série, pas une perte."""
    with base.connect() as conn:
        cle = _match(base, conn)
        # On reproduit l'état d'avant correctif : insertions brutes.
        for k, v in enumerate((2.30, 2.20, 2.10, 1.98)):
            conn.execute(
                """INSERT INTO cotes(cle, bookmaker, marche, selection, ligne,
                                     valeur, phase, releve_le)
                   VALUES (?,?,?,?,NULL,?, 'courante', ?)""",
                (cle, 'draftkings', '1X2', '1', v, f'2026-10-0{k + 1}T00:00:00+00:00'),
            )
        assert _compte(conn, cle) == 4

        stats = base.recuperer_cotes_accumulees(conn)
        assert stats['cotes_dedoublonnees'] == 3
        assert stats['cotes_versees_a_l_historique'] == 4
        assert _compte(conn, cle) == 1
        (restante,) = conn.execute(
            "SELECT valeur FROM cotes WHERE cle=? AND marche='1X2'", (cle,),
        ).fetchone()
        assert restante == pytest.approx(1.98)
        serie = [r['valeur'] for r in conn.execute(
            'SELECT valeur FROM cotes_historique WHERE cle=? ORDER BY id', (cle,))]
        assert serie == [2.30, 2.20, 2.10, 1.98]


def test_la_recuperation_est_idempotente(base):
    with base.connect() as conn:
        cle = _match(base, conn)
        for k, v in enumerate((2.30, 2.10)):
            conn.execute(
                """INSERT INTO cotes(cle, bookmaker, marche, selection, ligne,
                                     valeur, phase, releve_le)
                   VALUES (?,?,?,?,NULL,?, 'courante', ?)""",
                (cle, 'draftkings', '1X2', '1', v, f'2026-10-0{k + 1}T00:00:00+00:00'),
            )
        base.recuperer_cotes_accumulees(conn)
        assert base.recuperer_cotes_accumulees(conn) == {}
        assert conn.execute(
            'SELECT COUNT(*) FROM cotes_historique WHERE cle = ?', (cle,),
        ).fetchone()[0] == 2


# --------------------------------------------------------------------------
# Ce que l'instantané publie du mouvement
# --------------------------------------------------------------------------

def test_le_mouvement_est_resume_pas_deverse(base):
    """La série brute monte à 83 points par match : la publier faisait peser
    l'instantané 9,4 Mo. L'instantané publie son résumé."""
    from engine.snapshot import _mouvement

    with base.connect() as conn:
        cle = _match(base, conn)
        for k in range(40):
            base.upsert_cotes(conn, cle, 'draftkings', '1X2',
                              [('1', 2.30 - k * 0.005), ('N', 3.40), ('2', 3.20)])
        m = _mouvement(conn, cle)
    assert m['releves'] >= 2
    assert m['p1_fin'] > m['p1_debut']        # le domicile s'est raffermi
    assert m['derive_points'] > 0
    assert set(m) == {
        'releves', 'premier_le', 'dernier_le', 'p1_debut', 'p1_fin',
        'pn_debut', 'pn_fin', 'p2_debut', 'p2_fin', 'derive_points',
    }


def test_aucun_mouvement_sans_deux_releves(base):
    from engine.snapshot import _mouvement

    with base.connect() as conn:
        cle = _match(base, conn)
        base.upsert_cotes(conn, cle, 'draftkings', '1X2',
                          [('1', 2.30), ('N', 3.40), ('2', 3.20)])
        assert _mouvement(conn, cle) is None


def test_une_cote_aberrante_est_ecartee_du_mouvement(base):
    """ESPN sert parfois des cotes en direct : 56,0 pour une victoire à
    domicile, relevées pendant le match. Elles ne doivent pas entrer."""
    from engine.snapshot import _mouvement

    with base.connect() as conn:
        cle = _match(base, conn)
        base.upsert_cotes(conn, cle, 'draftkings', '1X2',
                          [('1', 2.30), ('N', 3.40), ('2', 3.20)])
        base.upsert_cotes(conn, cle, 'draftkings', '1X2',
                          [('1', 1.00), ('N', 1.00), ('2', 1.00)])
        base.upsert_cotes(conn, cle, 'draftkings', '1X2',
                          [('1', 2.10), ('N', 3.50), ('2', 3.40)])
        m = _mouvement(conn, cle)
    assert m['releves'] == 2
