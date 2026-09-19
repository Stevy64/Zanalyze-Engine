"""
API HTTP (optionnelle). La PWA n'en a pas besoin : elle lit le JSON publié
par GitHub Actions. Cette API sert le débogage local et un éventuel VPS.

Auth : si ENGINE_TOKEN est vide, tout est ouvert (développement). Sinon,
en-tête Bearer ou X-Engine-Token.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from engine.moteur import VERSION_MOTEUR, AnalyseInvalide, analyser, classer_journee
from engine.snapshot import SNAPSHOT_VERSION, exporter_snapshot

app = FastAPI(title='Zanalyze Engine', version=VERSION_MOTEUR, docs_url='/docs')


def _check_token(
    authorization: str | None = Header(default=None),
    x_engine_token: str | None = Header(default=None, alias='X-Engine-Token'),
) -> None:
    attendu = (os.environ.get('ENGINE_TOKEN') or '').strip()
    if not attendu:
        return
    recu = (x_engine_token or '').strip()
    if not recu and authorization and authorization.lower().startswith('bearer '):
        recu = authorization[7:].strip()
    if recu != attendu:
        raise HTTPException(401, 'token moteur invalide')


class MatchCotes(BaseModel):
    c1: float = Field(..., ge=1.01, le=1000)
    cn: float = Field(..., ge=1.01, le=1000)
    c2: float = Field(..., ge=1.01, le=1000)
    over: float | None = Field(None, ge=1.01, le=100)
    under: float | None = Field(None, ge=1.01, le=100)
    ligne: float | None = Field(
        None, ge=0.5, le=8.5,
        description='Ligne réellement cotée pour les totaux (2.5, 3.5, 4.5…). '
                    'Obligatoire dès que over et under sont fournis.',
    )
    nom_dom: str = 'Domicile'
    nom_ext: str = 'Extérieur'
    ligue: str | None = None
    ref: str | None = None

    def totaux(self):
        if self.over is None or self.under is None:
            return None
        if self.ligne is None:
            raise HTTPException(
                422,
                'Une cote de totaux sans sa ligne est inexploitable : '
                'préciser « ligne ».',
            )
        return (self.over, self.under, self.ligne)


class AnalyserRequest(BaseModel):
    matchs: list[MatchCotes]


class AnalyserResponse(BaseModel):
    version_moteur: str
    analyses: list[dict[str, Any]]


class RefreshRequest(BaseModel):
    jours_snapshot: int = Field(21, ge=1, le=60)
    recalibrer: bool = True
    archiver: bool = True


@app.get('/health')
def health() -> dict[str, str]:
    return {
        'status': 'ok',
        'service': 'zanalyze-engine',
        'version': VERSION_MOTEUR,
        'snapshot_version': str(SNAPSHOT_VERSION),
    }


@app.post('/v1/analyser', response_model=AnalyserResponse)
def post_analyser(body: AnalyserRequest) -> AnalyserResponse:
    if not body.matchs:
        raise HTTPException(400, 'matchs vide')
    analyses: list[dict[str, Any]] = []
    for m in body.matchs:
        try:
            payload = analyser(
                (m.c1, m.cn, m.c2), m.totaux(), m.nom_dom, m.nom_ext, ligue=m.ligue,
            )
        except AnalyseInvalide:
            continue
        if m.ref:
            payload['ref'] = m.ref
        analyses.append(payload)
    if not analyses:
        raise HTTPException(422, 'aucune analyse valide')
    classer_journee(analyses)
    return AnalyserResponse(version_moteur=VERSION_MOTEUR, analyses=analyses)


@app.post('/v1/analyser-un')
def post_analyser_un(m: MatchCotes) -> dict[str, Any]:
    try:
        payload = analyser(
            (m.c1, m.cn, m.c2), m.totaux(), m.nom_dom, m.nom_ext, ligue=m.ligue,
        )
    except AnalyseInvalide as e:
        raise HTTPException(422, str(e)) from e
    if m.ref:
        payload['ref'] = m.ref
    return payload


@app.post('/v1/refresh')
def post_refresh(body: RefreshRequest, _: None = Depends(_check_token)) -> dict[str, Any]:
    from engine.pipeline import refresh
    return refresh(
        jours_snapshot=body.jours_snapshot,
        recalibrer=body.recalibrer,
        archiver=body.archiver,
    )


@app.get('/v1/snapshot')
def get_snapshot(jours: int = 21, _: None = Depends(_check_token)) -> dict[str, Any]:
    return exporter_snapshot(jours=jours)


@app.get('/v1/bilan')
def get_bilan(_: None = Depends(_check_token)) -> dict[str, Any]:
    """Écart annoncé / observé par marché, avec sa marge d'erreur."""
    from engine.apprentissage import bilan_par_marche, collecter_observations
    from engine.store import connect, init_db
    init_db()
    with connect() as conn:
        observations = collecter_observations(conn)
    return {'observations': len(observations), 'marches': bilan_par_marche(observations)}
