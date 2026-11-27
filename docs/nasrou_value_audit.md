# Audit de la valeur ajoutée de la branche `Nasrou`

## Position Git vérifiée le 26 novembre 2026

- branche active : `Nasrou`, HEAD `625a3fe` ;
- ancêtre commun avec `main` et `origin/main` : `f0c91cb` ;
- 13 commits propres à `Nasrou` depuis cet ancêtre ;
- `origin/Nasrou` s’arrête à `40ebc86` ; les trois commits locaux supplémentaires ajoutent le partage d’identifiants (`38b7e41`), le papier/diaporama (`46b0dfb`) et la validation aveugle à deux annotateurs IA (`625a3fe`) ;
- `origin/main` a de son côté quatre commits propres. La branche a donc divergé : son intégration demande une réconciliation, pas un simple fast-forward.

Le diff depuis l’ancêtre commun porte sur 89 fichiers, environ 48 206 insertions et 120 suppressions. Le nombre de lignes vient en grande partie des deux bundles de résultats ; il ne constitue pas en soi la contribution scientifique.

## But du projet

Le cahier des charges demande une petite étude de sécurité sur le tracking avancé : partir de la littérature, poser une question, construire un dispositif technique, l’évaluer avec baselines et métriques, puis produire un papier AsiaCCS et une présentation. Dans la piste défensive, il faut détecter, comprendre, comparer ou empêcher des mécanismes allant au-delà des simples cookies.

Le projet répond par une étude de mesure instrumentée : détecter le fingerprinting sur des sites populaires, distinguer sa finalité (tracking ou service/sécurité), puis mesurer l’effet d’un refus de consentement, de GPC et de deux bloqueurs.

## Valeur ajoutée réellement démontrée

### 1. Le projet ne compte plus toutes les lectures d’API comme également nuisibles

`purpose.py` et les scripts associés séparent Tracking, Service, Dual-use et Indéterminé à partir de signatures de vendeurs, Tracker Radar, chemins first-party, cookies de vendeurs et contexte de page. C’est la contribution conceptuelle principale : dans le batch du 25 novembre, 179/285 paires sont classées service, contre 29/285 tracking. Une mesure agrégée aurait masqué que uBOL retire 25/29 paires tracking tout en retirant aussi une partie du fingerprinting de sécurité.

### 2. Le refus est vérifié, pas déduit d’un clic

`consent.py` lit l’état live des CMP et `analyze.py` réévalue aussi les cookies IAB TCF, OneTrust et Cookiebot. Une contradiction donne `unknown`. Le décodeur a été croisé avec la bibliothèque IAB sur 21 chaînes TCF. Les conditions `reject`, `gpc`, `optout` et `accept` isolent le clic de bannière du signal navigateur.

### 3. L’évaluation contient un plan expérimental et un bruit de fond

Chaque couple `(run, site)` utilise un profil neuf. `baseline2` mesure la rotation publicitaire et la variabilité naturelle ; les effets sont exprimés contre ce repeat baseline, avec bootstrap par site. Le batch principal contient huit conditions sur 149 sites, dont 97 accessibles depuis la sortie VPN allemande ; un second batch trois jours plus tôt sert de réplication.

### 4. Les conclusions reposent sur plus que des appels d’API

Le crawler conserve URL et corps de certaines requêtes. L’étude cherche les attributs propres au navigateur dans les sorties réseau et les valeurs de cookie de forme identifiante transmises à une autre entreprise. Après refus vérifié, 21/31 sites en envoient encore à un tracker connu, contre 19 au baseline sur les mêmes sites (McNemar exact p=0,625). Un filtre strict exigeant une variation du même cookie entre profils conserve les 21 sites.

### 5. La chaîne est reproductible et partageable

`scripts/reproduce_results.py` régénère hors ligne les analyses depuis la base privée, y compris
les tests exacts, la sensibilité stricte des identifiants et `sensitivity_audit.json` pour la
reprise `accept`, la suspension et la qualité des visites GPC. Le manifeste fige réglages, liste
de sites, sortie VPN et empreintes du code. L’exporteur retire valeurs de cookies, URLs de scripts
et IP. Les deux bundles publics contiennent les tables par site et les résultats agrégés.

### 6. La validation est explicite, avec ses limites

Deux annotateurs IA indépendants, aveugles aux labels automatiques et entre eux, ont évalué 80 scripts. Un troisième relecteur IA a arbitré leurs 16 désaccords, avec Claude Opus comme second avis indépendant : 7 « yes » et 9 « unsure ». L’accord initial sur la finalité vaut κ=0,92 ; 34/34 labels sécurité et 11/14 labels tracking jugés sont confirmés. Cela vérifie la cohérence, pas une vérité terrain humaine : le papier le reconnaît.

## Résultats scientifiques centraux

- 66/97 sites accessibles fingerprintent ; 17 des 18 sites avec tracking fingerprinting le font avant toute interaction de consentement.
- Un refus vérifié sans GPC laisse 13/13 paires tracking ; avec GPC, 14/15 survivent. GPC seul n’a pas d’effet mesurable sur le tracking.
- uBlock Origin Lite laisse 4/29 paires tracking (−86 points). Privacy Badger en laisse 16/29
  (−45 points), mais 8 paires propres à la condition apparaissent : le compte net vaut 24/29.
  Privacy Badger active aussi GPC. Les pertes de sécurité sont partiellement confondues avec la
  dérive temporelle.
- L’état de consentement stratifie fortement les cookies/contacts tiers : le refus reste proche
  des deux baselines sans action, tandis que la condition `accept` est beaucoup plus élevée.
  Ce plan séquentiel ne démontre pas que le refus cause une baisse. La couche fingerprinting et
  le partage d’identifiants ne diminuent pas sous refus.
- Les tendances principales se reproduisent dans le batch du 22 novembre.

## Extension cookies/profils isolés ajoutée lors de cet audit

`scripts/cookie_isolation.py` exploite l’isolation des profils pour chercher les valeurs réémises entre `baseline` et `baseline2`. Le taux brut se réplique presque exactement : 36/96 sites le 25 novembre et 34/91 le 22. Mais 61 tokens de chaque batch sont présents dans plus de deux profils et 31/39 sites candidats se recouvrent entre dates, signe de constantes de site/configuration. Seulement 4 puis 3 tokens sont confinés aux deux baselines, sans réplication des sites. Le résultat défensif important est donc un filtre contre une fausse attribution : « même valeur dans deux profils » n’est pas synonyme de « respawn par fingerprint ».

Le détail et le protocole causal suivant sont dans [`cookie_isolation_findings.md`](cookie_isolation_findings.md).

## Faiblesses qui restent ouvertes

1. Les 52 sites inaccessibles, surtout e-commerce, biaisent la population vers les sites tolérant une IP de datacenter.
2. Les comparaisons de consentement n’utilisent que 31 à 56 sites vérifiés ; les dénominateurs de tracking sont petits.
3. Les labels de finalité restent heuristiques et la validation est faite par IA, avec une part de reconnaissance de vendeurs commune aux règles.
4. Les conditions sont séquentielles et non randomisées ; `baseline2` mesure le bruit mais n’élimine pas la dérive temporelle.
   La baisse de service sous GPC tombe de −9,4 à −7,4 points avec un IC atteignant zéro lorsque
   l’unique visite dégradée est exclue ; le tracking GPC ne change pas.
5. La branche annonce le protocole `respawn.py`, mais aucun résultat causal sur sites réels n’est encore intégré au papier.
6. Une cooccurrence cookie/fingerprinting ne prouve pas que le même script ou la même empreinte produit l’identifiant. Il faut les contrastes F1/F2, sortie IP et répétitions décrits dans l’étude cookies.

## Conclusion de l’audit

La valeur ajoutée de `Nasrou` n’est pas « un crawler de plus ». C’est la transformation d’un détecteur d’API en étude défensive qui sépare la finalité, vérifie le consentement, compare six défenses/signaux à une baseline répétée, suit des valeurs identifiantes jusqu’au réseau, publie des résultats répliqués et expose ses incertitudes. L’extension cookies nouvellement ajoutée apporte surtout un résultat méthodologique : l’isolation des profils permet de générer des leads, mais seule une expérience contrastée peut attribuer une réémission au fingerprinting.
