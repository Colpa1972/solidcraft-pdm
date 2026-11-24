# PDM für SolidCraft – local part management

**[English](#english) · [Deutsch](#deutsch)**

---

# English

A small **local product data management (PDM)** tool for [SolidCraft](https://github.com/tangency-labs/solidcraft): working folder + vault of immutable versions + database, operated in your browser. Python standard library only, no installation, no cloud. Version 0.2.

## Start
Double-click `PDM-starten.command` (first time: right-click → Open), or run `python3 pdm.py`. The app opens in your browser (local only, `http://127.0.0.1:8765`). Quit by closing the terminal window.

## Folders (created on first start)
- `~/Documents/PDM/Arbeit` – work folder: save your SolidCraft files (`.sc`) here.
- `~/Documents/PDM/Tresor` – vault: protected, immutable versions (do not edit by hand).
- `~/Documents/PDM/pdm.sqlite` – database. Other location: environment variable `PDM_ROOT`.

## Workflow
1. Design in SolidCraft and save the file in the work folder.
2. PDM: tab "Arbeitsordner" → check in as a new part (number: empty = automatic from 1000; name, material, project).
3. Keep working: double-click a part to check it out and open it in SolidCraft.
4. Done: select the part → "Einchecken" – a new version goes into the vault, older ones are kept.
5. "Freigeben" turns a version into revision A, B, … which stays immutable.
6. The search field searches number, name, description, material, project and designer. "Tresor prüfen" compares all files with their checksums. "Auschecken verwerfen" moves the work file to `Arbeit/_verworfen`; nothing is deleted.

## 3D view
SolidCraft stores only the computation model in `.sc` (no mesh). For the rotatable 3D view export a GLB with the **same name** into the work folder (File → Export → GLB, e.g. `Shaft.sc` + `Shaft.glb`). On check-in the PDM stores it in the vault. Drag = rotate, wheel = zoom, Shift + drag = pan. Works completely offline. Sample files: `beispiele/`.

## Backup
Copy the whole `~/Documents/PDM` folder to an external drive regularly.

The user interface is German. License: MIT (see `LICENSE`); third-party parts: `THIRD-PARTY.md`.

---

# Deutsch

Kleine **lokale Teileverwaltung (PDM)** für [SolidCraft](https://github.com/tangency-labs/solidcraft): Arbeitsordner + Tresor mit unveränderlichen Versionen + Datenbank, Bedienung im Browser. Nur Python-Standardbibliothek, keine Installation, keine Cloud. Version 0.2.

## Start
Doppelklick auf `PDM-starten.command` (beim ersten Mal: Rechtsklick → Öffnen) oder `python3 pdm.py`. Das Programm öffnet sich im Browser (nur lokal, `http://127.0.0.1:8765`). Beenden: Terminal-Fenster schließen.

## Ordner (werden beim ersten Start angelegt)
- `~/Documents/PDM/Arbeit` – hier speicherst du in SolidCraft deine Dateien (`.sc`).
- `~/Documents/PDM/Tresor` – gesicherte, unveränderliche Versionen (nicht von Hand ändern).
- `~/Documents/PDM/pdm.sqlite` – Datenbank. Anderer Ort: Umgebungsvariable `PDM_ROOT`.

## Ablauf
1. In SolidCraft konstruieren und die Datei im Ordner Arbeit speichern.
2. PDM: Reiter „Arbeitsordner“ → „Als neues Teil einchecken“. Nummer (leer = automatisch ab 1000), Name, Material, Projekt eintragen.
3. Weiterarbeiten: Teil doppelklicken → es wird ausgecheckt und in SolidCraft geöffnet.
4. Fertig: Teil wählen → „Einchecken“ → neue Version im Tresor, die alte bleibt erhalten.
5. „Freigeben“ macht eine Version zur Revision A, B, … und sie bleibt dauerhaft unveränderlich.
6. Suchfeld oben: sucht in Nummer, Name, Beschreibung, Material, Projekt, Konstrukteur. „Tresor prüfen“ vergleicht alle Dateien mit ihren Prüfsummen. „Auschecken verwerfen“ legt die Arbeitsdatei in `Arbeit/_verworfen` ab, nichts wird gelöscht.

## 3D-Ansicht
SolidCraft speichert in der `.sc`-Datei nur das Rechenmodell (kein Dreiecksnetz). Für die drehbare 3D-Ansicht eine GLB-Datei mit **gleichem Namen** in den Arbeitsordner exportieren (Datei → Exportieren → GLB, z. B. `Getriebewelle.sc` + `Getriebewelle.glb`). Beim Einchecken übernimmt das PDM sie in den Tresor. Ziehen = drehen, Mausrad = zoomen, Umschalt + Ziehen = verschieben. Funktioniert komplett offline. Beispieldateien: `beispiele/`.

## Sicherung
Den ganzen Ordner `~/Documents/PDM` regelmäßig auf eine externe Platte kopieren.

Die Oberfläche ist deutsch. Lizenz: MIT (siehe `LICENSE`); Fremdsoftware: `THIRD-PARTY.md`.
