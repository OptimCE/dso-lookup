<p align="center">
  <img src="logo.svg" alt="OptimCE-logo" width="160">
</p>

# OptimCE — DNB-opzoeking (interne service)

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/nl/)
[![Licentie](https://img.shields.io/badge/Licentie-Apache%202.0-blue.svg)](../LICENSE)
[![Datalicentie](https://img.shields.io/badge/Data-CC%20BY%204.0-lightgrey.svg)](../ATTRIBUTION.md)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-43a047.svg)](README.nl.md)

Beantwoordt één vraag voor het OptimCE-platform: **welke distributienetbeheerder
(DNB, *gestionnaire de réseau de distribution*, *distribution system operator*)
beheert het elektriciteits- en het gasnet op een Belgisch adres?** crm-backend stelt
ze terwijl een gebruiker een meter invoert, om de DNB van de meter
(`meter_data.grd`) vooraf in te vullen. Het antwoord is een suggestie: de gebruiker
kan het altijd wijzigen, en wanneer deze service niet draait, behoudt het formulier
gewoon wat de gebruiker kiest.

De gegevens worden hier niet onderhouden. Ze zijn een vastgezette versie van
[`belgian-dso-map`](https://github.com/OptimCE/belgian-dso-map), de enige bron van
waarheid voor de toewijzingen gemeente → DNB, meegeleverd in de image onder `data/`.

Deze service maakt deel uit van het [OptimCE](https://www.optimce.be/nl/)-platform.
De volledige stack draait vanuit de [monorepo](https://github.com/OptimCE/monorepo).

## Intern, geen annex

dso-lookup volgt de conventies van de annexservices (het FastAPI-skelet, de logging,
de telemetrie en de CI van billing), maar is **geen annex**:

- **crm-backend is de enige aanroeper**, via het Docker-netwerk: in ontwikkeling het
  netwerk `backend` (plus een poort die alleen lokaal luistert, `127.0.0.1:8097`, voor
  controles met curl), in productie een eigen netwerk `dso-lookup` waarvan crm-backend
  het enige andere lid is.
- Geen KrakenD-route, geen nginx-location, geen `*-doc-gen`-container, geen regel in
  `krakend-builder.yaml`. De browser gebruikt de routes van crm-backend,
  `GET /dso/operators` en `GET /dso/resolve`, die deze service aanroepen.
- **Zet hem nooit in `ANNEX_CATALOG_*`**: crm-backend stopt bij het opstarten op een
  annexnaam die het niet kent.
- Geen authenticatie, geen CORS, geen database, geen broker.

## API

Elke body is verpakt als `{"data": <payload>, "error_code": 0}`;
`src/modules/dso/infra/dso-lookup.client.ts` in crm-backend pakt hem uit en leest de
types van `src/modules/dso/domain/dso.types.ts`, veld voor veld.

| Route | Antwoord |
|---|---|
| `GET /dso/resolve?postcode=&ins=&section=` | `ResolveOut`: op postcode, eventueel verfijnd per gemeente (`ins`) en dan per sectie |
| `GET /dso/resolve-address?nis_code=&postcode=&post_name=` | `ResolveOut`: op het adres dat de BeST-kiezer teruggaf (de NIS-code van 5 cijfers, met TRIM en exact vergeleken; elke INS volgt `[1-9][0-9]{4}`, dus de gehele vorm is dezelfde tekenreeks, en elke andere waarde geeft `not_found`, nooit een 422) |
| `GET /dso/operators` | `OperatorOut[]`: de catalogus, eerst de DNB's in de volgorde `GRD_OPTIONS` van de toepassing, Elia als laatste |
| `GET /dso/ean/{ean}/check` | `{format, belgian_prefix, gs1_check, known_operator}`: alleen informatief, geeft de EAN nooit terug |
| `GET /dso/meta` | `{data_version, version_date, communes, communes_by_region: {BRU, VLA, WAL}, rows, operators}` |
| `GET /health/liveness` | `{"status": "alive"}` |
| `GET /health/readiness` | 200 of 503, `{"status", "checks": {"data": …}}` |
| `GET /healthz` | Alias van `/health/readiness` (de naam die de specificatie gebruikt) |

`ResolveOut` is `{status, communes?, sections?, result?, data_version}` met een
`status` uit:

- `ok`: `result` = `{ins, commune: {fr, nl, de}, section | null, region, regulator,
  elec: {operator, designated}, gas: {status, operator | null}, flags, note | null,
  source_ids, valid_from | null, data_version}`. `elec.operator` is de DNB die het net
  beheert (die op de factuur, en die het meterformulier vooraf invult);
  `elec.designated` is de wettelijk aangewezen DNB, als noot getoond wanneer die
  verschilt.
- `choose_commune`: de postcode omvat meerdere gemeenten (1040, 1050, 1804);
  `communes` somt ze op, elk `{ins, names, region, province}`.
- `choose_section`: de gemeente heeft secties met verschillende beheerders; `communes`
  bevat die ene gemeente en `sections` haar kandidaten, elk `{section_key, names,
  postcodes, elec_operator}` (de aanroeper kan dus vooraf invullen wanneer ze allemaal
  overeenstemmen, zoals in Baarle-Hertog).
- `not_found`: die postcode of NIS-code bestaat niet. **Een onjuist gevormde invoer is
  een antwoord, nooit een 422.**

Het algoritme is `scripts/lookup.py` van belgian-dso-map, regel voor regel overgezet in
`api/dso/lookup.py` (normalisatie: TRIM van exact `[ \t\r\n ﻿]`, postcode
`^[0-9]{4}$`, namen in NFKD zonder diakritische tekens, kleine letters, alleen
`[a-z0-9]`; een filter zonder treffer wordt genegeerd; een postnaam die gelijk is aan
de naam van de gemeente kiest nooit een sectie). De regulator volgt uit het gewest:
`BRU` → `BE-BRU-BRUGEL`, `VLA` → `BE-VLA-VREG`, `WAL` → `BE-WAL-CWAPE` (de codes van
`reference/regulators.json` in de monorepo, waarvan de `active`-vlaggen hier niets
veranderen).

## Health

`/health/readiness` (en `/healthz`) geeft alleen 200 wanneer:

1. de gegevens geladen zijn (een `data/` dat zijn controles niet doorstaat, laat het
   **opstarten** mislukken, dus de container draait nooit half);
2. de uit de bestanden hertelde aantallen gelijk zijn aan de `counts` van
   `data/release.json` en de geleverde versie die van `release.json` is;
3. voor een gepubliceerde versie (zonder `-draft`) die geen fixture is
   (`DSO_DATA_FIXTURE`, geweigerd in productie): de nationale aantallen,
   565 gemeenten = 261 WAL / 285 VLA / 19 BRU.

De verwachte aantallen komen uit de gegevens zelf, dus de minifixture (6 gemeenten) is
ook klaar; de nationale controle is de extra bewaking van een echte versie. De
healthchecks van compose (ontwikkeling en productie) bevragen `/health/readiness` met
urllib van Python: de image heeft geen curl.

## Gegevens

`data/` is een versie van belgian-dso-map, byte voor byte (`.gitattributes`:
`data/** -text`), zonder het optionele `REPORT.md`. **Alleen `scripts/sync_data.py`
schrijft ze**, en het herschrijft tegelijk `ATTRIBUTION.md` op basis van dat van de
versie:

```bash
python scripts/sync_data.py --latest --if-newer            # dit voert de update uit; geen token nodig (DSO_MAP_TOKEN optioneel)
python scripts/sync_data.py --version 2026.10.0            # een gepubliceerde versie; door ze te noemen kan data/ worden teruggezet
python scripts/sync_data.py --latest --check               # exitcode 10 als er een nieuwere versie bestaat, schrijft niets
python scripts/sync_data.py --from-dir ../../belgian-dso-map/dist [--dry-run]
python scripts/sync_data.py --from-dir ../../belgian-dso-map/.build/mini --allow-draft
```

De opties en exitcodes zijn die van `scripts/sync_dso.py` van de website (`--version` |
`--latest`, `--from-dir`, `--allow-draft`, `--if-newer`, `--dry-run`, `--pr-body`,
`--github-output`; 0 gesynchroniseerd of actueel, 1 geweigerd of mislukt, 2 verkeerd
gebruik), plus `--check` (exitcode 10) en `--data-dir`. `--latest` leest GitHub en
wordt geweigerd met `--from-dir`; een versie die GitHub als `draft` of `prerelease`
markeert, wordt geweigerd; alleen URL's van de vorm
`/repos/{repo}/releases/assets/…` worden opgehaald, en omleidingen alleen via https.
belgian-dso-map is openbaar, dus GitHub wordt **anoniem** gelezen wanneer
`DSO_MAP_TOKEN` ontbreekt of leeg is (geen enkele `Authorization`-header). Stel het
alleen in op een GitHub-token om de anonieme limiet van de API op te heffen
(60 verzoeken per uur en per IP; een synchronisatie doet er een tiental): het wordt
verstuurd in een header die niet wordt doorgestuurd, nooit naar de ondertekende
opslag-URL waarnaar de bestanden omleiden, en wordt nooit afgedrukt. Geen enkele andere
variabele (`GITHUB_TOKEN`, `GH_TOKEN`) wordt gelezen.
Eén bewust verschil: een versie die **ouder** is dan die van `data/` wordt geweigerd,
tenzij ze met `--version` wordt genoemd (een terugdraaiing), terwijl `sync_dso.py`
doorgaat met een opmerking.

Voordat er iets wordt geschreven, wordt de versie precies zo gecontroleerd als de
service dat bij het opstarten doet: elk aanwezig bestand staat in `SHA256SUMS` en klopt,
de vereiste bestanden zijn aanwezig, het CSV-dialect en de kolomkoppen zijn die van het
contract, één `data_version` is gelijk aan `release.json`, de inhoudsdata kloppen
(`first_seen` ≤ `changed_at` ≤ de datum van de versie op elke regel; `valid_from` en de
`retrieved_at` van elke bron op of vóór die datum), de grammatica van `CHANGES.md`
klopt, elke vermelde beheerder, noot en bron bestaat, geen enkele regel is toegewezen
aan een beheerder met `lookup:false` (Elia), en de aantallen kloppen. Daarna wordt
`data/` volledig of helemaal niet vervangen. Een concept (`-draft`) wordt geweigerd,
behalve met `--from-dir … --allow-draft`, en de CI weigert conceptgegevens
(`tests/test_ci_guard.py`, en `build.yml` voordat het een image bouwt).

De **gegevensupdate** (`.github/workflows/dso-data-bump.yml`) voert
`sync_data.py --latest --if-newer` uit (of `--version <tag> --if-newer`) en opent de
pull request "dso data X → Y" met een persoonlijk toegangstoken
(`DSO_DATA_BOT_TOKEN`: lezen/schrijven van Contents en Pull requests, alleen in deze
repository), omdat een pull request die met `GITHUB_TOKEN` is geopend geen enkele
workflow start en de vectoren van de nieuwe versie nergens zouden draaien. De versie
lezen vraagt geen geheim: de synchronisatiestap krijgt de `github.token` van de run als
`DSO_MAP_TOKEN`, alleen tegen de anonieme limiet. Samenvoegen publiceert een nieuwe
`:main-prod`: `build.yml` volgt `data/**`. De update start op drie manieren:

- `repository_dispatch` `dso-data-released`, verstuurd door de `release.yml` van
  belgian-dso-map met `client_payload.version` wanneer het geheim
  `DSO_CONSUMERS_TOKEN` daar is ingesteld. GitHub **schakelt de cron van een openbare
  repository uit na 60 dagen zonder activiteit** (een e-mail, geen rode controle), en
  er verschijnt ongeveer om de zes maanden een versie: deze oproep houdt de update in
  leven. Kon `release.yml` hem niet versturen (geen geheim, of een mislukte oproep: de
  opmerking of waarschuwing zegt het), start de workflow dan na de versie met de hand
  (schakel hem eerst weer in als GitHub hem heeft uitgeschakeld).
- De cron, maandag om 06:00 UTC: het vangnet zolang hij actief is.
- Met de hand (`workflow_dispatch`); een daar opgegeven `version` wordt vastgezet, ook
  als ze ouder is.

## Privacy

Een opzoeking bevat een postcode, een NIS-code, een postnaam en een EAN — een thuis. De
service bewaart er niets van:

- uvicorn draait in beide Dockerfiles met `--no-access-log` (de toegangsregel bevat de
  volledige querystring, en in productie exporteert de rootlogger naar OTLP);
- geen enkele route logt een parameter. De gedeelde foutpaden van billing (413, 504,
  domeinfouten en onbehandelde fouten) loggen het URL-pad, dat voor
  `/dso/ean/{ean}/check` de EAN bevat: `main.py` vervangt het door het routesjabloon
  met een filter op die loggers, voordat een handler de regel ziet;
- het enige spoor is de teller `dso.lookups.total{endpoint, status}`, waarvan de labels
  gesloten woordenlijsten zijn (`core/metrics.py` weigert al de rest);
- de HTTP-metrieken van de OpenTelemetry-instrumentatie dragen het routesjabloon, nooit
  het pad of de querystring; er worden geen spans vastgelegd (het platform heeft geen
  `TracerProvider`), en dat mag ook niet voordat `/dso/` ervan is uitgesloten;
- de EAN-controle geeft de EAN nooit terug; de NIS-code wordt nooit opgeslagen, door
  niemand.

`tests/test_privacy.py` controleert dit allemaal.

## Configuratie

| Variabele | Standaard | |
|---|---|---|
| `ENV` | `local` | `local`, `test`, `staging`, `production`. Documentatie (`/docs`) alleen in `local` |
| `DSO_DATA_DIR` | `<repo>/data` | De map van de versie. Leeg laten: de image levert ze mee |
| `DSO_DATA_FIXTURE` | `false` | `true` alleen om een fixture te leveren die geen concept is; geweigerd in productie |
| `LOGGING_TOKEN`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | leeg | Vereist in productie (logs en metrieken via OTLP/HTTP) |
| `LOGGING_TRACES_URL` | leeg | Gedeclareerd voor gelijkheid met de annexen; ongebruikt |
| `DEFAULT_LOCALE` | `fr-BE` | |

## Ontwikkeling

Python 3.12 (`py -3.12` op Windows).

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements/all.txt

ENV=test .venv/Scripts/python.exe -m pytest -q     # zonder Docker: geen database
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy .

.venv/Scripts/uvicorn main:app --reload --no-access-log   # http://127.0.0.1:8000/docs
docker build -f Dockerfile.production -t dso-lookup:local .
```

De tests sturen elke regel van `data/vectors.csv` door HTTP, controleren de strengheid
van de lader op beschadigde kopieën van `data/`, vergelijken de overzetting met
`scripts/lookup.py` van belgian-dso-map wanneer die repository naast de monorepo staat,
en oefenen `sync_data.py` (succes, weigering, ongewijzigde `data/` bij een fout, geen
stille terugzetting, `--if-newer`, `--dry-run`, het GitHub-pad met een nagebootste API).

CI: `lint.yml` (ruff, opmaak, mypy), `test.yml` (pytest, zonder servicecontainer),
`build.yml` (`Dockerfile.production` → `ghcr.io/optimce/dso-lookup:main-prod`, met het
label `optimce.dso.data-version`), `dso-data-bump.yml`, `notify_monorepo_update.yml`.

## Afwijkingen van de specificatie

- De `/healthz` van de specificatie is de `/health/readiness` van billing; `/healthz`
  blijft als alias.
- `gh release download` is de REST-API van GitHub (`urllib`,
  `add_unredirected_header`) plus `--from-dir`: `gh` is niet geïnstalleerd op de
  machines van de ontwikkelaars.

## Bijdragen

Bijdragen zijn welkom! Lees de
[bijdragerichtlijnen](../CONTRIBUTING.md) en onze
[Gedragscode](../CODE_OF_CONDUCT.md) voordat u een issue of pull request opent.

## Beveiliging

Om een beveiligingslek te melden, volg het
[beveiligingsbeleid](../SECURITY.md) — open geen openbaar issue.

## Licentie

De code is gelicentieerd onder de [Apache-licentie 2.0](../LICENSE), die `data/` niet
dekt. De gegevens in `data/` zijn een versie van de openbare dataset
[belgian-dso-map](https://github.com/OptimCE/belgian-dso-map), samengesteld en
**door OptimCE gelicentieerd onder de
[licentie Creative Commons Naamsvermelding 4.0 Internationaal (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)**.
Wie ze hergebruikt, vermeldt "belgian-dso-map, OptimCE" met de gebruikte versie, de
datum ervan en een link naar de repository, en zegt of ze zijn gewijzigd. De bronnen
behouden hun eigen licenties en vereisten voor naamsvermelding:
[ATTRIBUTION.md](../ATTRIBUTION.md) vermeldt ze, en wordt bij elke gegevensversie
herschreven door `scripts/sync_data.py`.
