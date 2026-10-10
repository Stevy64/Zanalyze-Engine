"""Entrées historiques et explicites partagent un seul contrat."""
import pytest
from pydantic import ValidationError
from engine.api_analyse import MatchCotes, AnalyserRequest, post_analyser
from engine.moteur import VERSION_MOTEUR


def test_ancien_contrat_ou25_converti_explicitement():
    m = MatchCotes(c1=1.9, cn=3.5, c2=4., o25=1.9, u25=1.9)
    assert m.totaux() == (1.9, 1.9, 2.5)


@pytest.mark.parametrize('extra', [{'over':1.9, 'under':1.9}, {'o25':1.9}, {'ligne':3.5}, {'o25':1.9, 'u25':1.9, 'ligne':3.5}])
def test_totaux_ambigus_refuses(extra):
    with pytest.raises(ValidationError):
        MatchCotes(c1=1.9, cn=3.5, c2=4., **extra)


def test_ligne_et_reference_conservees():
    result = post_analyser(AnalyserRequest(matchs=[{'c1':1.9,'cn':3.5,'c2':4.,'over':1.9,'under':1.9,'ligne':3.5,'ref':'test'}]))
    assert result.version_moteur == VERSION_MOTEUR
    assert result.analyses[0]['ligne_totaux'] == 3.5
    assert result.analyses[0]['ref'] == 'test'


def test_version_du_paquet_identique_au_moteur():
    from pathlib import Path
    import re
    metadata = (Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text(encoding='utf-8')
    version = re.search(r'^version = "([^"]+)"', metadata, re.MULTILINE).group(1)
    assert version == VERSION_MOTEUR
