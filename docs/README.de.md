<p align="center">
  <img src="logo.svg" alt="OptimCE-Logo" width="160">
</p>

# OptimCE — VNB-Suche (interner Dienst)

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/de/)
[![Lizenz](https://img.shields.io/badge/Lizenz-Apache%202.0-blue.svg)](../LICENSE)
[![Datenlizenz](https://img.shields.io/badge/Daten-CC%20BY%204.0-lightgrey.svg)](../ATTRIBUTION.md)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-43a047.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](README.nl.md)

Beantwortet der OptimCE-Plattform eine einzige Frage: **Welcher
Verteilernetzbetreiber (VNB, *gestionnaire de réseau de distribution*,
*netbeheerder*) betreibt das Strom- und das Gasnetz an einer belgischen Adresse?**
crm-backend stellt sie, während ein Benutzer einen Zähler erfasst, um den VNB des
Zählers (`meter_data.grd`) vorauszufüllen. Die Antwort ist ein Hinweis: Der Benutzer
kann sie jederzeit ändern, und wenn dieser Dienst nicht läuft, behält das Formular
einfach, was der Benutzer wählt.

Die Daten werden nicht hier gepflegt. Sie sind eine festgelegte Version von
[`belgian-dso-map`](https://github.com/OptimCE/belgian-dso-map), der einzigen
maßgeblichen Quelle für die Zuordnungen Gemeinde → VNB, und liegen im Image unter
`data/`.

Dieser Dienst ist Teil der [OptimCE](https://www.optimce.be/de/)-Plattform. Der
gesamte Stack läuft aus dem [Monorepo](https://github.com/OptimCE/monorepo).

## Intern, kein Annex

dso-lookup folgt den Konventionen der Annex-Dienste (FastAPI-Gerüst, Logging,
Telemetrie und CI von billing), ist aber **kein Annex**:

- **crm-backend ist der einzige Aufrufer**, über das Docker-Netzwerk: in der
  Entwicklung das Netzwerk `backend` (plus ein nur lokal gebundener Port,
  `127.0.0.1:8097`, für Prüfungen mit curl), in der Produktion ein eigenes Netzwerk
  `dso-lookup`, dessen einziges weiteres Mitglied crm-backend ist.
- Keine KrakenD-Route, keine nginx-Location, kein `*-doc-gen`-Container, kein Eintrag
  in `krakend-builder.yaml`. Der Browser nutzt die Routen von crm-backend,
  `GET /dso/operators` und `GET /dso/resolve`, die diesen Dienst aufrufen.
- **Nie in `ANNEX_CATALOG_*` aufführen**: crm-backend beendet sich beim Start bei
  einem Annex-Namen, den es nicht kennt.
- Keine Authentifizierung, kein CORS, keine Datenbank, kein Broker.

## API

Jeder Antwortkörper ist in `{"data": <payload>, "error_code": 0}` verpackt;
`src/modules/dso/infra/dso-lookup.client.ts` in crm-backend packt ihn aus und liest
die Typen aus `src/modules/dso/domain/dso.types.ts`, Feld für Feld.

| Route | Antwort |
|---|---|
| `GET /dso/resolve?postcode=&ins=&section=` | `ResolveOut`: nach Postleitzahl, bei Bedarf eingegrenzt nach Gemeinde (`ins`) und dann Sektion |
| `GET /dso/resolve-address?nis_code=&postcode=&post_name=` | `ResolveOut`: nach der Adresse aus der BeST-Auswahl (der 5-stellige NIS-Code, mit TRIM bereinigt und exakt verglichen; jede INS entspricht `[1-9][0-9]{4}`, ihre Ganzzahlform ist also dieselbe Zeichenkette, und jeder andere Wert ergibt `not_found`, nie ein 422) |
| `GET /dso/operators` | `OperatorOut[]`: der Katalog, zuerst die VNB in der `GRD_OPTIONS`-Reihenfolge der Anwendung, Elia zuletzt |
| `GET /dso/ean/{ean}/check` | `{format, belgian_prefix, gs1_check, known_operator}`: nur informativ, gibt den EAN nie zurück |
| `GET /dso/meta` | `{data_version, version_date, communes, communes_by_region: {BRU, VLA, WAL}, rows, operators}` |
| `GET /health/liveness` | `{"status": "alive"}` |
| `GET /health/readiness` | 200 oder 503, `{"status", "checks": {"data": …}}` |
| `GET /healthz` | Alias für `/health/readiness` (der Name, den die Spezifikation verwendet) |

`ResolveOut` ist `{status, communes?, sections?, result?, data_version}` mit einem
`status` aus:

- `ok`: `result` = `{ins, commune: {fr, nl, de}, section | null, region, regulator,
  elec: {operator, designated}, gas: {status, operator | null}, flags, note | null,
  source_ids, valid_from | null, data_version}`. `elec.operator` ist der VNB, der das
  Netz betreibt (der auf der Rechnung und der, den das Zählerformular vorausfüllt);
  `elec.designated` ist der gesetzlich benannte, als Hinweis angezeigt, wenn er
  abweicht.
- `choose_commune`: Die Postleitzahl umfasst mehrere Gemeinden (1040, 1050, 1804);
  `communes` listet sie auf, jeweils `{ins, names, region, province}`.
- `choose_section`: Die Gemeinde hat Sektionen mit unterschiedlichen Betreibern;
  `communes` enthält diese eine Gemeinde und `sections` ihre Kandidaten, jeweils
  `{section_key, names, postcodes, elec_operator}` (der Aufrufer kann also
  vorausfüllen, wenn alle übereinstimmen, wie in Baarle-Hertog).
- `not_found`: Diese Postleitzahl oder dieser NIS-Code existiert nicht. **Eine
  fehlerhafte Eingabe ist eine Antwort, nie ein 422.**

Der Algorithmus ist `scripts/lookup.py` aus belgian-dso-map, Zeile für Zeile portiert
in `api/dso/lookup.py` (Normalisierung: TRIM von genau `[ \t\r\n ﻿]`,
Postleitzahl `^[0-9]{4}$`, Namen in NFKD ohne Diakritika, kleingeschrieben, nur
`[a-z0-9]`; ein Filter ohne Treffer wird ignoriert; ein Postname, der dem Namen der
Gemeinde gleicht, wählt nie eine Sektion). Die Regulierungsbehörde ergibt sich aus der
Region: `BRU` → `BE-BRU-BRUGEL`, `VLA` → `BE-VLA-VREG`, `WAL` → `BE-WAL-CWAPE` (die
Codes aus `reference/regulators.json` im Monorepo, deren `active`-Kennzeichen hier
nichts ändern).

## Health

`/health/readiness` (und `/healthz`) ist nur 200, wenn:

1. die Daten geladen sind (ein `data/`, das seine Prüfungen nicht besteht, lässt den
   **Start** scheitern, der Container läuft also nie halb funktionsfähig);
2. die aus den Dateien neu ermittelten Zählungen den `counts` aus `data/release.json`
   entsprechen und die ausgelieferte Version die von `release.json` ist;
3. bei einer veröffentlichten Version (ohne `-draft`), die keine Fixture ist
   (`DSO_DATA_FIXTURE`, in der Produktion abgelehnt): die nationalen Zählungen,
   565 Gemeinden = 261 WAL / 285 VLA / 19 BRU.

Die erwarteten Zählungen stammen aus den Daten selbst, daher ist auch die
Mini-Fixture (6 Gemeinden) bereit; die nationale Prüfung ist die zusätzliche
Absicherung einer echten Version. Die Compose-Healthchecks (Entwicklung und
Produktion) fragen `/health/readiness` mit urllib von Python ab: Das Image hat kein
curl.

## Daten

`data/` ist eine Version von belgian-dso-map, Byte für Byte (`.gitattributes`:
`data/** -text`), ohne deren optionales `REPORT.md`. **Nur `scripts/sync_data.py`
schreibt sie**, und es schreibt zugleich `ATTRIBUTION.md` aus dem der Version neu:

```bash
python scripts/sync_data.py --latest --if-newer            # das führt die Aktualisierung aus; kein Token nötig (DSO_MAP_TOKEN optional)
python scripts/sync_data.py --version 2026.10.0            # eine veröffentlichte Version; mit ihrem Namen lässt sich data/ zurücksetzen
python scripts/sync_data.py --latest --check               # Exit-Code 10, wenn es eine neuere Version gibt; schreibt nichts
python scripts/sync_data.py --from-dir ../../belgian-dso-map/dist [--dry-run]
python scripts/sync_data.py --from-dir ../../belgian-dso-map/.build/mini --allow-draft
```

Die Optionen und Exit-Codes sind die von `scripts/sync_dso.py` der Website
(`--version` | `--latest`, `--from-dir`, `--allow-draft`, `--if-newer`, `--dry-run`,
`--pr-body`, `--github-output`; 0 synchronisiert oder aktuell, 1 abgelehnt oder
fehlgeschlagen, 2 falsche Verwendung), dazu `--check` (Exit-Code 10) und
`--data-dir`. `--latest` liest GitHub und wird zusammen mit `--from-dir` abgelehnt;
eine Version, die GitHub als `draft` oder `prerelease` kennzeichnet, wird abgelehnt;
nur URLs der Form `/repos/{repo}/releases/assets/…` werden abgerufen, und
Weiterleitungen nur über https. belgian-dso-map ist öffentlich, GitHub wird daher
**anonym** gelesen, wenn `DSO_MAP_TOKEN` fehlt oder leer ist (gar kein
`Authorization`-Header). Setzen Sie ihn nur dann auf ein GitHub-Token, wenn Sie die
anonyme Ratenbegrenzung der API aufheben wollen (60 Anfragen pro Stunde und IP; eine
Synchronisierung braucht etwa zehn): Er wird in einem nicht weitergeleiteten Header
gesendet, nie an die signierte Speicher-URL, auf die die Dateien weiterleiten, und nie
ausgegeben. Keine andere Variable (`GITHUB_TOKEN`, `GH_TOKEN`) wird gelesen.
Ein gewollter Unterschied: Eine Version, die **älter** ist als die von `data/`, wird
abgelehnt, außer sie wird mit `--version` benannt (ein Rollback), während `sync_dso.py`
mit einem Hinweis fortfährt.

Bevor etwas geschrieben wird, wird die Version genau so geprüft wie vom Dienst beim
Start: Jede vorhandene Datei ist in `SHA256SUMS` aufgeführt und stimmt überein, die
erforderlichen Dateien sind da, CSV-Dialekt und Kopfzeilen entsprechen dem Vertrag,
ein einziges `data_version` gleicht `release.json`, die Inhaltsdaten stimmen
(`first_seen` ≤ `changed_at` ≤ Datum der Version in jeder Zeile; `valid_from` und das
`retrieved_at` jeder Quelle an diesem Datum oder davor), die Grammatik von
`CHANGES.md` stimmt, jeder referenzierte Betreiber, jeder Hinweis und jede Quelle
existiert, keine Zeile ist einem `lookup:false`-Betreiber (Elia) zugeordnet, und die
Zählungen stimmen. Danach wird `data/` ganz oder gar nicht ersetzt. Ein Entwurf
(`-draft`) wird abgelehnt, außer mit `--from-dir … --allow-draft`, und die CI lehnt
Entwurfsdaten ab (`tests/test_ci_guard.py`, und `build.yml`, bevor ein Image gebaut
wird).

Die **Datenaktualisierung** (`.github/workflows/dso-data-bump.yml`) führt
`sync_data.py --latest --if-newer` aus (oder `--version <tag> --if-newer`) und öffnet
den Pull Request „dso data X → Y“ mit einem persönlichen Zugriffstoken
(`DSO_DATA_BOT_TOKEN`: Lesen/Schreiben für Contents und Pull requests nur in diesem
Repository), denn ein mit `GITHUB_TOKEN` geöffneter Pull Request löst keinen Workflow
aus, und die Vektoren der neuen Version liefen nirgends. Das Lesen der Version braucht
kein Geheimnis: Der Synchronisierungsschritt erhält das `github.token` des Laufs als
`DSO_MAP_TOKEN`, nur gegen die anonyme Ratenbegrenzung. Das Zusammenführen
veröffentlicht ein neues `:main-prod`: `build.yml` beobachtet `data/**`. Gestartet wird
auf drei Wegen:

- `repository_dispatch` `dso-data-released`, gesendet vom `release.yml` von
  belgian-dso-map mit `client_payload.version`, wenn dessen Geheimnis
  `DSO_CONSUMERS_TOKEN` gesetzt ist. GitHub **deaktiviert den Cron eines öffentlichen
  Repositorys nach 60 Tagen ohne Aktivität** (eine E-Mail, keine rote Prüfung), und
  eine Version erscheint etwa alle sechs Monate: Dieser Aufruf hält die Aktualisierung
  am Leben. Konnte `release.yml` ihn nicht senden (kein Geheimnis oder
  fehlgeschlagener Aufruf: sein Hinweis oder seine Warnung sagt es), starten Sie den
  Workflow nach der Version von Hand (aktivieren Sie ihn vorher wieder, falls GitHub
  ihn deaktiviert hat).
- Der Cron, montags 06:00 UTC: das Sicherheitsnetz, solange er aktiv ist.
- Von Hand (`workflow_dispatch`); eine dort angegebene `version` wird festgelegt, auch
  wenn sie älter ist.

## Datenschutz

Eine Abfrage enthält eine Postleitzahl, einen NIS-Code, einen Postnamen und einen EAN
— ein Zuhause. Der Dienst behält nichts davon:

- uvicorn läuft in beiden Dockerfiles mit `--no-access-log` (seine Zugriffszeile ist
  der vollständige Query-String, und in der Produktion exportiert der Root-Logger nach
  OTLP);
- keine Route protokolliert einen Parameter. Die gemeinsamen Fehlerpfade von billing
  (413, 504, Domänenfehler und unbehandelte Fehler) protokollieren den URL-Pfad, der
  bei `/dso/ean/{ean}/check` den EAN enthält: `main.py` ersetzt ihn mit einem Filter
  auf diesen Loggern durch die Routenvorlage, bevor ein Handler den Eintrag sieht;
- die einzige Spur ist der Zähler `dso.lookups.total{endpoint, status}`, dessen Labels
  geschlossene Vokabulare sind (`core/metrics.py` lehnt alles andere ab);
- die HTTP-Metriken der OpenTelemetry-Instrumentierung tragen die Routenvorlage, nie
  den Pfad oder den Query-String; es werden keine Spans aufgezeichnet (in der Plattform
  gibt es keinen `TracerProvider`), und es dürfen keine aufgezeichnet werden, bevor
  `/dso/` davon ausgenommen ist;
- die EAN-Prüfung gibt den EAN nie zurück; der NIS-Code wird von niemandem gespeichert.

`tests/test_privacy.py` prüft all das.

## Konfiguration

| Variable | Standard | |
|---|---|---|
| `ENV` | `local` | `local`, `test`, `staging`, `production`. Dokumentation (`/docs`) nur in `local` |
| `DSO_DATA_DIR` | `<repo>/data` | Das Verzeichnis der Version. Leer lassen: Das Image bringt es mit |
| `DSO_DATA_FIXTURE` | `false` | `true` nur, um eine Fixture auszuliefern, die kein Entwurf ist; in der Produktion abgelehnt |
| `LOGGING_TOKEN`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | leer | In der Produktion erforderlich (Logs und Metriken über OTLP/HTTP) |
| `LOGGING_TRACES_URL` | leer | Zur Parität mit den Annexen deklariert; ungenutzt |
| `DEFAULT_LOCALE` | `fr-BE` | |

## Entwicklung

Python 3.12 (`py -3.12` unter Windows).

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements/all.txt

ENV=test .venv/Scripts/python.exe -m pytest -q     # ohne Docker: keine Datenbank
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy .

.venv/Scripts/uvicorn main:app --reload --no-access-log   # http://127.0.0.1:8000/docs
docker build -f Dockerfile.production -t dso-lookup:local .
```

Die Tests schicken jede Zeile von `data/vectors.csv` durch HTTP, prüfen die Strenge des
Laders an beschädigten Kopien von `data/`, vergleichen die Portierung mit
`scripts/lookup.py` aus belgian-dso-map, wenn dieses Repository neben dem Monorepo
ausgecheckt ist, und testen `sync_data.py` (Erfolg, Ablehnung, unverändertes `data/`
bei Fehlern, kein stilles Zurücksetzen, `--if-newer`, `--dry-run`, der GitHub-Weg mit
einer nachgestellten API).

CI: `lint.yml` (ruff, Format, mypy), `test.yml` (pytest, ohne Service-Container),
`build.yml` (`Dockerfile.production` → `ghcr.io/optimce/dso-lookup:main-prod`, mit dem
Label `optimce.dso.data-version`), `dso-data-bump.yml`, `notify_monorepo_update.yml`.

## Abweichungen von der Spezifikation

- Das `/healthz` der Spezifikation ist das `/health/readiness` von billing; `/healthz`
  bleibt als Alias.
- `gh release download` ist die REST-API von GitHub (`urllib`,
  `add_unredirected_header`) plus `--from-dir`: `gh` ist auf den Rechnern der
  Entwickler nicht installiert.

## Mitwirken

Beiträge sind willkommen! Bitte lesen Sie die
[Richtlinien für Beiträge](../CONTRIBUTING.md) und unseren
[Verhaltenskodex](../CODE_OF_CONDUCT.md), bevor Sie ein Issue oder einen Pull
Request eröffnen.

## Sicherheit

Um eine Sicherheitslücke zu melden, folgen Sie bitte der
[Sicherheitsrichtlinie](../SECURITY.md) — öffnen Sie kein öffentliches Issue.

## Lizenz

Der Code steht unter der [Apache-Lizenz 2.0](../LICENSE), die `data/` nicht abdeckt.
Die Daten in `data/` sind eine Version des öffentlichen Datensatzes
[belgian-dso-map](https://github.com/OptimCE/belgian-dso-map), zusammengestellt und
**von OptimCE lizenziert unter der
[Lizenz Creative Commons Namensnennung 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)**.
Wenn Sie sie weiterverwenden, nennen Sie „belgian-dso-map, OptimCE“ mit der verwendeten
Version, ihrem Datum und einem Link zum Repository und geben Sie an, ob Sie sie
verändert haben. Die Quellen behalten ihre eigenen Lizenzen und Vorgaben zur
Namensnennung: [ATTRIBUTION.md](../ATTRIBUTION.md) führt sie auf und wird bei jeder
Datenversion von `scripts/sync_data.py` neu geschrieben.
