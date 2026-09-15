"""
API HTTP (optionnelle). La PWA PythonAnywhere n’a pas besoin de l’appeler :
elle lit le JSON Git. L’API sert le VPS / le debug local (/docs).

Auth : si ENGINE_TOKEN est vide, tout est ouvert (dev). Sinon Bearer ou X-Engine-Token.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from engine.moteur import (
    VERSION_MOTEUR,
    AnalyseInvalide,
    analyser,
    classer_journee,
)
from engine.pipeline import refresh, sync
from engine.snapshot import SNAPSHOT_VERSION, exporter_snapshot

app = FastAPI(
    title='Zanalyze Engine',
    version=VERSION_MOTEUR,
    docs_url='/docs',
)


def _check_token(authorization: str | None = Header(default=None),
                 x_engine_token: str | None = Header(default=None, alias='X-Engine-Token')) -> None:
    expected = (os.environ.get('ENGINE_TOKEN') or '').strip()
    if not expected:
        return
    got = (x_engine_token or '').strip()
    if not got and authorization and authorization.lower().startswith('bearer '):
        got = authorization[7:].strip()
    if got != expected:
        raise HTTPException(401, 'token moteur invalide')


class MatchCotes(BaseModel):
    c1: float = Field(..., ge=1.01, le=100)
    cn: float = Field(..., ge=1.01, le=100)
    c2: float = Field(..., ge=1.01, le=100)
    o25: float | None = Field(None, ge=1.01, le=100)
    u25: float | None = Field(None, ge=1.01, le=100)
    nom_dom: str = 'Domicile'
    nom_ext: str = 'Extérieur'
    ref: str | None = None


class AnalyserRequest(BaseModel):
    matchs: list[MatchCotes]


class AnalyserResponse(BaseModel):
    version_moteur: str
    analyses: list[dict[str, Any]]


class SyncRequest(BaseModel):
    pages: int = Field(1, ge=0, le=5)
    passes: int = Field(1, ge=0, le=5)
    contexte: bool = False
    calculer: bool = True
    jours_snapshot: int = Field(21, ge=1, le=60)


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
        ou = (m.o25, m.u25) if m.o25 is not None and m.u25 is not None else None
        try:
            payload = analyser((m.c1, m.cn, m.c2), ou, m.nom_dom, m.nom_ext)
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
    ou = (m.o25, m.u25) if m.o25 is not None and m.u25 is not None else None
    try:
        payload = analyser((m.c1, m.cn, m.c2), ou, m.nom_dom, m.nom_ext)
    except AnalyseInvalide as e:
        raise HTTPException(422, str(e)) from e
    if m.ref:
        payload['ref'] = m.ref
    return payload


@app.post('/v1/sync')
def post_sync(body: SyncRequest, _: None = Depends(_check_token)) -> dict[str, Any]:
    if body.calculer:
        return refresh(
            pages=body.pages,
            passes=body.passes,
            avec_contexte=body.contexte,
            jours_snapshot=body.jours_snapshot,
        )
    stats = sync(pages=body.pages, passes=body.passes, avec_contexte=body.contexte)
    return {'sync': stats}


@app.get('/v1/snapshot')
def get_snapshot(jours: int = 21, _: None = Depends(_check_token)) -> dict[str, Any]:
    return exporter_snapshot(jours=jours)
