def evaluer(code, fh, fa, hh=None, ha=None):
    """Une option est-elle gagnante ?

    fh, fa : buts finaux domicile / extérieur.
    hh, ha : buts à la mi-temps, facultatifs.
    Retourne True (gagné), False (perdu) ou None (impossible à trancher).
    """
    if fh is None or fa is None:
        return None
    total, ecart = fh + fa, fh - fa

    if code == '1X2_1': return ecart > 0
    if code == '1X2_N': return ecart == 0
    if code == '1X2_2': return ecart < 0
    if code == 'DC_1X': return ecart >= 0
    if code == 'DC_X2': return ecart <= 0
    if code == 'DC_12': return ecart != 0

    if code.startswith('OV_'): return total > float(code[3:])
    if code.startswith('UN_'): return total < float(code[3:])

    if code.startswith('MRG_'):
        _, cote, n = code.split('_')
        n = int(n)
        return (ecart >= n) if cote == 'H' else (-ecart >= n)

    if code.startswith('HCP_'):
        # +1 : ne perd pas de plus d’un but (ecart + 1 >= 0).
        # -k : gagne par k+1 buts ou plus (ecart > k).
        _, cote, h_s = code.split('_')
        h = int(h_s)
        if h >= 0:
            return (ecart + h >= 0) if cote == 'H' else (-ecart + h >= 0)
        need = -h
        return (ecart > need) if cote == 'H' else (-ecart > need)

    if code.startswith('HT_'):
        if hh is None or ha is None: return None
        he, ht = hh - ha, hh + ha
        if code == 'HT_1': return he > 0
        if code == 'HT_N': return he == 0
        if code == 'HT_2': return he < 0
        if code.startswith('HT_OV_'): return ht > float(code[6:])
        if code.startswith('HT_UN_'): return ht < float(code[6:])

    if code == 'BTTS_O': return fh > 0 and fa > 0
    if code == 'BTTS_N': return not (fh > 0 and fa > 0)
    if code == 'DOM_MARQUE': return fh > 0
    if code == 'EXT_MARQUE': return fa > 0
    if code == 'DOM_2PLUS': return fh >= 2
    if code == 'EXT_2PLUS': return fa >= 2

    raise ValueError(f"code inconnu : {code}")
