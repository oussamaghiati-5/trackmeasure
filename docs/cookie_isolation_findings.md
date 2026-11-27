# Cookies et profils isolés — résultat défensif

## Question

Une valeur de cookie qui ressemble à un identifiant est-elle réémise au même site dans deux runs dont les profils Chromium sont indépendants, et ce phénomène coexiste-t-il avec le fingerprinting ?

Cette question prolonge la piste défensive du cahier des charges : elle cherche un signal de reliaison invisible entre profils, puis mesure les faux positifs avant toute attribution à une empreinte.

## Garantie d’isolation utilisée

`crawler.py` crée `tempfile.mkdtemp(prefix="tm_profile_")` dans `crawl_site`, une fois par couple `(run, site)`, lance un contexte Chromium persistant dans ce dossier, puis détruit le dossier dans `finally`. Ainsi, `baseline`, `baseline2`, les bloqueurs et les conditions de consentement ne partagent ni cookies ni stockage de profil.

`scripts/cookie_isolation.py` cherche un token d’au moins 10 caractères URL-safe, avec un chiffre et plus de quatre caractères distincts. Il exclut :

- les timestamps proches de la collecte ;
- les cookies de consentement connus ;
- tout token observé sur plus d’un site, car il s’agit probablement d’une constante globale ;
- les mesures non éligibles.

Les fichiers publics ne contiennent aucune valeur de cookie : seulement les sites, noms/domaines de cookies et comptes agrégés.

## Résultats répliqués

| Mesure, `baseline` vs `baseline2` | 22 nov. | 25 nov. |
|---|---:|---:|
| Sites comparables | 91 | 96 |
| Sites avec ≥1 token réémis | 34 (37,4 %) | 36 (37,5 %) |
| Tokens candidats | 64 | 65 |
| Tokens présents dans plus de deux profils | 61 | 61 |
| Tokens confinés aux deux baselines | 3 | 4 |
| Sites candidats avec FP dans les deux runs | 21 | 23 |
| … avec FP classé tracking dans les deux runs | 8 | 9 |

Les ensembles de sites se recouvrent sur 31 des 39 sites de leur union (Jaccard 0,795). Cette stabilité, jointe aux 61 tokens multi-conditions dans chaque batch, indique que le signal brut est dominé par des valeurs fixes propres au site ou à sa configuration. C’est un résultat négatif utile : une égalité exacte entre profils ne suffit pas à conclure à un respawn ou à une dérivation par empreinte.

Les petits ensembles confinés aux deux baselines ne se répliquent pas entre les dates :

- 25 novembre : `elmundo.es` (`_pcus`), `lemonde.fr` (`atauthority`), `lowes.com` (`__rtbh.lid`, `__rtbh.uid`) et `washingtonpost.com` (`_t_tests`) ;
- 22 novembre : `cnbc.com` (`_pcus`), `independent.co.uk` (`bounceClientVisit6764v`) et `newegg.com` (`__cf_ob`).

Deux contrôles sémantiques expliquent déjà des faux positifs. Piano documente `_pctx` dans son mécanisme de consentement et `_pcus` comme support de segments ; AT Internet documente `atauthority` comme mémoire du mode de confidentialité. Leur répétition peut donc encoder une configuration ou un état de consentement, pas une personne. En revanche, les politiques de cookies publiques décrivent `__rtbh.lid`/`__rtbh.uid` comme identifiants publicitaires RTB House : `lowes.com` est le premier candidat à retester, sans préjuger du mécanisme qui a réémis la valeur.

Références de contrôle : [Piano — gestion des cookies](https://docs.piano.io/en/subscriptions/consent-management-for-client-storage-cookies), [Piano — segments côté edge](https://docs.piano.io/en/subscriptions/api-interaction-on-edge), [AT Internet — cookie `atauthority`](https://developers.atinternet-solutions.com/as2-tagging-en/javascript-en/advanced-features-javascript-en/privacy-javascript-en/index.html), [SNCF Connect — liste des cookies RTB House](https://www.sncf-connect.com/en-en/informations-legales/confidentialite/cookies-liste).

## Ce que la cooccurrence avec le fingerprinting signifie

Dans le batch principal, 23/36 sites avec réémission brute fingerprintent aussi dans les deux baselines, dont 9 avec une finalité classée tracking. Douze ont un domaine propriétaire du cookie qui apparaît aussi comme domaine de script fingerprinting. Ces intersections servent à prioriser les cas, mais elles ne relient pas la valeur du cookie au calcul d’empreinte. Un script peut fingerprinting pour la sécurité pendant qu’un autre composant pose une constante.

## Protocole causal suivant

Pour chaque candidat, commencer par `lowes.com`, exécuter au moins cinq blocs dont l’ordre des traitements est randomisé :

| Traitement | Profil | État supprimé | Empreinte | Rôle |
|---|---|---|---|---|
| A1 | profil A | rien | F1 | référence avec cookie |
| A2-cookie | même profil A | cookies uniquement | F1 | respawn depuis localStorage/IndexedDB/cache/SW |
| A2-all | profil neuf | tout l’état navigateur | F1 | reliaison fraîche : candidat FP/IP/serveur |
| B | profil neuf | tout | F2 | contrôle d’empreinte |
| C | profil neuf | tout | F1, autre sortie IP | contrôle d’IP |

Le manifeste doit vérifier avant chaque navigation que les profils neufs ont zéro cookie et zéro origine de stockage, enregistrer les valeurs F1/F2 réellement exposées, et conserver le même contenu/page et la même fenêtre temporelle. Une attribution « compatible avec l’empreinte » exige :

1. la même valeur dans au moins 4/5 répétitions A2-all ;
2. son absence dans au moins 4/5 contrôles B ;
3. la disparition ou le changement sous C si l’IP explique le signal ;
4. l’exclusion des constantes de configuration par inspection du format et répétition à une autre date ;
5. idéalement, l’observation de la valeur dans une requête du composant qui fingerprint.

Sans ces contrastes, le verdict reste « réémission inter-profils », jamais « identifiant dérivé du fingerprint ».

## Reproduction

```bash
python scripts/cookie_isolation.py data/full_eu_20261125/crawl.sqlite results/full_eu_20261125
python scripts/cookie_isolation.py data/full_eu_20261122/crawl.sqlite results/full_eu_20261122
```

Les agrégats partageables se trouvent dans :

- `public_results/full_eu_20261125/cookie_isolation.{csv,json}` ;
- `public_results/full_eu_20261122/cookie_isolation.{csv,json}`.
