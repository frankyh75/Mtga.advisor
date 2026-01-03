# Fixtures: Grenzen und Annahmen

Diese Fixtures dienen ausschließlich als synthetische Beispiele für lokale MTGA-Logs und Export-Artefakte.

## Grenzen
- **Keine echten Daten:** Alle IDs, Zeitstempel und Inhalte sind erfunden und enthalten keine personenbezogenen Informationen.
- **Vereinfachtes Log-Format:** Die Logzeilen sind bewusst reduziert und spiegeln nicht notwendigerweise das aktuelle, vollständige MTGA-Format wider.
- **Nicht vollständig:** Die Fixtures enthalten nur ein kleines Set an Events und Karten-IDs; sie decken keine Randfälle oder Formatänderungen ab.
- **Best-effort-Quelle:** `fixtures/output_log.txt` ist rein illustrativ und darf nicht zur Bewertung von Vollständigkeit genutzt werden.

## Annahmen
- **Snapshot-first-Logik:** `fixtures/Player.log` enthält einen Snapshot (`PlayerInventory.GetPlayerCardsV3`), an den eine synthetische Delta-Aktualisierung angelehnt ist.
- **Rotation:** `fixtures/Player-prev.log` repräsentiert einen älteren Snapshot, um Log-Rotation zu simulieren.
- **Exports:** `out/collection.json`, `out/run-report.json` und `out/raw-samples/` sind Beispielausgaben, die nur Form und Felder illustrieren; sie sind keine formale Spezifikation.
