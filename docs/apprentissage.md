# Comment le moteur s'améliore

Le principe tient en une phrase : **le moteur compare ce qu'il a annoncé à ce
qui s'est produit, en tire des corrections, et ne les adopte que si elles font
mieux sur des matchs qu'il n'a pas servi à apprendre.**

## Le cycle

```text
prédiction figée ──► match joué ──► option réglée ──► archive CSV
                                                          │
                                                          ▼
                                             courbes candidates (passé)
                                                          │
                                        validation sur la période suivante
                                                          │
                                       gagne ? ──oui──► publiée
                                          │
                                          non ──► abandonnée sans bruit
```

Lancé à chaque `refresh`, ou à la main :

```bash
python -m engine bilan                      # où en est-on ?
python -m engine calibrer --essai           # mesurer sans publier
python -m engine calibrer                   # publier si meilleur
python -m engine calibrer --depuis-archive  # apprendre sur tout l'historique
```

## Ce qui est appris, et ce qui ne l'est pas

Seules les probabilités **calculées** par le modèle passent par la
calibration. Les probabilités lues directement sur le marché (le 1X2, et le
seuil de totaux réellement coté) n'y passent pas : les corriger reviendrait à
apprendre le bruit du bookmaker plutôt que les défauts du modèle.

Une courbe corrige une direction, l'autre s'en déduit par « 100 moins la
première ». C'est ce qui garantit que « plus de 3,5 buts » et « moins de
4 buts » somment toujours à 100 %. En v3.0, la correction par famille
cassait cette égalité de près de 8 points sur les cas extrêmes.

## Les trois garde-fous

Ils viennent tous d'erreurs réelles, constatées sur la journée 1 de Ligue des
champions.

### Validation chronologique

On apprend sur le passé, on teste sur la période suivante. Un tirage aléatoire
laisserait fuir l'information : deux options d'un même match tomberaient des
deux côtés de la coupure et la candidate paraîtrait meilleure qu'elle n'est.
La coupure est alignée sur une frontière de date, jamais au milieu d'une
journée.

### Volume minimal

Aucune courbe n'est produite sous **400 observations** pour un marché, ni sous
**1 200** pour une courbe propre à une ligue. Une case du découpage qui compte
moins de **40** observations est ignorée.

Le bilan partiel du 10 septembre signalait « 50 % de buts avant la pause, à
surveiller ». Six matchs de plus ramenaient le chiffre à 43 %, et l'archive de
6 666 matchs donnait 45,1 % — soit exactement l'hypothèse du moteur. Un réglage
fait sur ce signal l'aurait dégradé.

### Rétrécissement vers l'identité

Chaque point de courbe est tiré vers « ne rien corriger » proportionnellement
à son manque d'observations :

```text
correction = (n × observé + 60 × annoncé) / (n + 60)
```

Une case peu peuplée ne peut donc pas déplacer le moteur. Avec 60 observations,
la correction ne vaut que la moitié de ce que suggère l'écart brut ; avec 600,
elle en vaut 91 %.

La courbe est enfin rendue croissante : annoncer plus ne peut pas produire
moins.

## Savoir désapprendre

Une courbe héritée peut être fausse. La table livrée avec la v3.1 dégradait
15 marchés sur 18 sur les cinq grands championnats — elle avait été ajustée
sur un autre corpus.

Ne rien publier ne suffit pas à s'en défaire : le défaut reste actif. La
boucle arbitre donc, marché par marché, entre trois prétendantes — la courbe
apprise, celle en place, et l'absence de correction — sur une tranche de
validation intercalée entre l'apprentissage et le test. Quand l'absence de
correction gagne, une **courbe neutre explicite** est publiée, qui annule la
table héritée.

L'historique se découpe ainsi en trois : on apprend sur la première tranche,
on choisit sur la deuxième, on juge sur la troisième. Sans la tranche du
milieu, la courbe retenue serait jugée par les données qui l'ont choisie.

## Ce que l'arbitrage mesure : la calibration, pas le Brier

Le critère de départ était le score de Brier. Il était mal choisi.

Une dérive de trois points sur un pourcentage annoncé — « 74 % » qui vaut en
réalité 71 % — ne déplace le Brier que d'environ **0,0006**. Le Brier
mélange deux choses : la justesse des pourcentages et la capacité à
distinguer les matchs. La seconde domine, et elle ne dépend pas d'une courbe
de calibration. Résultat : le critère refusait des courbes qui ramenaient
l'erreur de calibration de 2,1 à 1,0 point, sous prétexte que le Brier ne
bougeait pas.

Or c'est le pourcentage affiché qui est le produit. L'arbitrage se fait donc
sur l'**erreur de calibration** — la moyenne, pondérée par les effectifs, de
l'écart entre annoncé et observé par tranche de 10 points :

```text
marché       ECE sans   ECE avec courbe   décision
+2.5             2.09              1.00   courbe
HC dom −2        1.69              0.79   courbe
+1.5             0.84              0.81   neutre  (gain sous la marge)
```

Deux garde-fous encadrent ce critère :

- une courbe n'est retenue que si elle gagne **plus de 0,05 point** d'erreur
  de calibration, sinon c'est du bruit ;
- elle est refusée si elle **dégrade le Brier** de plus de 0,001. Aplatir
  toutes les annonces sur la fréquence de base donnerait une calibration
  presque parfaite et une prévision sans aucune valeur : ce garde-fou existe
  pour cela, et `tests/test_apprentissage.py` le vérifie.

Les deux seuils ont été réglés sur douze mois d'archive et confirmés sur la
période suivante.

## Le mode challenger

Une calibration candidate ne remplace la version en place que si elle gagne
sur une tranche **hors échantillon**, contre la meilleure des deux
références : la version actuelle et l'absence totale de correction. Sinon le
rapport le dit et rien ne change.

C'est ce qui évite le piège de la v3.0, dont la correction par famille
dégradait le résultat : Brier 0,19138 avec correction contre 0,18999 sans.

```text
Observations réglées : 4 210
Apprentissage sur 2 946 options, test sur 1 264 options postérieures.
Score de Brier hors échantillon — sans correction : 0.20714 ;
version en place : 0.20355 ; candidate : 0.19822.
Nouvelle calibration publiée. Brier 0.19822 contre 0.20355 pour la
meilleure version en place
```

## La mémoire longue

La base SQLite vit dans le cache GitHub Actions et peut disparaître. L'archive,
elle, est versionnée :

```text
data/archive/observations-2026-09.csv   une ligne par option réglée
data/archive/resultats-2026-09.csv      une ligne par match joué
```

Un CSV par mois, en ajout seul, trié, sans doublon : lisible, comparable d'un
commit à l'autre, et assez compact pour git. `python -m engine archive stats`
en donne l'inventaire.

## Amorcer l'historique

Le moteur apprend au rythme des journées : environ 30 options réglées par
match, soit quelques milliers par mois. Pour disposer tout de suite de
plusieurs saisons de résultats :

```bash
python -m engine archive importer --saisons 2022,2023,2024,2025 \
                                  --ligues PL,LIGA,BL,SA,L1,LP
```

La source est football-data.co.uk, qui publie scores, scores à la pause et
cotes de clôture. Les noms d'équipes passent par la même normalisation que le
flux courant, donc les clés de match sont compatibles.

Pour les coupes d'Europe :

```bash
python -m engine archive importer-coupes \
       --saisons 2021-22,2022-23,2023-24,2024-25,2025-26
```

La source est `openfootball/champions-league`, qui donne les scores et les
scores à la pause **sans les cotes**. Ces rencontres ne peuvent donc pas
servir à la calibration ; elles alimentent les forces d'équipe et la mesure
des constantes du modèle. C'est sur ce corpus élargi — 7 668 matchs, huit
compétitions — qu'a été établi qu'aucune constante ne justifie d'être
déclinée par compétition. Voir [verification-v4.md](verification-v4.md),
résultat 6.

Deux limites à connaître. D'abord, cet import remplit `resultats`, pas
`predictions` : il donne au moteur une mémoire des **matchs**, pas de ses
propres annonces passées — la calibration, elle, ne s'apprend que sur des
prédictions réellement faites avant le coup d'envoi. Ensuite, certains réseaux
d'entreprise bloquent ce domaine ; la commande le signale alors au lieu
d'échouer, et l'archive se remplit simplement au fil des journées.

## Surveiller la dérive

```bash
python -m engine bilan
```

```text
Marché             n   annoncé  observé    écart   marge  significatif
+2.5             412     50.4%    61.1%    +10.7     4.8  oui
+3.5             412     31.0%    41.4%    +10.4     4.8  oui
MT +0.5          389     68.8%    72.0%     +3.2     4.6  non
```

La colonne « marge » est l'incertitude à deux écarts-types. Un écart plus petit
que sa marge est du bruit : `significatif` vaut alors `non`, et il ne faut rien
en conclure. C'est la colonne la plus importante du tableau.
