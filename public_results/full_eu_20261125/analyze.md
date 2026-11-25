# Analyse du batch `full_eu_20261125`

Collecte du 25 novembre 2026, de 09:16 à 16:44 UTC. Elle porte sur 149 sites, en 8 conditions, avec une sortie VPN en Allemagne. Ce document résume les résultats et les compare au batch du 22 novembre (`public_results/full_eu_20261122`). Il signale aussi ce qu'il ne faut pas en conclure.

Tous les chiffres viennent des fichiers de ce dossier : surtout `purpose_results.json`, `paired_uncertainty.json` et `consent_coverage.csv`. On peut les régénérer avec `python scripts/reproduce_results.py --batch full_eu_20261125 --export` à partir de la base privée (section 9).

## Résumé

1. **Le fingerprinting est surtout défensif.** 66 des 97 sites accessibles exécutent du fingerprinting. Sur 285 paires (domaine du script, technique) :
   - 62 % relèvent de l'anti-fraude ou de la sécurité ;
   - 11 % de la publicité ou de l'analytics ;
   - 27 % restent de finalité inconnue.

   Le fingerprinting de **tracking** (publicité ou analytics) concerne 18 sites et 29 paires ; la borne haute est de 30 sites et 67 paires.
2. **Refuser les cookies ne retire pas le fingerprinting de tracking déjà présent.** Sur les sites où le refus est vérifié, 13 paires de tracking sur 13 survivent au refus seul, et 14 sur 15 au refus + GPC. 8 des 31 à 34 sites au refus vérifié exécutent encore un script de tracking après le refus.
3. **GPC seul n'a pas d'effet mesurable.** Le nombre de tiers ne change pas : 0 domaine, IC95 [0 ; 0]. Le refus avec ou sans GPC donne le même résultat : 8 sites avec tracking dans les deux cas.
4. **Accepter ajoute surtout des tiers.** L'acceptation ajoute en médiane +50,5 domaines tiers par site (IC95 [40 ; 61,5]) et 22 nouvelles paires de tracking. Toutes les paires de tracking de la baseline restent présentes.
5. **Les bloqueurs sont les seuls à réduire nettement le tracking.**
   - uBlock Origin Lite retire 86 % des paires de tracking (−86 pts, IC95 [−97 ; −72]).
   - Privacy Badger en retire 45 % (−45 pts, IC95 [−68 ; −19]).
   - Les deux retirent aussi une partie des scripts de sécurité : −16 et −13 pts, surtout HUMAN/PerimeterX.
6. **Les résultats reproduisent ceux du 22 novembre** : même prévalence, même effet des bloqueurs, même survie au refus. Le correctif du clic de consentement fait passer les acceptations vérifiées de 44 à 56.

## 1. Ce qui a été mesuré

| Paramètre | Valeur |
|---|---|
| Sites | 149 : 50 news, 50 e-commerce, 37 social, 12 affiliation (même liste que le 22/11) |
| Réseau | OpenVPN VPNBook `de220`, sortie vérifiée en Allemagne, même IP du début à la fin |
| Navigateur | Chromium via Playwright 1.63, headful (xvfb), langue en-US, fuseau **Europe/Berlin** aligné sur la sortie, graine 0 |
| Visite | Accueil, consentement, rechargement de l'accueil, 1 sous-page ; 5 s d'attente par page ; 2 workers |
| Détection | API de fingerprinting instrumentées (canvas, canvas_font, webgl, audio, webrtc, énumération d'attributs) |

Les 8 conditions :

| Condition | Ce qui change | Heure (UTC) |
|---|---|---|
| `baseline` | rien | 09:16–09:39 |
| `baseline2` | rien (mesure répétée = plancher de bruit) | 09:39–10:01 |
| `ubol` | uBlock Origin Lite | 10:01–10:31 |
| `pbadger` | Privacy Badger | 10:31–12:37 |
| `optout` | refus des cookies + GPC | 12:37–14:20 |
| `reject` | refus des cookies seul | 14:20–14:59 |
| `gpc` | signal GPC seul | 14:59–15:25 |
| `accept` | acceptation des cookies | 15:25–16:44 |

**Nouveautés par rapport au batch du 22/11 :**
- Les conditions `reject` et `gpc` séparent les deux signaux combinés dans `optout`.
- Les boutons de consentement sont trouvés par leur texte visible.
- Les murs « consentir ou payer » sont détectés.
- Les corps des requêtes POST sont capturés.
- Les cookies et les URL ne sont plus tronqués.
- Le fuseau horaire est aligné sur la sortie VPN.
- Les erreurs réseau ont droit à une seule nouvelle tentative.

## 2. Collecte

| Condition | Sites mesurés | Sites comparables* |
|---|---|---|
| baseline | 97 / 149 | 97 |
| baseline2 | 96 / 97 | 96 |
| ubol | 96 / 97 | 96 |
| pbadger | 95 / 97 | 95 |
| optout | 94 / 97 | 34 (refus vérifié) |
| reject | 94 / 97 | 31 (refus vérifié) |
| gpc | 95 / 97 | 95 |
| accept | 97 / 97 | 56 (acceptation vérifiée) |

\* Une condition de consentement n'est comparée que sur les sites où le choix enregistré a été vérifié (section 5).

**Pourquoi la baseline n'a que 97 sites.** Les 52 sites en échec sont des blocages anti-bot de l'IP de datacenter du VPN : 44 HTTP 403, 4 HTTP 429, 2 HTTP 401, 1 HTTP 400 et 1 erreur HTTP réseau. L'e-commerce est le plus touché : 23 sites accessibles sur 50, contre 43 sur 50 pour les news, 25 sur 37 pour le social et 6 sur 12 pour l'affiliation. Les autres conditions ne visitent que les 97 sites accessibles en baseline.

**Incidents** (détail dans `vpn_provenance.json`) :
- **Coupure réseau pendant `accept`.** À 15:51, le Wi-Fi de la machine a décroché. Le réseau suivant bloquait OpenVPN.
  - Les 25 derniers sites ont échoué et le script s'est arrêté.
  - La reprise s'est faite avec `--modes accept`, sur le même réseau, le même profil et la même IP de sortie ; le manifeste est inchangé.
  - Le crawler a remesuré les 25 sites et `accept` finit à 97/97.
  - Un seul de ces 25 sites (linkedin.com, HTTP 404 sur une sous-page) n'aurait pas été réessayé dans une autre condition.
- **Deux suspensions de la machine** : 73 min pendant `pbadger`, 65 min pendant `optout`.
  - La pause d'`optout` est tombée entre deux sites.
  - Dans `pbadger`, une visite a été figée en cours de mesure : la sous-page de foodnetwork.com.
  - **Test de sensibilité :** sans ce site, les résultats de `pbadger` sont identiques, car foodnetwork.com n'a aucune paire de fingerprinting.
  - Les autres mises en veille n'étaient que des extinctions d'écran : aucun trou d'activité, et les visites ont la même durée et le même volume qu'en dehors.

## 3. Prévalence (baseline, 97 sites)

| Mesure | Valeur |
|---|---|
| Sites avec fingerprinting | 66 / 97 (68 %) |
| Avec fingerprinting de sécurité avant toute interaction de consentement | 42 sites |
| Avec fingerprinting publicité ou analytics avant consentement | 17 sites |
| Sites avec uniquement du fingerprinting de sécurité | 19 |
| Paires (domaine du script, finalité, technique) | 285 |

**Par finalité :**

| Finalité | Paires | Part |
|---|---|---|
| Anti-fraude / sécurité | 177 | 62 % |
| Inconnue | 76 | 27 % |
| Publicité | 19 | 7 % |
| Analytics | 11 | 4 % |
| Fonctionnelle | 2 | 1 % |

**Par origine du script :**
- premier tiers : 107 paires ;
- tiers : 99 ;
- **vendeur caché derrière une URL premier tiers** : 79.

Les vendeurs cachés représentent 100 scripts sur 31 sites, dont 80 d'Akamai Bot Manager (70 avec un chemin aléatoire).

**Par classe de nuisance :**

| Classe | Paires | Sites |
|---|---|---|
| Service (sécurité ou fonctionnel) | 179 | 47 |
| **Tracking** (publicité ou analytics) | **29** | **18** |
| Indéterminé, capacité haute | 37 | 12 |
| Indéterminé, capacité basse | 39 | 23 |
| Double usage | 1 | 1 |
| *Tracking, borne haute* (tracking + double usage + indéterminé haut) | *67* | *30* |

**Le tracking agit avant le consentement.** 17 des 18 sites avec tracking l'exécutent avant toute interaction de consentement, dont 12 avec un script de capacité haute.

**Les scripts de tracking**, toutes conditions confondues, sont 58 sur 29 sites :
- 52 sont des tiers présents sur au moins 2 sites, dans ce crawl ou selon Tracker Radar ;
- 39 écrivent dans le stockage ;
- 37 envoient des attributs connus du navigateur (fuseau horaire, plateforme) dans une URL ou un corps POST.

Principaux vendeurs : réseau d'ad-recovery de la famille html-load, pixel X (Twitter) Ads, AppsFlyer, Wunderkind, Prebid, Microsoft Clarity.

**Par catégorie** (sites avec fingerprinting / sites accessibles) :
- e-commerce : 22/23, dont 21 avec de la sécurité ;
- news : 26/43, dont 10 avec publicité ou analytics ;
- social : 15/25 ;
- affiliation : 3/6.

## 4. Effet des conditions : survie des paires de la baseline

**Comment lire le tableau :**
- **Survie** = part des paires de la baseline encore présentes dans la condition, après la phase de consentement.
- **Écart** = survie de la condition − survie de `baseline2` sur les mêmes sites (plancher de bruit).
- L'IC95 vient d'un bootstrap par site.
- Un écart dont l'IC contient 0 n'est pas distinguable du bruit.

| Condition | Toutes paires | Tracking | Service |
|---|---|---|---|
| ubol | 165/240, **−22,9 pts** [−36,2 ; −8,9] | 4/29, **−86,2 pts** [−96,8 ; −72,0] | 114/142, **−16,2 pts** [−30,7 ; −1,6] |
| pbadger | 167/224, **−18,8 pts** [−29,7 ; −8,6] | 16/29, **−44,8 pts** [−67,7 ; −19,4] | 111/133, **−12,8 pts** [−23,3 ; −3,6] |
| optout (refus + GPC) | 53/64, −12,5 pts [−31,0 ; +2,5] | 14/15, −6,7 pts [−25,0 ; 0] | 33/42, −21,4 pts [−45,0 ; 0] |
| reject (refus seul) | 45/51, −3,9 pts [−18,2 ; +8,5] | 13/13, 0 pt | 25/30, −16,7 pts [−39,3 ; 0] |
| gpc | 200/233, **−8,6 pts** [−15,2 ; −3,1] | 26/29, −10,3 pts [−23,3 ; 0] | 124/138, **−9,4 pts** [−19,4 ; −1,4] |
| accept | 76/93, −6,5 pts [−16,5 ; +1,2] | 23/23, 0 pt (+22 nouvelles) | 44/54, **−11,1 pts** [−26,0 ; −1,9] |

**Domaines tiers contactés**, différence médiane par site avec la baseline (IC95) :

| Condition | Différence | IC95 |
|---|---|---|
| ubol | −6 | [−10 ; −4] |
| pbadger | 0 | [−1 ; 0] |
| optout | +2 | [0 ; 3] |
| reject | +2 | [0 ; 3] |
| gpc | 0 | [0 ; 0] |
| accept | +50,5 | [40 ; 61,5] |

**Lecture :**
- **Bloqueurs.** Seuls les bloqueurs réduisent nettement le tracking, et uBlock bien plus que Privacy Badger. Les deux retirent aussi des scripts de sécurité, surtout HUMAN/PerimeterX sur 3 sites, puis Akamai, Forter et Riskified : c'est un dommage collatéral sur la détection de fraude.
- **Pertes « Service » sous `gpc`, `optout`, `reject` et `accept`.** Elles viennent de 1 à 3 sites chacune, avec des anti-bots qui ne se déclenchent pas à chaque visite (Kasada, Akamai, Stripe Radar, PerimeterX). Ce n'est pas un effet du signal.
  - Les dénominateurs sont petits (30 à 54 paires sous consentement), d'où les IC larges.
  - Ces conditions ont tourné 3 à 7 h après la baseline (section 8).
- **GPC.** L'écart « toutes paires » de −8,6 pts est significatif, mais il vient des pertes de scripts de sécurité, pas du tracking : 26 paires de tracking sur 29 survivent, avec un IC qui contient 0.

## 5. Consentement

**Vérification du choix enregistré.**
- Un choix est « confirmé » quand l'état de l'API du CMP (TCF, OneTrust, Cookiebot…) ou le cookie de consentement correspond à la demande.
- Il est « échoué » quand l'action n'a pas abouti ou que l'état contredit la demande.
- Il est « inconnu » quand aucun CMP pris en charge ne rapporte un choix complet.

| Condition | Confirmé | Échoué | Inconnu | Total |
|---|---|---|---|---|
| accept | 56 | 20 | 21 | 97 |
| reject | 31 | 37 | 26 | 94 |
| optout | 34 | 32 | 28 | 94 |

Les sources de confirmation sont l'API du CMP et le cookie stocké (29, 18 et 17 sites), le cookie seul, ou le TCF seul (détail dans `purpose_results.json` → `consent`). 29 des 31 refus vérifiés en `reject` le sont aussi en `optout`.

**Après un refus vérifié :**

| | refus + GPC (`optout`) | refus seul (`reject`) |
|---|---|---|
| Sites au refus vérifié | 34 | 31 |
| … avec du fingerprinting après le refus | 24 | 21 |
| … avec du fingerprinting publicité ou analytics | 8 | 8 |
| … avec du **tracking** (classe de nuisance) | **8** (6 de capacité haute) | **8** (5 de capacité haute) |

Les vendeurs de tracking encore actifs après un refus vérifié sont : ad-recovery html-load, Adobe Experience Platform, AnyClip/Prebid, AppsFlyer, GumGum, HubSpot, Mantis (ou Microsoft Clarity en `reject`), Prebid et le pixel X Ads.

Sites avec du fingerprinting publicité ou analytics après le refus : dictionary.com, elpais.com, huffpost.com, rtings.com, techcrunch.com, vice.com, weather.com, plus mirror.co.uk (`optout`) ou elcorteingles.es (`reject`).

**Refus contre acceptation**, sur les 31 sites vérifiés dans les deux sens :
- domaines tiers en médiane : 50 après acceptation contre 22 après refus ;
- cookies tiers en médiane : 27 contre 1 ;
- 29 des 31 sites contactent plus de tiers après acceptation ;
- paires de tracking : 22 après acceptation (10 sites) contre 14 après refus (7 sites).

Le refus coupe donc les cookies tiers, mais pas le fingerprinting de tracking déjà présent.

**États contradictoires.** Sur 6 sites américains utilisant OneTrust (cnn.com, nbcnews.com, cnbc.com, usatoday.com, nypost.com, rottentomatoes.com), le cookie stocké après le clic « refuser » montre un état accepté ou mixte, ou contredit l'API. Ils sont comptés en échec ou en inconnu, jamais en refus vérifié. Il faut les vérifier à la main avant d'en tirer une conclusion sur le site.

**Murs « consentir ou payer ».** Le détecteur a signalé 11 sites, mais seuls **3 sont de vrais murs** dans la bannière de consentement :
- repubblica.it : « Rifiuta e abbonati » ;
- theguardian.com : « Reject all and subscribe » ;
- goal.com : « Reject & Pay ».

Les 8 autres (dailymail.co.uk, newsweek.com, telegraph.co.uk, theverge.com, webmd.com, wikihow.com, wired.com, merriam-webster.com) sont des boutons d'abonnement ordinaires de la page, avec un libellé vide pour merriam-webster.com. Le motif `PAY_TO_REJECT` de `consent.py` accepte « subscribe » seul, même hors du cadre du CMP. Il faut le resserrer avant la prochaine collecte.

Sur les 3 vrais murs, le refus est impossible sans payer. Ils sont donc en « échoué » (action non terminée) et n'entrent pas dans les comparaisons de refus.

## 6. Validité des étiquettes de finalité

- **Lecture de `navigator.webdriver`** (sonde de détection de robot) :
  - scripts de sécurité : 139/144 = 96,5 % (IC95 [92,1 ; 98,5]) ;
  - scripts publicité ou analytics : 46/65 = 70,8 % (IC95 [58,8 ; 80,4]) ;
  - test exact de Fisher : p = 3·10⁻⁷.
- **Techniques fortes** (canvas, webgl, audio…) : 86,8 % des scripts de sécurité contre 12,3 % des scripts publicité ou analytics.

Ces deux tests indirects confirment que les deux groupes se comportent différemment.

**Accord avec Tracker Radar** sur les domaines tiers : κ = 0,45, accord brut de 64 %, sur seulement 11 domaines. Les désaccords portent sur stripe.network, adobedtm.com, cloudflare.com et hs-analytics.net. Tracker Radar connaît 47 des 60 domaines tiers, mais n'en catégorise que 16.

**Couverture des étiquettes** : 73 % des paires ont une finalité, 86 % des paires à technique forte. Sur 351 scripts, la confiance est haute pour 127, moyenne pour 79 et basse pour 145.

La validation manuelle à deux annotateurs n'est **pas encore faite**. La feuille d'annotation de 80 scripts est générée dans `results/full_eu_20261125/validation/` (privé). Elle est à remplir avant de publier un κ inter-annotateurs.

## 7. Comparaison avec le batch du 22 novembre

| Mesure | 22/11 | 25/11 |
|---|---|---|
| Sites accessibles en baseline | 93 | 97 |
| Sites avec fingerprinting | 64 | 66 |
| Paires | 237 | 285 |
| Part sécurité / publicité-analytics / inconnu (paires) | 63 % / 11 % / 25 % | 62 % / 11 % / 27 % |
| Tracking : sites, paires | 17, 26 | 18, 29 |
| Tracking, borne haute : sites, paires | 23, 45 | 30, 67 |
| Tracking avant consentement (sites) | 17 | 17 |
| Refus vérifiés (`optout`) / acceptations vérifiées | 36 / 44 | 34 / **56** |
| Sites avec tracking après refus vérifié | 8 / 36 | 8 / 34 |
| Paires de tracking survivant au refus (`optout`) | 15/16 | 14/15 |
| uBlock, tracking | −88 pts | −86 pts |
| Privacy Badger, tracking | −40 pts | −45 pts |
| uBlock, service | −19,7 pts | −16,2 pts |
| Tiers en plus après acceptation (médiane) | +56 | +50,5 |
| Scripts de tracking envoyant des attributs | 12 / 57 | 37 / 58 |

**Ce qui se reproduit :** la prévalence, la domination de la sécurité, l'effet des bloqueurs, la survie du tracking au refus et l'explosion des tiers après acceptation.

**Ce qui change, et pourquoi :**
- **Acceptations vérifiées en hausse.** C'est le clic par texte visible.
- **Scripts qui envoient des attributs : ×3.** Les corps POST sont maintenant capturés, et le fuseau cherché est celui du navigateur (Europe/Berlin). Ce n'est pas un changement des sites. C'est aussi pourquoi la borne haute du tracking monte : plus de scripts « inconnus » passent en capacité haute.

## 8. Limites

- **Conditions séquentielles.** Les conditions ont tourné l'une après l'autre sur 7 h. Le plancher de bruit (`baseline2`) est mesuré 23 min après la baseline, alors que `gpc` et `accept` tournent 6 à 7 h après. La dérive temporelle (rotation des scripts, réputation de l'IP) n'est donc pas couverte par le plancher. Les pertes de scripts de sécurité sous `gpc` (−9 pts) en donnent un ordre de grandeur. Un ordre aléatoire des conditions par site, ou une `baseline3` en fin de collecte, mesurerait cette dérive.
- **Petits dénominateurs sous consentement.** Il n'y a que 13 à 23 paires de tracking sur 8 à 14 sites, d'où des IC larges.
- **IP de datacenter.** Les 52 sites bloqués, surtout en e-commerce, manquent à toutes les conditions. Les résultats décrivent les sites accessibles depuis un VPN.
- **Étiquettes heuristiques.** Les finalités sont des estimations fondées sur des preuves (signatures, Tracker Radar, contexte), pas des déclarations des vendeurs. 27 % des paires restent de finalité inconnue, et la validation à deux annotateurs n'est pas faite.
- **Consentement.** 42 % des acceptations et environ 65 % des refus restent « échoué » ou « inconnu ». La cause : bannière non trouvée ou non cliquée, mur payant, ou CMP sans lecteur d'état (par exemple Reddit, Temu, YouTube). Les comparaisons de consentement portent donc sur un sous-ensemble de sites, et le détecteur de murs payants a des faux positifs (section 5).
- **Machine hôte.** Il y a eu deux suspensions et une coupure réseau (section 2). Elles sont sans effet mesuré sur les résultats, mais il faudra empêcher la veille sur secteur et éviter le Wi-Fi campus, qui bloque OpenVPN, pour la prochaine collecte.

## 9. Reproduire

```bash
# depuis la base privée data/full_eu_20261125/crawl.sqlite (non versionnée)
python scripts/reproduce_results.py --batch full_eu_20261125 --export
```

La collecte elle-même : `python scripts/run_study.py --sites data/full_eu_20261122/sites.csv --batch full_eu_20261125 --workers 2 --modes baseline baseline2 ubol pbadger optout reject gpc accept` (VPN configuré dans `.env`). Le manifeste (`manifest.json`) fige les réglages, la liste de sites et les empreintes du code.

Les autres fichiers de ce dossier sont décrits dans `README.md`.
