# wedstrijdblad-sync

Een lokale, deterministische Windows-tool om een PSD-selectie gecontroleerd als
**onbewaard e-Kickoff-concept** over te zetten. Er is geen LLM, API-key of
cloud-backend nodig.

De app vraagt steeds om zichtbare PSD- en e-Kickoff-logins, toont een voorstel,
en vereist expliciete bevestigingen voor conceptacties. Ze bevat geen codepad
voor e-Kickoff **Bewaren** of **Versturen**: de afgevaardigde doet de laatste
controle en eventuele bewaring zelf.

Een optionele, standaard uitgeschakelde supportmelding kan na een fout een
vooraf ingevuld GitHub-formulier openen. De gebruiker leest en verstuurt dat
zelf; de app gebruikt geen GitHub-token of API en verstuurt uitsluitend vaste,
persoonsvrije foutcategorieën.

## Downloads

Een succesvolle GitHub Actions-run publiceert een Windows x64-artefact met de
executables en [START-HERE-WINDOWS.md](START-HERE-WINDOWS.md). Onderteken en
controleer een artefact volgens [WINDOWS-RELEASE-CHECKLIST.md](WINDOWS-RELEASE-CHECKLIST.md)
voor je het aan eindgebruikers verspreidt.

## Gebruik en veiligheid

- [Korte Windows-handleiding](START-HERE-WINDOWS.md)
- [Technische runtime- en authenticatiedocumentatie](STANDALONE-RUNTIME.md)
- [Volledige gebruikers- en ontwerpdocumentatie](README-wedstrijdblad-sync.md)
- [Security policy](SECURITY.md)

Gebruik de tool uitsluitend met een account en testwedstrijdblad waarvoor je
toestemming hebt. Test geen officiële verzending via automatisatie.
