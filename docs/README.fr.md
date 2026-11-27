<p align="center">
  <img src="logo.svg" alt="Logo OptimCE" width="160">
</p>

# OptimCE — Recherche du GRD (service interne)

[![Site web](https://img.shields.io/badge/Site%20web-optimce.be-2e7d32.svg)](https://www.optimce.be)
[![Licence](https://img.shields.io/badge/Licence-Apache%202.0-blue.svg)](../LICENSE)
[![Licence des données](https://img.shields.io/badge/Donn%C3%A9es-CC%20BY%204.0-lightgrey.svg)](../ATTRIBUTION.md)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-43a047.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](README.nl.md)

Répond à une seule question pour la plateforme OptimCE : **quel gestionnaire de
réseau de distribution (GRD, *distribution system operator*, *netbeheerder*)
exploite les réseaux d'électricité et de gaz à une adresse belge ?** crm-backend la
pose pendant qu'un utilisateur encode un compteur, pour préremplir le GRD du
compteur (`meter_data.grd`). La réponse est indicative : l'utilisateur peut toujours
la changer, et quand ce service est arrêté, le formulaire garde simplement ce que
l'utilisateur choisit.

Les données ne sont pas maintenues ici. Elles sont une version épinglée de
[`belgian-dso-map`](https://github.com/OptimCE/belgian-dso-map), la seule source de
vérité des attributions commune → GRD, livrée dans l'image sous `data/`.

Ce service fait partie de la plateforme [OptimCE](https://www.optimce.be). La stack
complète s'exécute depuis le [monorepo](https://github.com/OptimCE/monorepo).

## Interne, pas une annexe

dso-lookup suit les conventions des services annexes (le squelette FastAPI, la
journalisation, la télémétrie et la CI de billing), mais ce n'est **pas une
annexe** :

- **crm-backend est son seul appelant**, par le réseau Docker : le réseau `backend`
  en développement (plus un port limité à la boucle locale, `127.0.0.1:8097`, pour
  les vérifications curl), et un réseau `dso-lookup` dédié en production, dont le
  seul autre membre est crm-backend.
- Pas de route KrakenD, pas d'emplacement nginx, pas de conteneur `*-doc-gen`, pas
  d'entrée dans `krakend-builder.yaml`. Le navigateur passe par les routes de
  crm-backend, `GET /dso/operators` et `GET /dso/resolve`, qui appellent ce service.
- **Ne jamais le lister dans `ANNEX_CATALOG_*`** : crm-backend s'arrête au démarrage
  sur un nom d'annexe qu'il ne connaît pas.
- Pas d'authentification, pas de CORS, pas de base de données, pas de broker.

## API

Chaque corps est enveloppé dans `{"data": <payload>, "error_code": 0}` ; le fichier
`src/modules/dso/infra/dso-lookup.client.ts` de crm-backend le déballe et lit les
types de `src/modules/dso/domain/dso.types.ts`, champ pour champ.

| Route | Réponse |
|---|---|
| `GET /dso/resolve?postcode=&ins=&section=` | `ResolveOut` : par code postal, affiné au besoin par commune (`ins`) puis par section |
| `GET /dso/resolve-address?nis_code=&postcode=&post_name=` | `ResolveOut` : par l'adresse renvoyée par le sélecteur BeST (le code NIS à 5 chiffres, passé par TRIM et comparé exactement ; chaque INS suit `[1-9][0-9]{4}`, donc sa forme entière est la même chaîne, et toute autre valeur donne `not_found`, jamais un 422) |
| `GET /dso/operators` | `OperatorOut[]` : le catalogue, les GRD d'abord dans l'ordre `GRD_OPTIONS` de l'application, Elia en dernier |
| `GET /dso/ean/{ean}/check` | `{format, belgian_prefix, gs1_check, known_operator}` : informatif seulement, ne renvoie jamais l'EAN |
| `GET /dso/meta` | `{data_version, version_date, communes, communes_by_region: {BRU, VLA, WAL}, rows, operators}` |
| `GET /health/liveness` | `{"status": "alive"}` |
| `GET /health/readiness` | 200 ou 503, `{"status", "checks": {"data": …}}` |
| `GET /healthz` | Alias de `/health/readiness` (le nom qu'emploie la spec) |

`ResolveOut` vaut `{status, communes?, sections?, result?, data_version}`, avec un
`status` parmi :

- `ok` : `result` = `{ins, commune: {fr, nl, de}, section | null, region, regulator,
  elec: {operator, designated}, gas: {status, operator | null}, flags, note | null,
  source_ids, valid_from | null, data_version}`. `elec.operator` est le GRD qui
  exploite le réseau (celui de la facture, et celui que le formulaire du compteur
  préremplit) ; `elec.designated` est le GRD légalement désigné, affiché en note
  quand il diffère.
- `choose_commune` : le code postal couvre plusieurs communes (1040, 1050, 1804) ;
  `communes` les liste, chacune `{ins, names, region, province}`.
- `choose_section` : la commune a des sections aux gestionnaires différents ;
  `communes` contient cette seule commune et `sections` ses candidates, chacune
  `{section_key, names, postcodes, elec_operator}` (l'appelant peut donc préremplir
  quand elles concordent toutes, comme à Baarle-Hertog).
- `not_found` : code postal ou code NIS inexistant. **Une entrée mal formée est une
  réponse, jamais un 422.**

L'algorithme est celui de `scripts/lookup.py` de belgian-dso-map, porté ligne à ligne
dans `api/dso/lookup.py` (normalisation : TRIM de `[ \t\r\n ﻿]` exactement,
code postal `^[0-9]{4}$`, noms en NFKD sans diacritiques, en minuscules, `[a-z0-9]`
seulement ; un filtre qui ne correspond à rien est ignoré ; un nom postal égal au nom
de la commune ne choisit jamais de section). Le régulateur vient de la région :
`BRU` → `BE-BRU-BRUGEL`, `VLA` → `BE-VLA-VREG`, `WAL` → `BE-WAL-CWAPE` (les codes de
`reference/regulators.json` du monorepo, dont les indicateurs `active` ne changent
rien ici).

## Santé

`/health/readiness` (et `/healthz`) ne vaut 200 que lorsque :

1. les données sont chargées (un `data/` qui échoue à ses contrôles fait échouer le
   **démarrage**, donc le conteneur ne tourne jamais à moitié) ;
2. les décomptes refaits à partir des fichiers égalent les `counts` de
   `data/release.json`, et la version servie est celle de `release.json` ;
3. pour une version publiée (sans `-draft`) qui n'est pas une fixture
   (`DSO_DATA_FIXTURE`, refusée en production) : les décomptes nationaux,
   565 communes = 261 WAL / 285 VLA / 19 BRU.

Les décomptes attendus viennent des données elles-mêmes : la mini-fixture
(6 communes) est donc prête elle aussi ; l'assertion nationale est la garde
supplémentaire d'une vraie version. Les healthchecks compose (développement et
production) sondent `/health/readiness` avec l'urllib de Python : l'image n'a pas de
curl.

## Données

`data/` est une version de belgian-dso-map, octet pour octet (`.gitattributes` :
`data/** -text`), moins son `REPORT.md` facultatif. **Seul `scripts/sync_data.py`
l'écrit**, et il réécrit en même temps `ATTRIBUTION.md` à partir de celui de la
version :

```bash
python scripts/sync_data.py --latest --if-newer            # ce que lance la mise à jour ; aucun jeton requis (DSO_MAP_TOKEN facultatif)
python scripts/sync_data.py --version 2026.10.0            # une version publiée ; la nommer permet de ramener data/ en arrière
python scripts/sync_data.py --latest --check               # code de sortie 10 si une version plus récente existe, n'écrit rien
python scripts/sync_data.py --from-dir ../../belgian-dso-map/dist [--dry-run]
python scripts/sync_data.py --from-dir ../../belgian-dso-map/.build/mini --allow-draft
```

Les options et les codes de sortie sont ceux de `scripts/sync_dso.py` du site web
(`--version` | `--latest`, `--from-dir`, `--allow-draft`, `--if-newer`, `--dry-run`,
`--pr-body`, `--github-output` ; 0 synchronisé ou à jour, 1 refusé ou en échec,
2 mauvais usage), plus `--check` (code 10) et `--data-dir`. `--latest` lit GitHub et
est refusé avec `--from-dir` ; une version que GitHub marque `draft` ou `prerelease`
est refusée ; seules les URL `/repos/{repo}/releases/assets/…` sont téléchargées, et
les redirections seulement en https. belgian-dso-map est public : GitHub est donc lu
**anonymement** quand `DSO_MAP_TOKEN` est absent ou vide (aucun en-tête
`Authorization`). Ne le fixez à un jeton GitHub que pour lever la limite de débit
anonyme de l'API (60 requêtes par heure et par IP ; une synchronisation en fait une
dizaine) : il est envoyé dans un en-tête non redirigé, jamais à l'URL de stockage
signée vers laquelle les fichiers redirigent, et n'est jamais affiché. Aucune autre
variable (`GITHUB_TOKEN`, `GH_TOKEN`) n'est lue.
Une différence, voulue : une version **plus ancienne** que celle de `data/` est
refusée sauf si elle est nommée avec `--version` (un retour en arrière), là où
`sync_dso.py` continue avec une note.

Avant toute écriture, la version est contrôlée exactement comme le service la
contrôle au démarrage : chaque fichier présent est listé dans `SHA256SUMS` et
correspond, l'ensemble requis est présent, le dialecte et les en-têtes CSV sont ceux
du contrat, un seul `data_version` égal à `release.json`, les dates du contenu tiennent
(`first_seen` ≤ `changed_at` ≤ la date de la version sur chaque ligne ; `valid_from`
et le `retrieved_at` de chaque source à cette date ou avant), la grammaire de
`CHANGES.md` tient, chaque gestionnaire, note et source référencé existe, aucune ligne
n'est attribuée à un gestionnaire `lookup:false` (Elia), et les décomptes
correspondent. `data/` est ensuite remplacé en tout ou rien. Un brouillon (`-draft`)
est refusé sauf avec `--from-dir … --allow-draft`, et la CI refuse les données
brouillon (`tests/test_ci_guard.py`, et `build.yml` avant de construire une image).

La **mise à jour des données** (`.github/workflows/dso-data-bump.yml`) lance
`sync_data.py --latest --if-newer` (ou `--version <tag> --if-newer`) et ouvre la pull
request « dso data X → Y » avec un jeton d'accès personnel (`DSO_DATA_BOT_TOKEN` :
lecture/écriture de Contents et Pull requests sur ce seul dépôt), car une pull request
ouverte avec `GITHUB_TOKEN` ne déclenche aucun workflow et les vecteurs de la nouvelle
version ne tourneraient nulle part. Lire la version ne demande aucun secret : l'étape
de synchronisation reçoit le `github.token` de l'exécution comme `DSO_MAP_TOKEN`,
uniquement contre la limite de débit anonyme. La fusionner publie un nouveau
`:main-prod` : `build.yml` surveille `data/**`. Elle démarre de trois façons :

- `repository_dispatch` `dso-data-released`, envoyé par le `release.yml` de
  belgian-dso-map avec `client_payload.version` quand son secret
  `DSO_CONSUMERS_TOKEN` est défini. GitHub **désactive le cron d'un dépôt public après
  60 jours sans activité** (un e-mail, aucun contrôle en rouge), et une version sort
  environ tous les six mois : c'est cet envoi qui garde la mise à jour en vie. Quand
  `release.yml` n'a pas pu l'envoyer (pas de secret, ou envoi en échec : sa note ou
  son avertissement le dit), lancez le workflow à la main après la version
  (réactivez-le d'abord si GitHub l'a désactivé).
- Le cron, le lundi à 06:00 UTC : le filet de sécurité tant qu'il est actif.
- À la main (`workflow_dispatch`) ; une `version` donnée là est épinglée même si elle
  est plus ancienne.

## Vie privée

Une recherche porte un code postal, un code NIS, un nom postal et un EAN — un
domicile. Le service n'en garde rien :

- uvicorn tourne avec `--no-access-log` dans les deux Dockerfiles (sa ligne d'accès
  contient toute la chaîne de requête, et en production le logger racine exporte en
  OTLP) ;
- aucune route ne journalise un paramètre. Les chemins d'erreur partagés de billing
  (413, 504, erreurs de domaine et non gérées) journalisent le chemin de l'URL, qui
  pour `/dso/ean/{ean}/check` contient l'EAN : `main.py` le remplace par le modèle de
  la route avec un filtre sur ces loggers, avant qu'aucun handler ne voie
  l'enregistrement ;
- la seule trace est le compteur `dso.lookups.total{endpoint, status}`, dont les
  étiquettes sont des vocabulaires fermés (`core/metrics.py` refuse tout le reste) ;
- les métriques HTTP de l'instrumentation OpenTelemetry portent le modèle de la route,
  jamais le chemin ni la chaîne de requête ; aucun span n'est enregistré (aucun
  `TracerProvider` n'existe dans la plateforme), et aucun ne doit l'être avant que
  `/dso/` n'en soit exclu ;
- le contrôle d'EAN ne renvoie jamais l'EAN ; le code NIS n'est jamais stocké, par
  personne.

`tests/test_privacy.py` vérifie tout cela.

## Configuration

| Variable | Défaut | |
|---|---|---|
| `ENV` | `local` | `local`, `test`, `staging`, `production`. Documentation (`/docs`) seulement en `local` |
| `DSO_DATA_DIR` | `<repo>/data` | Le dossier de la version. À laisser vide : l'image le contient |
| `DSO_DATA_FIXTURE` | `false` | `true` seulement pour servir une fixture qui n'est pas un brouillon ; refusé en production |
| `LOGGING_TOKEN`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | vide | Requis en production (journaux et métriques en OTLP/HTTP) |
| `LOGGING_TRACES_URL` | vide | Déclaré par parité avec les annexes ; inutilisé |
| `DEFAULT_LOCALE` | `fr-BE` | |

## Développement

Python 3.12 (`py -3.12` sous Windows).

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements/all.txt

ENV=test .venv/Scripts/python.exe -m pytest -q     # sans Docker : pas de base de données
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy .

.venv/Scripts/uvicorn main:app --reload --no-access-log   # http://127.0.0.1:8000/docs
docker build -f Dockerfile.production -t dso-lookup:local .
```

Les tests passent chaque ligne de `data/vectors.csv` par HTTP, vérifient la rigueur du
chargeur sur des copies cassées de `data/`, comparent le portage à
`scripts/lookup.py` de belgian-dso-map quand ce dépôt est cloné à côté du monorepo, et
exercent `sync_data.py` (succès, refus, `data/` intact en cas d'échec, aucun retour en
arrière silencieux, `--if-newer`, `--dry-run`, le chemin GitHub avec une fausse API).

CI : `lint.yml` (ruff, format, mypy), `test.yml` (pytest, sans conteneur de service),
`build.yml` (`Dockerfile.production` → `ghcr.io/optimce/dso-lookup:main-prod`, avec
l'étiquette `optimce.dso.data-version`), `dso-data-bump.yml`,
`notify_monorepo_update.yml`.

## Écarts avec la spec

- Le `/healthz` de la spec est le `/health/readiness` de billing ; `/healthz` reste un
  alias.
- `gh release download` est remplacé par l'API REST de GitHub (`urllib`,
  `add_unredirected_header`) et `--from-dir` : `gh` n'est pas installé sur les postes
  des développeurs.

## Contribuer

Les contributions sont les bienvenues ! Veuillez lire les
[directives de contribution](../CONTRIBUTING.md) et notre
[Code de conduite](../CODE_OF_CONDUCT.md) avant d'ouvrir une issue ou une pull
request.

## Sécurité

Pour signaler une faille de sécurité, veuillez suivre la
[politique de sécurité](../SECURITY.md) — n'ouvrez pas d'issue publique.

## Licence

Le code est distribué sous la [licence Apache 2.0](../LICENSE), qui ne couvre pas
`data/`. Les données de `data/` sont une version du jeu de données public
[belgian-dso-map](https://github.com/OptimCE/belgian-dso-map), compilé et
**mis à disposition par OptimCE sous la
[licence Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)**.
Pour les réutiliser, créditez « belgian-dso-map, OptimCE » avec la version utilisée, sa
date et un lien vers le dépôt, et dites si vous les avez modifiées. Les sources gardent
leurs propres licences et exigences d'attribution : [ATTRIBUTION.md](../ATTRIBUTION.md)
les crédite, et est réécrit à chaque version des données par `scripts/sync_data.py`.
