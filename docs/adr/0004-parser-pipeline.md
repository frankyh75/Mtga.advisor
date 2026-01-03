# ADR 0004: Parser-Pipeline

## Status
Accepted

## Kontext
Collection-Export basiert auf lokalen MTGA-Logs. Log-Formate können variieren, rotieren oder unvollständig sein. Eine robuste Pipeline braucht klare Quellenpriorität und Validierungsschritte.

## Entscheidung
- **Quellenpriorität:** `Player.log` ist kanonisch; `Player-prev.log` dient der Rotation/Cache-Ergänzung.
- **Unsichere Quellen:** `output_log.txt` ist nur Best-Effort und darf keine „complete“-Claims begründen.
- **Pipeline:**
  1. Pfad-Discovery pro OS.
  2. Existenz-/Lesbarkeitscheck.
  3. Parse der relevanten Events/Snapshots.
  4. Auswahl der neuesten vollständigen Snapshot-Sequenz.
  5. Ausgabe inkl. Unsicherheiten/Warnungen.

## Validierungsaufgaben (unklare Log-Pfade/Settings)
- **Windows Log-Verzeichnis:** Bestätigen, ob `Player.log` unter `AppData\LocalLow\Wizards Of The Coast\MTGA` bleibt oder alternative Pfade in Steam/Proton existieren.
- **macOS Log-Pfad:** Verifizieren des Standardpfads unter `~/Library/Logs/Wizards Of The Coast/MTGA` und ob Sandbox/Steam-Varianten abweichen.
- **Settings-Flag für Detailed Logs:** Prüfen, ob ein konfiguratives Flag die Ausgabe von `Player.log` beeinflusst (z. B. „Detailed Logs“ in MTGA).
- **Rotation/Cache:** Verifizieren, ob `Player-prev.log` immer vorhanden ist und wie lange rotierte Logs aufbewahrt werden.
- **Log-Encoding/Locale:** Prüfen, ob Nicht-UTF8 oder lokalisierte Inhalte das Parsing beeinflussen.

## Konsequenzen
- Parser muss defensiv sein und Unsicherheiten dokumentieren.
- Künftige Änderungen der Log-Pfade erfordern Anpassungen der Discovery-Logik.
- Fehlende Validierung wird als Warnung in den Outputs sichtbar gemacht.
