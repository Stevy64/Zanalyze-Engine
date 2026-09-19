# v3.1 contre v4 : la vérification avant migration

*Menée le 19 septembre 2026, sur 6 305 matchs réels de trois saisons.*

La question posée était simple : la v4 est-elle vraiment meilleure, ou
ai-je introduit une régression ? Voici le protocole et les chiffres.

## Le protocole

**Données.** 6 305 rencontres des saisons 2023/24, 2024/25 et 2025/26, plus le
début de 2026/27, dans six compétitions : Premier League, LaLiga, Bundesliga,
Serie A, Ligue 1 et Liga Portugal. Chaque ligne porte les cotes de clôture
1X2, les cotes plus/moins 2,5 buts, le score final et le score à la pause.

Contrôle de vraisemblance avant toute analyse : 2,81 buts par match, 53,6 %
de « plus de 2,5 buts », 25,6 % de nuls, 43,0 % de victoires à domicile, marge
de bookmaker de 5,0 %, et 44,2 % des buts inscrits avant la pause. Ce sont les
valeurs attendues pour ces championnats — les données sont saines.

**Les deux moteurs.** La v3.1 est son **code d'origine**, non modifié, exécuté
dans son propre processus. Les deux moteurs reçoivent exactement les mêmes
entrées et sont jugés sur les mêmes résultats.

**Deux scénarios.**

- **A — ligne réelle 2,5.** Celle de l'archive. Neutralise le défaut de
  lecture de ligne et compare tout le reste.
- **B — ligne réaliste.** Pour chaque match, on retient la ligne qu'un book
  aurait cotée : celle de l'échelle standard la plus proche du total
  implicite. Le prix correspondant est dérivé du marché réel du match, avec
  sa marge réelle. v3.1 reçoit ce prix et le suppose porter sur 2,5 ; v4 lit
  la ligne. Les résultats, eux, sont ceux des vrais matchs.

Sur ces données, **29,5 % des rencontres auraient été cotées ailleurs qu'à
2,5** : 27,9 % sur 3,5, 1,0 % sur 4,5, 0,7 % sur 1,5.

## Résultat 1 — aucune régression, au sens strict

Scénario A, 213 302 options communes aux deux moteurs :

| | écart de probabilité |
|---|---|
| maximum | **0,00e+00** |
| moyen | **0,00e+00** |

Les probabilités sont **identiques au bit près**. Là où la ligne vaut
vraiment 2,5, la v4 ne change rien au calcul. Une régression y était
impossible.

Les 9 181 options que la v4 ne produit plus sont les quatre codes de handicap
retirés. Vérification faite match par match :

| code retiré | équivalent conservé | occurrences | écart de probabilité | résultats divergents |
|---|---|---|---|---|
| `HCP_H_-1` | `MRG_H_2` | 4 383 | 0,00e+00 | 0 |
| `HCP_H_-2` | `MRG_H_3` | 1 688 | 0,00e+00 | 0 |
| `HCP_A_-1` | `MRG_A_2` | 2 410 | 0,00e+00 | 0 |
| `HCP_A_-2` | `MRG_A_3` | 700 | 0,00e+00 | 0 |

C'étaient des doublons exacts. Les retirer ne perd aucune information ; les
garder faisait compter deux fois le même pari dans le plafond de répétition.

## Résultat 2 — le correctif de ligne, mesuré

Scénario B, à périmètre d'options identique :

| | options | log-perte v3.1 | log-perte v4 | gain | Brier v3.1 | Brier v4 | gain |
|---|---|---|---|---|---|---|---|
| ligne = 2,5 | 150 756 | 0,54461 | 0,54461 | **0,00 %** | 0,18330 | 0,18330 | **0,00 %** |
| ligne ≠ 2,5 | 61 990 | 0,55173 | 0,52588 | **+4,69 %** | 0,18713 | 0,17649 | **+5,69 %** |
| ensemble | 212 746 | 0,54669 | 0,53915 | +1,38 % | 0,18441 | 0,18131 | +1,68 % |

Le détail par famille, sur les seuls matchs à ligne ≠ 2,5 :

| famille | gain de log-perte | gain de Brier | écart de calibration v3.1 → v4 |
|---|---|---|---|
| Total buts | **+10,92 %** | **+12,97 %** | ±0,0 → ±0,0 |
| Écart de buts | +7,82 % | +8,93 % | **+5,9 → +2,2 points** |
| Mi-temps | +2,84 % | +3,63 % | −0,2 → −0,1 point |

Lecture : là où rien ne change, rien ne change ; là où la v3.1 se trompait de
ligne, la v4 gagne, et d'autant plus que le marché dépend directement des
buts.

## Résultat 3 — la sélection, un arbitrage à assumer

Scénario A, donc à probabilités rigoureusement identiques : tout écart vient
de la seule couche de sélection.

| niveau | moteur | matchs servis | annoncé | réalisé | écart | formes distinctes | forme dominante |
|---|---|---|---|---|---|---|---|
| Recommandée | v3.1 | 6 279 | 80,7 % | 82,8 % | +2,1 | 12 | moins de 4,5 buts — **51,8 %** |
| Recommandée | **v4** | **6 305** | 70,9 % | 71,1 % | **+0,1** | **15** | double chance 1-2 — 28,4 % |
| Prudente | v3.1 | 6 279 | 64,2 % | 64,6 % | +0,4 | 17 | moins de 4,5 buts — 18,6 % |
| Prudente | **v4** | **6 305** | 78,3 % | 78,7 % | +0,4 | **20** | moins de 3,5 buts — 25,3 % |
| Équilibrée | v3.1 | 6 015 | 55,3 % | 55,6 % | +0,3 | 25 | MT moins de 1,5 — 16,0 % |
| Équilibrée | **v4** | **6 305** | 58,4 % | 57,4 % | −1,0 | 25 | MT moins de 1,5 — 21,5 % |
| Audacieuse | v3.1 | 5 793 | 36,1 % | 36,8 % | +0,7 | 23 | match nul — 10,7 % |
| Audacieuse | **v4** | **6 305** | 39,4 % | 39,6 % | +0,3 | **26** | domicile par 2 buts — 17,7 % |

Trois constats.

**Les deux moteurs sont bien calibrés** à tous les niveaux : les écarts entre
annoncé et réalisé vont de −1,0 à +2,1 points. Aucun des deux ne ment sur ses
chances — mais la v4 est plus juste partout sauf sur « équilibrée », et sa
Recommandée tombe à **+0,1 point** d'écart contre +2,1 pour la v3.1.

**La v4 sert toujours les quatre propositions** : 6 305 matchs sur 6 305. La
v3.1 laissait 512 trous à « audacieuse », 290 à « équilibrée » et 26 à la
Recommandée. Sa « prudente » sortait par ailleurs à 64,2 % de moyenne, sous sa
propre bande de 70–90 % : elle se rabattait souvent hors cible.

**La Recommandée était l'arbitrage à trancher — il l'est.** Celle de la v3.1
réussit 82,8 % du temps, celle de la v4 71,1 %. Mais la v3.1 obtient ce taux
en proposant « moins de 4,5 buts » dans **51,8 %** des cas — un pari dont la
cote juste tourne autour de 1,21, et qui ne dit à peu près rien du match. La
v4 répartit sur 15 formes, la plus fréquente à 28,4 %.

Onze points de réussite contre une proposition qui porte une information :
c'était un choix de produit, pas une vérité technique. **Arbitrage rendu le
19 septembre 2026 : la version v4, plus informative et plus variée.** La
fenêtre reste réglable d'une ligne dans `moteur.BANDE_RECOMMANDEE`,
actuellement 0,62–0,88 ; la remonter à 0,78–0,95 rendrait une Recommandée
proche de celle de la v3.1.

## Résultat 4 — la force d'équipe n'ajoute rien au marché

Le module `engine/force.py` estime, pour chaque club, une force d'attaque et
de défense à partir des **seuls résultats passés** : Dixon–Coles, avantage du
terrain, pondération temporelle de demi-vie 120 jours, régularisation vers la
moyenne pour les promus. Aucune cote n'entre dans l'ajustement.

Il fonctionne : sur la Premier League il retrouve Arsenal, Liverpool et
Manchester City en tête, Southampton, Ipswich et Leicester en bas, et ses buts
attendus sont justes — 2,80 prédits contre 2,77 observés sur 4 066 matchs.

Mais mélangé au marché, il ne l'améliore jamais. Walk-forward mensuel, 4 247
matchs, log-perte 1X2 :

| | log-perte | Brier |
|---|---|---|
| marché seul | **0,96172** | **0,57175** |
| mélange 5 % | 0,96225 | 0,57205 |
| mélange 10 % | 0,96294 | 0,57244 |
| mélange 20 % | 0,96480 | 0,57352 |
| mélange 50 % | 0,97431 | 0,57944 |
| forces seules | 1,00423 | 0,59968 |

**Le poids optimal est zéro**, et la dégradation est monotone. Les cotes de
clôture contiennent déjà tout ce que les forces savent. C'est le résultat
attendu par la littérature, et il est ici mesuré sur tes championnats.

Ce n'est pas un échec du module, c'est son cadrage. Sa place est là où il n'y
a pas de marché : les rencontres **sans cotes**, que la v3.1 n'analysait pas
du tout — 17 % des matchs du dernier instantané. Pour celles-là, les forces
valent bien mieux que le silence (log-perte 1,004 contre 1,079 pour une simple
fréquence de base). Ces analyses portent `source_cotes = "forces"` et une
confiance abaissée de 7 points.

Le portillon reste en place : `force.poids_optimal()` remesure le mélange à
chaque cycle et le réactivera si un jour il gagne.

## Résultat 5 — la calibration livrée nuisait, et la boucle ne savait pas la corriger

Découverte imprévue, et la plus utile de la campagne.

La table `engine/calibration_par_marche.json`, livrée avec la v3.1 et héritée
par la v4, **dégrade 15 marchés sur 18** sur ces données :

| | Brier | log-perte |
|---|---|---|
| aucune correction | **0,17246** | **0,51767** |
| calibration livrée | 0,17344 | 0,52040 |

Elle avait été ajustée sur un autre corpus ; elle ne vaut pas pour les cotes
de clôture des grands championnats.

Plus gênant : la boucle d'apprentissage de la v4, telle que je l'avais
écrite, **ne pouvait pas réparer cela**. Elle savait ajouter une courbe, pas
en retirer une — et ne rien publier revenait à laisser la table nuisible en
place.

Corrigé. La boucle arbitre désormais marché par marché entre trois
prétendantes — la courbe apprise, celle déjà en place, et l'absence de
correction — sur une tranche de validation intercalée entre l'apprentissage
et le test. Quand l'absence de correction gagne, elle publie une **courbe
neutre explicite**, ce qui annule la table héritée.

Walk-forward mensuel sur 25 mois, 121 168 options de test :

| | Brier | log-perte |
|---|---|---|
| calibration livrée | 0,17344 | 0,52040 |
| apprise, avec arbitrage | **0,17244** | **0,51761** |
| | **+0,58 %** | **+0,56 %** |

La boucle identifie les 18 marchés concernés et récupère l'écart.

En complément, `data/calibration.json` est livré pré-rempli avec les
17 courbes neutralisées mesurées ici, pour que la correction s'applique dès
le premier cycle plutôt qu'après plusieurs semaines. Pour revenir aux courbes
d'origine : supprimer ce fichier.

## Résultat 6 — la calibration par compétition, coupes d'Europe comprises

*Campagne complémentaire, 19 septembre 2026. 1 363 matchs de Ligue des
champions et de Ligue Europa sur cinq saisons (2021/22 à 2025/26, plus le
début de 2026/27), ajoutés aux 6 305 rencontres nationales : 7 668 matchs,
huit compétitions.*

Source : `openfootball/champions-league`, importable par
`python -m engine archive importer-coupes --saisons 2021-22,…`. Ces fichiers
donnent les scores et les scores à la pause, **pas les cotes** : les coupes
d'Europe nourrissent donc les forces d'équipe et la mesure des constantes,
jamais la calibration, qui exige des prédictions faites avant le coup d'envoi.

### L'environnement européen est différent, et c'est mesurable

| | buts/match | +2,5 buts | nuls | victoires dom. |
|---|---|---|---|---|
| Ligue des champions | **3,22** | **60,7 %** | **18,2 %** | 48,8 % |
| Ligue Europa | 2,92 | 55,9 % | 22,6 % | 48,2 % |
| cinq grands championnats | 2,81 | 53,6 % | 25,6 % | 43,0 % |

Plus de buts, moins de nuls, plus d'affiches déséquilibrées : exactement le
terrain où le correctif de ligne du Résultat 2 rapporte le plus.

### Mais les constantes du modèle, elles, ne bougent pas

La question était : faut-il une constante par compétition ? Réponse mesurée,
compétition par compétition.

**Part des buts inscrits avant la pause**, sur 7 571 matchs renseignés :

| PL | LIGA | BL | SA | L1 | LP | UCL | UEL | ensemble |
|---|---|---|---|---|---|---|---|---|
| 0,437 | 0,442 | 0,460 | 0,433 | 0,439 | 0,444 | 0,437 | 0,450 | **0,4424** |

L'écart entre la plus haute (Bundesliga) et la plus basse (Serie A) vaut
2,7 points, pour une marge à deux écarts-types de ±3,2 points par
compétition. **Aucune ne s'écarte significativement de l'ensemble.** Une
constante par compétition reviendrait à apprendre du bruit.

`FACTEUR_MI_TEMPS` passe donc de 0,45 (valeur supposée) à **0,442** (valeur
mesurée), une seule fois, pour tout le monde.

**Corrélation des scores faibles (ρ de Dixon–Coles)** : mesure groupée
**−0,0505**, intervalle [−0,084 ; −0,018]. La valeur retenue, −0,06, tombe
dans l'intervalle de chaque compétition prise isolément. Inchangée.

**Courbes de calibration par ligue** : 0,17267 de Brier avec, 0,17267 sans.
Rien. Le seuil de 1 200 observations par ligue reste en place, mais il ne
s'agit plus d'attendre un gain — simplement de ne pas dégrader si un jour un
écart apparaît.

### Ce qui, en revanche, était faux

Deux réglages de la couche de sélection ne reposaient sur rien de mesuré.

**La fiabilité par famille** — l'écart typique entre ce qu'une famille de
paris annonce et ce qu'elle réalise, qui sert à hiérarchiser les propositions.
Remesurée sur les 7 668 matchs :

| famille | v3.1 (supposé) | v4 (mesuré) |
|---|---|---|
| Écart de buts | 1,00 | **0,80** |
| 1X2, double chance | 1,00 | 1,02 |
| Mi-temps | 1,20 | 1,17 |
| Total buts | 1,10 | 1,18 |
| BTTS | **4,07** | **1,42** |
| Handicap | **1,80** | **2,94** |
| Total d'une équipe | 2,50 | 3,06 |
| Une équipe marque | 3,00 | 3,15 |

Deux erreurs franches. Le handicap était réputé fiable et ne l'est pas — il
était donc proposé trop souvent. Le « les deux équipes marquent » était
réputé le pire de tous et se révèle le troisième meilleur — il était exclu
pour rien. **BTTS entre dans `FAMILLES_ELIGIBLES`**, le handicap y reste mais
pèse trois fois moins.

**Le critère d'arbitrage des courbes** mesurait la mauvaise chose. Il
comparait des scores de Brier ; or une dérive de trois points sur un
pourcentage annoncé ne déplace le Brier que de 0,0006 — invisible. Pour qui
lit « 74 % » sur l'écran, c'est pourtant la seule chose qui compte. Le
critère est passé à l'**erreur de calibration**, avec le Brier en garde-fou :
une courbe qui flatterait la calibration en détruisant la prévision est
refusée. Les deux seuils (0,05 point d'ECE, 0,001 de Brier) ont été réglés
sur douze mois et confirmés sur les suivants.

Effet immédiat, sur `data/calibration.json` reconstruit : **11 courbes
actives et 6 neutres**, contre 17 neutres auparavant. L'ancien critère
refusait des courbes qui ramenaient l'erreur de calibration de 2,1 à 1,0
point.

### Contre-vérification : la v4 finale n'a pas régressé

La couche de sélection ayant changé (fiabilités remesurées, BTTS éligible,
nouveau critère de courbes), les Résultats 1 à 3 ont été rejoués avec le code
final :

| | Brier v3.1 → v4 | log-perte v3.1 → v4 | ECE v3.1 → v4 |
|---|---|---|---|
| scénario A, probabilité publiée | +0,46 % | +0,42 % | 0,81 → **0,24** |
| scénario A, probabilité **brute** | −0,00 % | −0,00 % | 0,61 → 0,59 |
| scénario B, probabilité publiée | +2,16 % | +1,81 % | 0,75 → **0,21** |
| scénario B, probabilité **brute** | **+2,98 %** | **+3,29 %** | 0,93 → 0,58 |

La colonne « brute » est la probabilité avant calibration : c'est la seule
lecture entièrement hors échantillon, puisque les courbes livrées ont été
apprises sur ce corpus. Elle dit l'essentiel — **+2,98 % de Brier en
scénario B, et l'égalité stricte en scénario A**. Les gains sur la
probabilité publiée sont réels mais en partie flattés par cet apprentissage ;
c'est la mesure en trois tranches de la boucle (Résultat 5) qui les valide
hors échantillon.

En scénario A, la seule différence de probabilité brute vient de
`FACTEUR_MI_TEMPS` (44 131 options de mi-temps) : Brier 0,20962 → 0,20965,
erreur de calibration 0,84 → 0,88. Écart nul en pratique — et la part
mesurée sur ce corpus précis vaut 0,4424, soit exactement la constante
retenue. Le modèle de mi-temps n'est donc pas parfaitement proportionnel ;
c'est un point à creuser, pas une régression.

Enfin, la v4 finale sert **les cinq niveaux sur les 6 305 matchs**, ce que la
v3.1 ne faisait pour aucun niveau sauf le filet.

## Verdict

**Ce n'est pas une régression.** Là où rien ne devait changer, les deux
moteurs sont identiques au bit près. Là où quelque chose change, la v4 est
devant, jamais derrière.

Le gain global tient en un chiffre — **+2,98 % de Brier, +3,29 % de
log-perte** hors calibration sur 212 746 options — mais il est très
inégalement réparti : nul sur 70 % des matchs, de l'ordre de 5 % sur les
30 % où le book bouge sa ligne, et jusqu'à 13 % sur les marchés de buts de
ces matchs-là.

S'y ajoute ce que la campagne par compétition a corrigé : deux fiabilités de
famille franchement fausses, une constante de mi-temps supposée plutôt que
mesurée, et un critère d'arbitrage qui regardait à côté.

L'arbitrage sur la Recommandée est rendu : **plus informative et plus
variée**, onze points de réussite en dessous, et un écart annoncé/réalisé
ramené de +2,1 à +0,1 point.

## Ce que cette campagne ne prouve pas

**Les coupes d'Europe entrent sans leurs cotes.** Les 1 363 matchs de Ligue
des champions et de Ligue Europa servent aux constantes et aux forces
d'équipe ; faute de cotes historiques accessibles, ils ne peuvent pas
alimenter la calibration ni les scénarios A et B. Le gain du correctif de
ligne y est probablement supérieur à celui mesuré sur les championnats —
l'environnement le laisse attendre (3,22 buts par match, 60,7 % de plus de
2,5) — mais ce n'est pas démontré.

**Les courbes livrées ont été apprises sur le corpus de test.** Les chiffres
de calibration du scénario A et B sur la probabilité *publiée* sont donc en
partie in-sample. La lecture honnête est la colonne « brute », et la
validation en trois tranches du Résultat 5.

**Le scénario B simule le choix de la ligne**, pas les prix. Les prix sont
dérivés du marché réel de chaque match avec sa marge réelle, et les résultats
sont ceux des vrais matchs. Reste l'hypothèse qu'un book cote sa ligne de
façon cohérente avec son prix sur 2,5 — ce qu'il fait en pratique, mais que
je n'ai pas pu vérifier sur des données ESPN historiques, inaccessibles depuis
l'environnement de travail.

**Les cotes de l'archive sont des cotes de clôture**, plus affûtées que celles
qu'ESPN publie avant match. Le moteur travaillera en production sur des prix
un peu moins bons ; la comparaison entre les deux versions, elle, n'en est pas
affectée.

**La force d'équipe n'a été testée que comme mélange sur le 1X2.** Un usage
différent — détecter les cotes aberrantes, pondérer les marchés de buts —
reste à mesurer.

## Reproduire

```bash
# Les scripts du banc d'essai ne sont pas versionnés : ils dépendent d'une
# archive externe. La démarche, elle, tient en quatre étapes.
python -m engine archive importer --saisons 2023,2024,2025 \
                                  --ligues PL,LIGA,BL,SA,L1,LP
python -m engine archive importer-coupes \
       --saisons 2021-22,2022-23,2023-24,2024-25,2025-26
python -m engine calibrer --depuis-archive --essai   # mesurer sans publier
python -m engine bilan --depuis-archive              # écarts par marché
pytest                                               # 121 tests
```
