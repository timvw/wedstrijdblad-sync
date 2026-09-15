# Deterministische PSD → e-Kickoff synchronisatie

`wedstrijdblad_sync.py` is een schrijfvrije planner. Hij leest geen browserdata en schrijft niets
naar PSD of wedstrijdbladen.be. `psd_reader.py` leest PSD via een apart, zichtbaar browserprofiel
en schrijft een lokaal manifest met alleen pseudonieme speler-ID's. Vóór elk plan toont de planner
altijd een preflight met spelers, kapitein, doelman en technische staf. Pas na de exacte,
wedstrijdgebonden bevestiging maakt hij een **dry-run** diff; ook dan voert hij geen ADF-acties uit.

De kern blokkeert automatisch wanneer iets onduidelijk is:

- niet het expliciet geconfigureerde aantal starters en wissels;
- dubbele rugnummers;
- geen of meer dan één kapitein, of een kapitein die geen starter is;
- geen exact één expliciet bepaalde doelman;
- ontbrekende of dubbele technische staf.

Matchblad **4344375** (KAC Betekom) is bovendien hard geblokkeerd; een manifest kan het nooit
plannen.

Gebruik de offline zelftest:

```bash
python3 wedstrijdblad_sync.py --self-test
```

Gebruik uitsluitend opaque IDs in een manifest op schijf. De live browser-adapter mag namen tijdelijk
aan de gebruiker tonen tijdens de preflight, maar bewaart of logt die niet.

```json
{
  "matchsheet_id": "4358637",
  "selection_rules": {
    "starters": 11,
    "substitutes": 3,
    "captain_required": true,
    "captain_must_start": true,
    "goalkeeper_required": true
  },
  "staff_policy": "synchronize",
  "allow_empty_staff": false,
  "desired": [
    {"psd_member_id": "P01", "shirt_number": 1, "role": "starter", "captain": true, "goalkeeper": true}
  ],
  "current": [],
  "desired_staff": [{"psd_staff_id": "S01", "function": "coach"}],
  "current_staff": []
}
```

`selection_rules` hoort bij competitie/ploeg, niet bij de code. De weggelaten waarde is
uitsluitend een tijdelijke compatibiliteitsdefault voor de huidige U17-workflow (11 + 3).
Een toekomstige browserlezer schrijft de regel daarom altijd expliciet in zijn tijdelijke
in-memory plan.

Het aantal wissels is dus configureerbaar: drie is geen vaste PSD- of e-Kickoff-beperking.
In de CLI geef je bijvoorbeeld `--starters 11 --substitutes 5`; in de PSD Wizard staan beide
aantallen bovenaan en worden ze in het lokale manifest vastgelegd. Kies de aantallen die voor de
reeks en wedstrijdvorm gelden; de tool leidt ze nooit stilzwijgend af uit een toevallig eerder blad.

`staff_policy` is bewust verplicht gedrag, geen gok:

- `synchronize`: vergelijk de opgegeven PSD-staf met e-Kickoff. Een lege lijst blokkeert, tenzij
  `allow_empty_staff` expliciet `true` is.
- `preserve`: raak de bestaande e-Kickoff-staf niet aan; een ontbrekende/lege PSD-staf kan dus
  nooit per ongeluk iemand verwijderen.

Voor de toekomstige zichtbare stafkeuze zijn twee bevestigde PSD-voorstellen beschikbaar:
`Trainer` → `T1` en `Delegate` → `Officiële team afgevaardigde` (in een Engelse e-Kickoff-interface:
`Official team delegate`). De valideerlaag zet beide exacte zichtbare labels op dezelfde canonieke
rol. Dat zijn vooraf ingevulde,
wijzigbare suggesties per persoon — geen automatische stafsync. Elke andere PSD-functie vereist
een expliciete e-Kickoff-keuze. De Draft Wizard vraagt die zichtbare koppeling nu per aanwezig
staflid, bouwt een opaque stafdiff en voert die alleen uit als onderdeel van dezelfde verse
review-gate als de spelers. Een onduidelijke nieuwe staf-autocomplete stopt veilig.

Eerst alleen beoordelen:

```bash
python3 wedstrijdblad_sync.py manifest.json
```

Na die zichtbare review wordt de exacte bevestiging gevraagd:

```bash
python3 wedstrijdblad_sync.py manifest.json --confirm
```

De ADF-draftadapter blijft apart en moet na deze preflight nog een nieuwe, plan-specifieke
toestemming krijgen om een matchblad te wijzigen of als concept te bewaren. Officieel versturen is
geen onderdeel van de adapter.

## Gevalideerde e-Kickoff-observatie (14-09-2026)

Matchblad `4358637` is live read-only geopend. De browserreader en de ADF-spelerrijlezer zijn
tegen dezelfde actuele, niet-lege spelersstaat gecontroleerd; de ADF-laag wacht daarbij op de
asynchrone tabelverversing en stopt als een rijstructuur niet eenduidig is. De actuele selectie
wordt bewust niet in documentatie, logs of plannen bewaard. Daarom blijft e-Kickoff-validatie
aanvullend: de lokale preflight en de expliciete koppeling blijven de bron voor een voorstel.

De knoppen `Copy players previous game`, `Copy staff previous game` en `Delete all …` zijn
zichtbaar, maar zijn niet aangeroepen: bij Oracle ADF mag niet worden aangenomen dat een actie pas
bij `Save` serverstatus wijzigt.

De adapter hoort een zelfstandige lokale CLI te zijn, niet een Codex-/chat-run. Zie
[STANDALONE-RUNTIME.md](STANDALONE-RUNTIME.md) voor installatie, eigen browserprofiel en
authenticatie. De bekende PSD-route-aanwijzingen en de afwezigheid van een aangetoonde e-Kickoff-
API staan in [RESEARCH-PSD-EKICKOFF.md](RESEARCH-PSD-EKICKOFF.md).

Voor gebruikers zonder terminal is **Wedstrijdblad Dry-run Wizard** de aanbevolen route. Die app
vraagt doel-wedstrijdblad-ID en een expliciet gekozen PSD-bron, opent de twee zichtbare logins
wanneer nodig, toont de PSD-selectie tijdelijk, laat kapitein/doelman en `BEVESTIG <doel-ID>` kiezen
en toont vervolgens het voorstel. Bij een gevuld blad verschijnt eerst een tijdelijke
één-op-één-koppeling: per PSD-speler kiest de gebruiker een bestaand e-Kickoff-item of **Nieuw
toevoegen**. Pas daarna verschijnt de tweede, plan-specifieke bevestiging voor de conceptactie:
spelers toevoegen/verwijderen en rugnummers/keeper/kapitein instellen. Staf blijft voorlopig
behouden. Bewaren en versturen bestaan niet in de tool.

## PSD-reader (nieuw, alleen lezen)

De PSD-lezer kent expliciet het **doel**-wedstrijdblad-ID en de PSD-bron (datum en tegenstander).
Als bron mag een eerdere wedstrijd gebruikt worden: datum plus tegenstander moeten dan nog steeds
exact één PSD-rij identificeren; het manifest houdt intussen het doel-ID voor de e-Kickoff-dry-run
bij. Hij weigert de beschermde wedstrijd `4344375` én elke Betekom-wedstrijd vóór er een browser
wordt gestart. Hij opent voor een bron altijd het volledige PSD-overzicht **Wedstrijden**: een
historische bron gaat uitsluitend via `wedstrijd-verslag`, een aankomende bron via
`wedstrijd-voorbereiding`. Daarna opent hij alleen `Selectie`; `Opslaan`, `Selectie versturen` en
verwijderen komen niet in zijn actielijst voor.

```bash
python3 psd_reader.py 4358637 --date 06/09/2026 --opponent "VC Bertem-leefdaal" --output psd-selectie.json --channel msedge
python3 wedstrijdblad_sync.py psd-selectie.json
```

De PSD-selectietabel levert volgens de geverifieerde semantiek de actieve basis-/wisselknoppen.
Ze levert nog geen betrouwbaar doelmanveld voor een autonome omzetting. De reader vult daarom
nooit een kapitein of doelman in op basis van een gok. De Windows-PSD-wizard toont de selectie
alleen tijdelijk, vraagt die twee keuzes expliciet en schrijft pas na de exacte bevestiging een
pseudoniem manifest. De planner kan daarna nog steeds blokkeren voor een verkeerde 11+3 of een
dubbel rugnummer.

## Eén veilige end-to-end dry-run

`wedstrijdblad_dryrun.py` blijft de eenvoudige, alleen-lege-blad CLI. De Windows-wizard ondersteunt
daarnaast een gevuld blad via `wedstrijdblad_populated_sync.py`: bestaande personen worden nooit
automatisch gematcht. De gebruiker maakt per PSD-speler een expliciete één-op-één-koppeling of
kiest **Nieuw toevoegen**; niet-gekoppelde huidige rijen blijven zichtbaar als verwijderactie in
het voorstel.

Elk dry-run-voorstel bevat ook een PII-vrije `review_gate`: hashes van exact dit voorstel en de
read-only doelbladsnapshot. De draft-writer moet het doelblad direct vóór elke mutatie opnieuw
lezen, dezelfde hashes terugvinden en de plan-specifieke bevestiging vragen. Wijzigt een andere
gebruiker intussen het blad, dan is het voorstel automatisch ongeldig. De
`wedstrijdblad_adf_draft_adapter.py` vormt daarvoor de smalle Playwright-laag: enkel speler
toevoegen/verwijderen, rugnummer, doelman, kapitein en concept bewaren. Hij heeft geen officiële
verzendactie. De adapter is aan de wizard gekoppeld, maar stopt veilig zodra de live ADF-layout of
een benodigde selector afwijkt van de geverifieerde structuur.

```bash
# Het doel is 4358637; de PSD-bron mag een expliciet gekozen vorige wedstrijd zijn.
python3 wedstrijdblad_dryrun.py 4358637 --date 06/09/2026 --opponent "VC Bertem-leefdaal" --channel msedge
```
