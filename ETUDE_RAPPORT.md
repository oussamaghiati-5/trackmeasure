# Rapport de l’étude TrackMeasure

**Étude principale :** collecte européenne de 149 sites, sortie VPN en Allemagne
**Batch :** `full_eu_20261122`
**Branche :** `Nasrou`
**Statut :** les six conditions ont été exécutées; la base exportée a passé `PRAGMA quick_check`.
**Public visé :** collègues qui vont examiner les résultats et préparer le rapport académique.

## 1. Résumé

TrackMeasure observe des appels à des API de navigateur associées à des techniques de fingerprinting, les requêtes réseau et l’état du consentement, puis compare plusieurs conditions de navigation. La liste d’étude comprend 149 domaines : 50 sites d’actualité, 50 de commerce, 37 sociaux et 12 d’affiliation.

À la sortie allemande, 93 sites ont produit une mesure de référence exploitable. Parmi eux, 91 ont aussi une mesure exploitable pour la seconde visite et pour uBlock Origin Lite, 92 pour Privacy Badger, 91 pour le refus avec GPC et 92 pour l’acceptation. Les refus HTTP 401/403/429 et autres échecs de navigation restent documentés et ne sont pas interprétés comme une absence de suivi.

Sur les sites appariés avec des paires détectées à la référence, les deux bloqueurs réduisent les paires script-domaine/technique observées par rapport à la répétition de référence. La diminution est plus marquée avec uBlock. Les choix de consentement sont beaucoup trop rarement confirmés pour estimer l’efficacité du refus : deux refus et une acceptation sont confirmés. Le résultat académique central doit donc porter sur les bloqueurs et la qualité/couverture de la vérification du consentement, sans conclusion générale sur l’effet juridique ou comportemental du consentement.

## 2. Question et contribution

Un site peut utiliser certaines caractéristiques techniques du navigateur — rendu graphique, polices, WebGL, audio ou propriétés du navigateur — pour contribuer à distinguer un navigateur d’un autre. Cet ensemble de techniques est souvent appelé **fingerprinting**. La présence d’une bannière de cookies ou d’une extension de protection ne suffit pas à établir ce qui se passe réellement après une visite.

Le projet fait tourner un navigateur Chromium instrumenté sur une liste de sites, attribue les appels détectés aux scripts lorsque possible, relève les requêtes et domaines tiers, puis répète la visite avec des protections différentes. Il tente aussi de refuser ou d’accepter via le gestionnaire de consentement du site et vérifie l’état enregistré après le choix.

La contribution pratique est une chaîne reproductible qui relie collecte, contrôles d’extensions, vérification de navigation, vérification du consentement et analyse par site. Le résultat n’est pas un nouveau détecteur validé de pistage : c’est une mesure comparative de comportements observés par des heuristiques navigateur, accompagnée de dénominateurs et d’exclusions explicites.

## 3. Conditions et protocole

Les six conditions sont :

| Identifiant | Condition | Rôle dans l’étude |
|---|---|---|
| `baseline` | Navigation sans bloqueur ni choix sollicité | Référence initiale et activité avant consentement |
| `baseline2` | Même protocole que la référence | Variabilité de répétition (publicités, contenu dynamique, changements de page) |
| `ubol` | uBlock Origin Lite activé | Mesurer les observations et requêtes avec le bloqueur |
| `pbadger` | Privacy Badger activé | Même comparaison avec une autre protection |
| `optout` | Refus demandé et GPC activé | Mesure du refus seulement lorsque le choix persistant est confirmé |
| `accept` | Acceptation demandée | Mesure d’acceptation seulement lorsque le choix est confirmé |

Paramètres enregistrés dans `results/full_eu_20261122/manifest.json` : deux workers; navigateur headful via Xvfb; une sous-page; cinq secondes de dwell; timeout de navigation de 45 s; timeout du site de 180 s; attente de consentement de 20 s; locale `en-US`; fuseau `Europe/Amsterdam`; seed `0`. Les conditions de comparaison réutilisent les URLs de sous-pages de la référence lorsqu’elles existent.

La sortie réseau mesurée depuis le réseau du crawler est `51.75.145.220`, pays `DE`. Le batch utilise un serveur VPNBook situé en Allemagne. Les identifiants et le profil privé ne sont pas inclus dans ce dépôt; les informations non secrètes et empreintes de fichiers sont dans `results/full_eu_20261122/vpn_provenance.json`. Le DNS-over-HTTPS a été changé de Cloudflare à Google après des expirations qui redémarraient le tunnel; l’interruption de consentement a été remise à zéro puis relancée.

## 4. Couverture et accès aux sites

| Catégorie | Liste | Référence exploitable | Taux d’accès de référence |
|---|---:|---:|---:|
| Actualités | 50 | 42 | 84 % |
| Commerce | 50 | 22 | 44 % |
| Social | 37 | 22 | 59 % |
| Affiliation | 12 | 7 | 58 % |
| **Total** | **149** | **93** | **62 %** |

La référence compte 56 échecs de mesure. 51 sont associés à un HTTP 401, 403 ou 429; les autres incluent d’autres erreurs ou chargements incomplets. L’échantillon mesuré n’est donc pas représentatif de façon égale des catégories : le commerce est particulièrement sous-représenté. Les résultats décrivent les visites exploitables, pas l’ensemble des 149 sites ni l’ensemble du Web.

### Dénominateurs par condition

Les valeurs ci-dessous sont **mesures de navigation exploitables**, et non nombre de choix de consentement confirmés.

| Condition | Visites exploitables / tentées | Résumé de l’analyse |
|---|---:|---|
| Référence | 93 / 149 | Médiane 15 domaines tiers; 1 384 appels FP; 64 sites avec au moins une paire FP |
| Référence répétée | 91 / 93 | Médiane 14 domaines tiers; 1 383 appels FP |
| uBlock Origin Lite | 91 / 93 | Médiane 7 domaines tiers; 816 appels FP; 3 624 requêtes bloquées |
| Privacy Badger | 92 / 93 | Médiane 12 domaines tiers; 1 016,5 appels FP; 2 376 requêtes bloquées |
| Refus + GPC | 91 / 93 | Médiane descriptive 16 domaines tiers; consentement confirmé sur 2 sites seulement |
| Acceptation | 92 / 93 | Médiane descriptive 34 domaines tiers; consentement confirmé sur 1 site seulement |

Les nombres de domaines, appels API et requêtes des conditions de consentement résument les visites navigables. Ils ne sont **pas** des estimations de l’effet du refus/acceptation, car la plupart des choix ne sont pas confirmés.

## 5. Résultats fingerprinting

### Activité observée à la référence

Parmi les 93 sites de référence exploitables, 64 ont au moins une paire script-domaine/technique classée comme fingerprinting par les heuristiques; 59 présentent au moins une telle observation pendant la phase pré-consentement. Les techniques détectées par site sont :

| Technique suivie | Sites concernés sur 93 |
|---|---:|
| WebGL | 51 |
| Énumération d’attributs | 49 |
| Canvas | 34 |
| Audio | 18 |
| Canvas-font | 11 |
| WebRTC | 10 |

Ces catégories peuvent se chevaucher sur un même site. Elles indiquent l’utilisation d’API instrumentées selon les règles du projet. Un usage peut être légitime ou fonctionnel; une détection ne prouve pas l’intention d’identifier ou de suivre un utilisateur. Les scripts et domaines doivent être examinés avant une affirmation sur des acteurs précis.

### Répétabilité et bloqueurs

Une paire est ici une combinaison **domaine du script × technique** observée. La répétition de référence sert à estimer la variabilité de court terme : 183 des 192 paires de référence sont réapparues sur 91 sites, soit 95,3 %; 11 nouvelles paires sont apparues. C’est un bruit de répétition substantiel à garder en tête pour lire les différences entre conditions.

| Condition | Sites exploitables dans comparaison | Paires de référence | Paires survivantes | Nouvelles paires | Taux de survie brut |
|---|---:|---:|---:|---:|---:|
| Référence répétée | 91 | 192 | 183 | 11 | 95,3 % |
| uBlock Origin Lite | 91 | 192 | 129 | 27 | 67,2 % |
| Privacy Badger | 92 au total; 57 dans le bootstrap apparié avec détections | 196 dans le tableau d’analyse | 145 | 30 | 74,0 % |

Le taux brut est agrégé par les paires de la comparaison disponible; il faut préférer l’estimation appariée suivante pour quantifier l’écart à la répétition. Sur les sites où la référence contient des paires détectées et où les deux mesures sont disponibles :

| Bloqueur vs répétition de référence | Sites appariés avec paires détectées | Différence de survie des paires | IC bootstrap 95 % (points de pourcentage) |
|---|---:|---:|---:|
| uBlock Origin Lite | 56 | −27,7 points | [−40,0; −16,3] |
| Privacy Badger | 57 | −19,8 points | [−31,7; −8,6] |

Les intervalles sont des percentiles d’un bootstrap par site (2 000 tirages, seed fixe). Ils décrivent l’incertitude entre sites de cet échantillon; ils ne corrigent ni la sélection non aléatoire, ni les erreurs de classification heuristique, ni la variabilité d’une autre journée.

Les domaines tiers contactés donnent un résultat concordant pour uBlock, plus faible pour Privacy Badger :

| Bloqueur vs répétition de référence | Sites avec domaines mesurés dans les deux visites | Variation médiane appariée du nombre de domaines tiers | IC bootstrap 95 % |
|---|---:|---:|---:|
| uBlock Origin Lite | 90 | −6,5 domaines | [−8,5; −4] |
| Privacy Badger | 91 | 0 domaine | [−2; 0] |

Les requêtes bloquées comptées pendant les visites sont 3 624 pour uBlock et 2 376 pour Privacy Badger. Ce sont des totaux de requêtes au cours du protocole; ils ne représentent pas forcément des trackers ou des identifiants empêchés d’atteindre un tiers.

## 6. Résultats de consentement

Le vérificateur distingue l’action tentée de l’état enregistré. Un clic, une bannière disparue ou une valeur par défaut ne suffit pas : le résultat doit être confirmé par l’état lisible du CMP et rester cohérent lors des contrôles ultérieurs.

| Condition | Confirmé | Échoué | Inconnu | Navigation exploitable |
|---|---:|---:|---:|---:|
| Refus + GPC | 2 | 36 | 53 | 91 |
| Acceptation | 1 | 65 | 26 | 92 |

Un échec de navigation supplémentaire est présent dans le groupe `accept` (93 tentatives au total). Après comparaison avec la référence répétée et exigence de choix confirmé, il reste seulement **un site apparié admissible** pour le refus et **un** pour l’acceptation. Ce volume ne permet ni estimation stable, ni conclusion générale sur l’efficacité du consentement. La conclusion défendable est que la couverture de confirmation du CMP est faible avec ce protocole et cette liste de sites.

Le traitement combine refus et GPC; même avec plus de sites, cette conception ne permettrait pas d’attribuer séparément un effet au refus et un effet à GPC sans condition supplémentaire.

## 7. Interprétation et limites

1. **Heuristiques, pas preuve d’intention.** Des appels API sont des signaux potentiels. Les scripts d’anti-fraude, de rendu ou de fonctionnalité peuvent produire des signaux similaires. Le rapport doit employer « appels détectés » ou « paires classées par l’heuristique » plutôt que d’assimiler chaque détection à un traqueur.
2. **Attrition d’accès non aléatoire.** Les refus HTTP sont concentrés dans certains secteurs; l’analyse de 93 sites ne peut être extrapolée mécaniquement à la liste complète ou à la popularité générale.
3. **Mesure à un seul moment.** Les pages, campagnes publicitaires, scripts, CMP et règles des extensions évoluent. La référence répétée mesure une partie du bruit de session, pas l’évolution temporelle entre jours.
4. **Consentement faiblement couvert.** Trois choix confirmés au total excluent des conclusions comparatives générales.
5. **Effet propre à la configuration.** Les résultats sont liés aux versions de navigateur/extensions, aux paramètres, à la locale et à cette sortie VPN. Le manifeste et les journaux doivent accompagner les chiffres.
6. **Domaine tiers ≠ traqueur.** Un contact tiers ne signifie pas automatiquement collecte d’un identifiant ou usage publicitaire.
7. **Résultats décrits, pas causalité absolue.** Les visites sont rapprochées par site et URL autant que possible, mais les pages changent et les bloqueurs peuvent modifier le chargement. Les différences sont des effets observés dans cette exécution.

## 8. Fichiers de données et mode d’emploi

Commencer par ces sorties :

| Fichier | Contenu / usage |
|---|---|
| `results/full_eu_20261122/analysis.log` | Résumé lisible des comptes, médianes, techniques et paires survivantes |
| `results/full_eu_20261122/per_site.csv` | Ligne par site et condition; statut de mesure, requêtes, domaines, appels, consentement, admissibilité |
| `results/full_eu_20261122/comparison.csv` | Comparaison des paires détectées avec la référence, par site |
| `results/full_eu_20261122/paired_uncertainty.json` | Différences appariées et intervalles bootstrap calculés par `scripts/paired_uncertainty.py` |
| `results/full_eu_20261122/consent_coverage.csv` | Toutes les actions et issues de consentement, y compris les exclusions |
| `results/full_eu_20261122/fp_scripts.csv` | Observations par script, domaine et technique |
| `results/full_eu_20261122/manifest.json` | Liste, réglages, sortie réseau et empreintes des sources du batch |
| `results/full_eu_20261122/vpn_provenance.json` | Provenance non secrète du VPN et changement DNS |
| `data/full_eu_20261122/crawl.sqlite` | Base complète de visites, requêtes, API, cookies et statuts |
| `data/full_eu_20261122/sites.csv` | Copie figée de la liste d’entrée |

Les sorties agrégées partageables sont aussi publiées dans [`public_results/full_eu_20261122/`](public_results/full_eu_20261122/). Ce paquet exclut la base brute, les journaux détaillés, les URL de scripts susceptibles de contenir des paramètres de session et l’adresse IP de sortie VPN. Le script de génération est `scripts/export_public_results.py`.

Les fichiers `*.log` de chaque condition expliquent les erreurs de navigation. Utiliser `comparison.csv` et `paired_uncertainty.json` pour les résultats de bloqueurs; utiliser `per_site.csv` pour contrôler les lignes; utiliser `consent_coverage.csv` pour toute affirmation de consentement. Ne pas calculer un effet de refus/acceptation sur tous les sites navigables : filtrer sur les choix confirmés et l’admissibilité appariée, ce qui laisse ici n=1 par action.

La base européenne a été exportée depuis un volume Docker Linux après récupération SQLite; le fichier exporté a passé `quick_check`. La base complète de l’étude exploratoire directe au Maroc se trouve dans `data/full_direct_20261122/crawl.sqlite` et ses tableaux dans `results/full_direct_20261122/`. Cette collecte secondaire est distincte et ne remplace pas le résultat européen pour le consentement.

## 9. Conclusion à communiquer

Dans cette collecte européenne, uBlock Origin Lite et Privacy Badger ont réduit les paires d’API classées comme fingerprinting relativement à la variabilité de répétition, avec une réduction plus marquée pour uBlock. uBlock a également diminué le nombre de domaines tiers contactés dans les visites appariées; le changement médian correspondant avec Privacy Badger était nul. Ces observations concernent les sites accessibles et les heuristiques employées. La plupart des choix de consentement n’ont pas pu être confirmés; l’étude ne permet donc pas de conclure que refuser ou accepter les cookies change le fingerprinting à l’échelle générale.

## 10. Prochaines étapes pour le papier

Un brouillon d’article structuré autour de ces résultats et des travaux liés est disponible dans [`PAPER_DRAFT.md`](PAPER_DRAFT.md). Il reprend les résultats mesurés et explicite les limites; les éléments ci-dessous restent à faire avant soumission.

1. Reproduire les tableaux ci-dessus depuis `comparison.csv`, `paired_uncertainty.json` et `consent_coverage.csv`.
2. Faire vérifier manuellement un échantillon stratifié de scripts signalés, surtout sur commerce et anti-fraude; documenter la règle de classification.
3. Rapporter les échecs et dénominateurs par catégorie avec les résultats, sans imputer les sites inaccessibles comme zéros.
4. Inclure les versions d’extensions, les réglages et la provenance de la sortie réseau en annexe/méthodes.
5. Garder le résultat consentement sous forme d’un constat de couverture faible; une étude de consentement plus forte nécessite une meilleure prise en charge des CMP et davantage de choix confirmés.
6. Si le calendrier le permet, répéter la mesure un autre jour pour caractériser la variabilité temporelle et valider manuellement les faux positifs.
