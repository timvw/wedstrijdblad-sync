# Standalone runtime: geen Codex, Claude, ChatGPT of LLM

De uiteindelijke tool is een lokale Python-app. Hij roept geen AI-model, chatdienst of externe
LLM-API aan. `wedstrijdblad_sync.py` is de pure regels-/diff-kern;
`psd_reader.py` en `wedstrijdblad_browser.py` zijn Playwright-lagen en zijn strikt
**read-only**.

## Eenmalige installatie

```bash
python3 -m pip install -r requirements-wedstrijdblad-sync.txt
```

De CLI gebruikt standaard de lokaal geïnstalleerde Microsoft Edge op Windows (`channel="msedge"`)
en een eigen browserprofiel onder `%LOCALAPPDATA%\\WedstrijdbladSync\\browser-profile`.
Voor macOS is de equivalent `--channel chrome`. Er is dus geen Playwright-browserdownload nodig.
De app start daarmee een zichtbaar browservenster.

## Windows-distributie

Op een Windows-buildmachine draait de maintainer eenmaal `build-windows-reader.bat`. Dat levert
`dist\\wedstrijdblad-psd-lezer.exe`, `dist\\wedstrijdblad-psd-wizard.exe`,
`dist\\wedstrijdblad-dryrun-wizard.exe`, `dist\\wedstrijdblad-reader.exe`,
`dist\\wedstrijdblad-dryrun.exe` en
`dist\\wedstrijdblad-controle.exe` op; de eindgebruiker heeft daarna geen Python, Node of extensie
nodig. De controle-app is een eenvoudige Windows-interface
voor de offline preflight en dry-run. Test de executables altijd eerst met
`wedstrijdblad-reader.exe --self-test` en vervolgens
met een aangewezen lege/testwedstrijd. PyInstaller bouwt per besturingssysteem: een macOS-machine
levert geen betrouwbare Windows-executable.
De buildmachine gebruikt de reguliere Python.org-runtime met Tcl/Tk; een uitgeklede/embeddable
Python zonder Tk wordt door het buildscript expliciet geweigerd, omdat de twee wizards grafische
vensters zijn.
Windows 11 op ARM is geschikt als test-VM: het voert een Windows x64-executable uit via de
ingebouwde x64-emulatie. Bouw de te verspreiden x64-release echter op een Windows x64-machine
of met een expliciet x64-Python op Windows ARM. Een native ARM64-Python maakt anders een
ARM64-executable; die is prima voor een ARM-test, maar niet de vervanger voor de x64-release.
`build-windows-reader.bat` kiest standaard `py`; op Windows ARM kan de maintainer dus expliciet
de x64-runtime kiezen, bijvoorbeeld:

```bat
set "WEDSTRIJDBLAD_PYTHON=C:\Users\<gebruiker>\AppData\Local\Programs\Python\Python313-x64\python.exe"
call build-windows-reader.bat
```
Bij de eerste `wedstrijdblad-sync login` logt de gebruiker zelf in op PSD en RBFA/e-Kickoff. De
browser bewaart de sessiecookies in dat lokale profiel; het script leest geen wachtwoord of token.

Voor eindgebruikers staat de korte, niet-technische werkwijze in
[START-HERE-WINDOWS.md](START-HERE-WINDOWS.md). Lever dat bestand samen met de ondertekende exe's.

KeePassXC-Browser is optioneel. Wie die wil gebruiken installeert de extensie eenmalig in dit
aparte profiel en laat KeePassXC de velden lokaal invullen. Handmatig aanmelden blijft volledig
ondersteund.

Sommige PSD-logins verschijnen in een cross-origin iframe. Als KeePassXC dan vraagt om
**Allow Cross-Origin iframes**, schakel die toestemming niet automatisch in: ze verruimt
credential-toegang voor die site. De gebruiker kiest dat zelf expliciet, of vult het wachtwoord
zichtbaar handmatig in. De applicatie heeft die permissie niet nodig en leest het wachtwoord nooit.

PSD gebruikt een tweestaps-login: eerst alleen het e-mailadres en daarna een afzonderlijk
wachtwoordveld. Een browserwachtwoordmanager moet dus beide schermen herkennen; een
“username-only”-instelling vult terecht geen wachtwoord op het tweede scherm. Dit is geen vereiste
voor de applicatie: de zichtbare handmatige login is de betrouwbare fallback en het programma
leest, kopieert of logt geen credentials.

## Gewone werking

Voor een Windows-gebruiker zonder terminalkennis is de volgorde:

1. Open **Wedstrijdblad Dry-run Wizard**. Vul het doel-wedstrijdblad-ID in, plus datum en
   tegenstander van de gekozen PSD-bron. Die bron mag een vorige wedstrijd zijn: de reader zoekt
   in PSD's volledige overzicht **Wedstrijden**, niet alleen in het dashboard met aankomende matchen.
2. Kies het geldige aantal basis- en wisselspelers. Meld zo nodig zichtbaar aan in het aparte
   PSD-venster; de wizard toont uitsluitend de gevonden selectie tijdelijk.
3. Kies kapitein en doelman en typ exact `BEVESTIG <doel-ID>`.
4. De wizard opent e-Kickoff zichtbaar en wacht zo nodig op de lokale RBFA-login. Bij bestaande
   spelers kiest de gebruiker voor iedere PSD-speler expliciet een bestaand item of **Nieuw
   toevoegen**. Daarna toont de wizard het voorstel. Er wordt niets opgeslagen of verstuurd vóór de
   tweede planbevestiging.

**Wedstrijdblad PSD Wizard** en **Wedstrijdblad Controle** blijven beschikbaar als gescheiden,
offline workflow voor wie eerst een lokaal pseudoniem controlebestand wil maken.

De wizard heeft geen knop of browseractie voor PSD opslaan, e-Kickoff-bewaren of e-Kickoff
officieel versturen. Alleen na de tweede exacte planbevestiging kan hij een onbewaard concept
opbouwen; de afgevaardigde controleert en bewaart dat vervolgens zelf in e-Kickoff.

```bash
# 1. PSD alleen lezen: de gebruiker meldt zich zichtbaar aan wanneer nodig.
# Het doel-ID is 4358637; datum en tegenstander zijn de gekozen PSD-bron.
python3 psd_reader.py 4358637 --date 06/09/2026 --opponent "VC Bertem-leefdaal" --output psd-selectie.json --channel msedge

# 2. huidige, volledig schrijfvrije e-Kickoff-reader
python3 wedstrijdblad_browser.py 4358637 --channel msedge

# 3. pure offline planner/diff, met uitsluitend opaque IDs
python3 wedstrijdblad_sync.py psd-selectie.json

# Alternatief: één console-flow voor een *leeg* e-Kickoff-blad.
# Hij stopt als er al spelers op het blad staan; hij wijzigt nooit de browser.
python3 wedstrijdblad_dryrun.py 4358637 --date 06/09/2026 --opponent "VC Bertem-leefdaal" --channel msedge
```

Bij de eerste run logt de gebruiker zichtbaar in. `psd_reader.py` leest daarna in één run:

1. Opent alleen de opgegeven wedstrijd op PSD en vervolgens alleen de tab `Selectie`.
2. Leest per rij uitsluitend het PSD-rugnummer en de actieve `basis`/`wissel`-status.
3. Schrijft een lokaal manifest met pseudonieme speler-ID's; namen worden niet opgeslagen.
4. Toont de selectie tijdelijk en vraagt de gebruiker een kapitein en doelman expliciet te kiezen,
   vóór er een manifest wordt geschreven. Wanneer PSD aanwezige staf bevat, bevestigt de gebruiker
   ook per persoon de zichtbare e-Kickoff-functie en koppelt die persoon later expliciet aan een
   bestaande e-Kickoff-stafrij of **Nieuw toevoegen**. Namen worden nooit automatisch gematcht.

De gebruiker vult vóór het lezen ook het geldige aantal basis- en wisselspelers voor de wedstrijd in.
De voorgestelde waarden 11 + 3 zijn enkel een startwaarde voor deze U17-workflow; vijf of meer
wissels kan wanneer de competitie dat toelaat. De gekozen regel staat expliciet in het manifest en
wordt vóór elk voorstel opnieuw gevalideerd.

De RBFA-SSO-login zelf is niet aan een taal gebonden: de terugkeer naar e-Kickoff wordt via de
applicatie-URL vastgesteld, niet via een vertaald opschrift. De huidige e-Kickoff-tab- en
actiecontroles zijn geverifieerd voor Nederlands en Engels. Bij een andere site-interface-taal
stopt de app veilig zodra een benodigde bediening niet exact herkenbaar is; de gebruiker kiest dan
tijdelijk Nederlands of Engels in plaats van een onbekende knop te laten automatiseren.
De staffuncties gebruiken dezelfde aanpak: de live geverifieerde Nederlandse en Engelse labels
worden uitsluitend via een vaste, exacte aliaslijst naar één canonieke rol omgezet.

`wedstrijdblad_browser.py` leest de huidige e-Kickoff-selectie en rapporteert alleen of spelers
en staf leeg zijn en of `Send` uitgeschakeld is. Het vergelijkt nog geen personen: daarvoor moet
eerst de veilige, eenduidige PSD→e-Kickoff-identiteitskoppeling worden gevalideerd.

Voor de maintainer bestaat er daarnaast `wedstrijdblad-adf-probe.exe <wedstrijdblad-ID>`. Die
opent uitsluitend de bestaande read-only tabs en schrijft een PII-geredigeerde ADF-control-map naar
de console. Hij is geen eindgebruikersstap en bevat evenmin muterende browseracties; de output is
uitsluitend bewijs voor toekomstige, op een testblad te valideren selectors.

`wedstrijdblad-dryrun-wizard.exe` is de aanbevolen één-venster versie van deze flow. Hij toont eerst
de PSD-selectie en vraagt een exacte bevestiging plus kapitein/doelman. Bij een gevuld e-Kickoff-blad
leest hij de huidige rijen uitsluitend voor tijdelijke expliciete speler- en stafkoppelschermen;
geen naam wordt automatisch gematcht of bewaard. Het daaruit opgebouwde PII-vrije plan toont alle
spelers- en staf-add/remove/functieacties vóór de tweede planbevestiging. Een nieuwe staf stopt
veilig wanneer e-Kickoff niet exact één autocomplete-resultaat toont.

Een dry-run draagt een interne, PII-vrije `review_gate`. Die bindt het plan aan de actuele,
read-only momentopname van het doelblad. De concept-writer leest die momentopname onmiddellijk
vóór een wijziging opnieuw en vraagt een tweede plan-specifieke bevestiging; bij een afwijking
stopt hij zonder actie. Dit beschermt tegen een collega die het blad na de review heeft aangepast.

`wedstrijdblad_draft_executor.py` bevat de uitvoeringskern voor de concept-writer: exact één
ADF-actie tegelijk, een herlezing na toevoegen/verwijderen/rugnummer/doelman/kapitein en een
volledige eindherlezing. Hij bevat geen `Save`-actie.
`wedstrijdblad_adf_draft_adapter.py` is de smalle concrete Playwright-laag: hij bevat enkel die
conceptacties en geen officiële verzendactie of generieke click-escape. Hij is gekoppeld aan de
wizard voor zowel lege als gevulde spelersbladen, maar bij een gevuld blad verschijnt eerst de
verplichte expliciete identiteitskoppeling.

De reader heeft bewust geen `apply`, `Save`, `Bewaren`, `Send` of `Versturen`-pad. Alle eigen
browserklikken gaan door een guard die zulke acties weigert. ADF-knoppen zoals “Copy previous
game” worden evenmin aangeklikt, omdat hun servereffect vóór `Save` niet bewezen is.

De conceptroute vereist een werkende PSD-reader, een zichtbare preflight/diff, een nieuwe actuele
snapshot en een expliciete gebruikersbevestiging. `Bewaren` en `Versturen` blijven afzonderlijke,
niet-geïmplementeerde manuele acties.

Voor de maintainer staat de concrete Windows-build- en acceptatiecontrole in
[WINDOWS-RELEASE-CHECKLIST.md](WINDOWS-RELEASE-CHECKLIST.md). De checklist eist expliciet een
leeg niet-officieel testblad; een release verifieert nooit schrijfacties op een echte selectie.

## Authenticatie en beveiliging

- Geen API-key, wachtwoord, cookie of token in configuratie, log of manifest.
- Browsercookies blijven in het lokale, OS-beschermde browserprofiel.
- Verlopen PSD/RBFA-sessie: CLI opent de zichtbare loginpagina en wacht op de gebruiker; daarna
  gaat hij verder.
- Een Oracle ADF-scherm `Page Expired` is eveneens een verlopen RBFA-sessie, niet “geen
  wedstrijdblad”. De reader meldt dit expliciet en voert geen herstel- of schrijfactie uit.
- Geen remote-debugging-poort of koppeling met de dagelijkse browserprofiel nodig.
- Namen en geboortedata worden alleen in browsergeheugen gebruikt voor e-Kickoff-autocomplete en
  tijdelijke weergave; persistente plandata gebruikt enkel opaque IDs.
- Wedstrijdblad `4344375` blijft hard geblokkeerd in de planner én de browserlaag.

## Waarom een apart profiel?

Het maakt de tool onafhankelijk van een open Chrome/Codex-sessie en voorkomt dat automatisatie
met gewone privé-tabs, extensies of andere websites interfereert. De gebruiker moet hoogstens bij
de eerste keer (of na sessieverloop) zichtbaar aanmelden. Als dat aparte profiel nog in een ander
Chrome/Edge-venster openstaat, sluit de gebruiker alleen dat venster volledig en probeert opnieuw;
de app probeert nooit een bestaande browser te kapen of geforceerd te sluiten.
