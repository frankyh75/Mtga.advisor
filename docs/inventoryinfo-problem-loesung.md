# Loesung: InventoryInfo statt GetPlayerCardsV3

## Problem
In manchen Logs erscheint nur `InventoryInfo`, aber kein `PlayerInventory.GetPlayerCardsV3`.
Ohne Karten-Snapshot bleibt die Kartenliste leer; Wildcards koennen trotzdem erkannt werden.

## Status im Projekt
- InventoryInfo wird als Wildcards-Snapshot erkannt.
- Karten werden nur bei echtem Karten-Snapshot als "complete" markiert.
- Warnungen fuer fehlenden Snapshot und fehlende Detailed Logs existieren.

## Lessons Learned (Parser-Vergleich)
- Snapshot+Delta ist der Standard (GetPlayerCardsV3 + Inventory.Updated) fuer vollstaendige Karten.
- Fehlender Snapshot ist ein Failure-Mode; ohne Snapshot bleiben cards unknown.
- Detailed Logs fehlen = Failure-Mode; explizite Warnung und Nutzerhinweis.
- Rotation/Cache (Player.log + Player-prev.log) erhoeht Snapshot-Chance.
- Event-Dispatch pro Event-Typ ist robuster als reine Regex-Suche.
- InventoryInfo liefert Wildcards, aber keine Kartenliste (Wildcards-only).
- mtgatool-desktop: Event-Dispatch + Detailed-Logs-Check als Referenz-Strategie.
- rconroy293/mtga-log-client: Snapshot+Delta, Rotation-Pfade, Reconnect-Handling.
- AdamManuel: InventoryInfo/GetPlayerCardsV3 + Regex-Fallback + Snapshot-Caching.
- gathering-gg/parser: Segment-Parser fuer PlayerInventoryGetPlayerCards.
- output_log.txt ist Legacy; Player.log ist der bessere Primary-Input.
- Defensive JSON-Extraktion fuer mehrzeilige Payloads ist Pflicht.

## Naechste Schritte (realistisch)
1. Snapshot-Event im echten Log suchen (GetPlayerCardsV3 oder anderer Event-Name).
2. Falls kein Snapshot: in MTGA Collection/Decks oeffnen, neu exportieren.
3. Wenn weiter kein Snapshot: Log-Snippet sichern und Parser nur mit belegtem Event erweitern.
