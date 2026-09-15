# Wedstrijdblad Sync — starten op Windows

Gebruik deze handleiding met **Wedstrijdblad Dry-run Wizard**. De app leest PSD en e-Kickoff en
kan, na twee expliciete bevestigingen, spelers als onbewaard concept zetten. Bewaren en officieel
versturen bestaan niet in de app: de afgevaardigde doet die laatste controle en eventuele bewaring
zelf in e-Kickoff.

## Voor je begint

1. Sluit geen gewone programma's of browservensters. Alleen wanneer de app meldt dat het
   **Wedstrijdblad Sync-browserprofiel al open** is, sluit je uitsluitend dát aparte
   Chrome/Edge-venster volledig en start je de stap opnieuw.
2. Noteer het nummer van het doel-wedstrijdblad, bijvoorbeeld `4358637`.
3. Kies de PSD-wedstrijd die de selectie bevat. Dat mag een vorige wedstrijd zijn.
4. Weet hoeveel basis- en wisselspelers voor deze reeks toegelaten zijn. Drie wissels is slechts
   een voorgestelde beginwaarde, geen limiet van de app.

## De veilige controle uitvoeren

1. Dubbelklik `wedstrijdblad-dryrun-wizard.exe`.
2. Vul **Doel-wedstrijdblad-ID** in. Controleer ieder cijfer.
3. Vul de **PSD-brondatum** en **PSD-tegenstander** in. Deze beschrijven de bronselectie, niet
   noodzakelijk de doelwedstrijd. De wizard zoekt hiervoor in PSD's volledige overzicht
   **Wedstrijden**, dus ook een afgeronde vorige wedstrijd mag als bron dienen.
4. Vul **Vereiste basis** en **Vereiste wissels** in.
5. Klik **1. Lees PSD-selectie**.
6. Als PSD een loginvenster toont, meld dan daar zelf zichtbaar aan. Vraagt PSD om een account of
   profiel te kiezen, kies uitsluitend het clubprofiel met toegang tot de selecties — niet een
   profiel met beperkte rechten. De app leest of bewaart geen wachtwoord en gokt nooit op een
   profiel. Wacht tot het PSD-dashboard geladen is.
7. Controleer de getoonde lijst: rugnummers, basis/wissel en het totale aantal.
8. Kies expliciet één **Kapitein** en één **Doelman**. Kies alleen een kapitein uit de basis.
   Als PSD aanwezige staf bevat, verschijnt daarna een klein venster waarin je de e-Kickoff-functie
   per staflid bevestigt. De voorgestelde mappings, zoals `Trainer → T1`, zijn zichtbaar; je kunt
   ze aanpassen vóór je verdergaat.
9. Typ exact `BEVESTIG <doel-wedstrijdblad-ID>`, bijvoorbeeld `BEVESTIG 4358637`.
10. Klik **2. Controleer doelblad en toon voorstel**.
11. Meld, indien nodig, zelf aan in het RBFA/e-Kickoff-venster. Wacht tot de app verdergaat.
12. Lees het resultaat. Bij een leeg doelblad toont de app de exacte spelersacties. Bevat het blad
    al spelers, kies dan voor elke PSD-speler expliciet een bestaand e-Kickoff-item of
    **Nieuw toevoegen**. De app gokt nooit op basis van een naam.
13. Bevat PSD aanwezige staf, doe dan hetzelfde in het zichtbare **Bestaande staf koppelen**-
    venster. Kies per PSD-staflid een bestaand e-Kickoff-item of **Nieuw toevoegen**. De app
    vergelijkt of normaliseert geen stafnamen zelf.
    Als je een bestaande speler of stafrij niet koppelt, toont de app die rij expliciet en vraagt
    ze nogmaals of die als verwijderactie in het voorstel mag staan.

Na deze stappen is er nog steeds niets naar PSD of e-Kickoff geschreven. Als het doelblad nog
steeds leeg is, toont de wizard daarna een tweede, plan-specifieke bevestiging voor **Stap 3**.
Alleen na het exact typen daarvan kan hij de spelers als onbewaard concept toevoegen. Bewaren en
versturen hebben geen knop of codepad in de app: de afgevaardigde controleert en bewaart zelf in
e-Kickoff.

## Wanneer stoppen?

Stop en controleer de gegevens opnieuw wanneer de app meldt dat:

- de PSD-selectie niet precies het gevraagde aantal basis- en wisselspelers bevat;
- een rugnummer dubbel is;
- kapitein of doelman onduidelijk is;
- een bestaande e-Kickoff-speler niet expliciet kan worden gekoppeld;
- de wedstrijd-ID, datum of tegenstander niet eenduidig is;
- de RBFA- of PSD-login verlopen is.

Deze meldingen zijn bescherming, geen fout die je moet omzeilen.

## Wat de app bewust niet doet

- geen PSD-selectie aanpassen;
- geen spelers of staf kopiëren via de onveilige “vorige wedstrijd”-knoppen;
- geen concept bewaren;
- geen wedstrijdblad versturen of afsluiten;
- geen wachtwoorden, cookies, namen of geboortedata in haar lokale controlebestand opslaan.

De wizard verwerkt ook een gevuld spelers- en stafblad, maar alleen na een expliciete
één-op-één-koppeling in het zichtbare venster. Niet-gekoppelde bestaande rijen verschijnen
zichtbaar als verwijderactie in het voorstel. Een nieuwe stafnaam die niet als exact één
e-Kickoff-autocomplete-resultaat terugkomt, stopt veilig; kies dan een bestaand item of corrigeer
de bron. **Versturen** en afsluiten blijven buiten de automatisatie: die knop bestaat niet in de
tool en wordt door de afgevaardigde zelf gecontroleerd.

## Taal van de aanmelding

De RBFA-aanmelding mag in een andere browsertaal verlopen: de app herkent de terugkeer naar
e-Kickoff via de applicatie-URL, niet via de tekst op het inlogscherm. De huidige e-Kickoff
bediening is geverifieerd voor Nederlands en Engels. Verschijnen tab- of actieknoppen in een
andere taal, dan stopt de app veilig met een duidelijke melding; verander de browsertaal tijdelijk
naar Nederlands of Engels en maak geen eigen gok op een knop.

## Bij een probleem

Noteer alleen het wedstrijdblad-ID en de foutmelding. Deel geen wachtwoord, browsercookie,
spelernaam, geboortedatum of screenshot met persoonlijke gegevens in een supportvraag.
