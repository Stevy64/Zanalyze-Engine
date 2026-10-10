"""Contrat de calcul commun à Engine et à la web app, sans accès aux données."""
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from engine.moteur import VERSION_MOTEUR, AnalyseInvalide, analyser, classer_journee

router = APIRouter()

class MatchCotes(BaseModel):
    c1: float = Field(..., ge=1.01, le=100)
    cn: float = Field(..., ge=1.01, le=100)
    c2: float = Field(..., ge=1.01, le=100)
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

    @model_validator(mode='before')
    @classmethod
    def anciens_totaux(cls, values):
        # Seuls les noms historiques o25/u25 portent explicitement la ligne.
        if not isinstance(values, dict):
            return values
        values = dict(values)
        legacy = values.get('o25') is not None or values.get('u25') is not None
        if legacy:
            if values.get('over') is not None or values.get('under') is not None:
                raise ValueError('Ne pas mélanger o25/u25 avec over/under.')
            if values.get('ligne') not in (None, 2.5):
                raise ValueError('o25/u25 désignent exclusivement la ligne 2.5.')
            values['over'] = values.pop('o25', None)
            values['under'] = values.pop('u25', None)
            values['ligne'] = 2.5
        return values

    @model_validator(mode='after')
    def verifier_totaux(self):
        if (self.over is None) != (self.under is None):
            raise ValueError('Fournir ensemble over et under.')
        if self.over is not None and self.ligne is None:
            raise ValueError('Préciser la ligne réellement cotée pour les totaux.')
        if self.ligne is not None and self.over is None:
            raise ValueError('Une ligne nécessite les deux cotes de totaux.')
        return self

    def totaux(self):
        return None if self.over is None else (self.over, self.under, self.ligne)


class AnalyserRequest(BaseModel):
    matchs: list[MatchCotes]


class AnalyserResponse(BaseModel):
    version_moteur: str
    analyses: list[dict[str, Any]]


@router.post('/v1/analyser', response_model=AnalyserResponse)
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


@router.post('/v1/analyser-un')
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


