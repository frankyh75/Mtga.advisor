# Phase 1 Checkliste (End-to-End)

1. [x] Logs werden auf Windows LocalLow gefunden (Player.log + Player-prev.log), Fallback optional.
2. [x] InventoryInfo-Snapshot wird erkannt; cards bleiben unknown ohne PlayerInventory.GetPlayerCardsV3.
3. [x] Inventory.Updated Deltas werden nur nach Cards-Snapshot auf Karten angewendet.
4. [x] collection.json wird immer erzeugt (auch bei Warnungen oder Partial/Unknown).
5. [x] run-report.json enthaelt Log-Metadaten (Pfad, Groesse, mtime).
6. [ ] Warnungen bei fehlenden Snapshots oder Detailed Logs werden ausgewiesen (keine explizite Warnung fuer fehlenden Snapshot/Detailed Logs).
7. [x] Export ist offline-only, keine Netzaufrufe im Kernpfad.
8. [x] Minimaler Testlauf mit lokalen Logs bestaetigt die Ausgabe.
