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

from engine.moteur import VERSION_MOTEUR
from engine.snapshot import SNAPSHOT_VERSION, exporter_snapshot

from engine.api_analyse import router

app = FastAPI(title='Zanalyze Engine', version=VERSION_MOTEUR, docs_url='/docs')
app.include_router(router)


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
