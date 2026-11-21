# Spec: Autonomer Collection-Watcher (offset-frei)

Stand 21.11.2026 · MTGA 2026.65.0 · Unity 6000.3.14 · Helper 1.3.0

## Ziel

Der Scanner erkennt die Sammlung **selbstständig**: er wartet im Hintergrund, bis ein *valider*
Sammlungs-Snapshot lesbar ist, schreibt diesen als Snapshot und meldet andernfalls, warum er noch
wartet. Kein manuelles Probing, keine Messung auf Vermutung.

## Gesicherte Fakten

1. **Log-Pfad ist tot.** `PlayerInventory.GetPlayerCardsV3` wurde Aug 2021 entfernt und nie ersetzt
   (drei eigene Messungen: 0 Treffer; extern bestätigt). Nicht weiter verfolgen.
2. **Metadata ist vollständig lesbar**: 33.308.672 B, Magic `0xAF1BB1FA`. Klassennamen liegen als
   ASCII-Strings darin.
3. **`CardsAndQuantity` ist der Container** der Sammlung. IL2CPP-Objekt, Dictionary-artig:

   | Offset | Inhalt |
   |---|---|
   | `+0x10` | `Int32[] buckets` |
   | `+0x18` | `Entry[] entries` (SZARRAY: Länge `int32` @ `+0x18`, Elemente **inline** ab `+0x20`) |
   | `+0x20` | `int32 count` (belegte Einträge) |
   | `+0x24` | freeList/version |

   `Entry` = **16 B**: `hashCode` (u32, bei int-Keys == grpId), `next` (i32), `grpId` (i32),
   `quantity` (i32).
4. **Verifizierte Messung** (21.11.2026, Client im DeckBuilder):
   `unique = 8610`, `total_copies = 17876`, Verteilung 1:3949 / 2:1801 / 3:1115 / 4:1745, Max 4.
   Anker: `105645` (TVA) = **1** ✓, `104977` (We Say Thee Nay!) = **3** ✓.
   Deckungsgleich mit dem Snapshot vom 02.08.2026 (7874 / 16280, max 4) → gleiche Semantik.
5. **Der Weg über `WrapperController` ist fragil.** Das Manager-Objekt wird vom GC abgeräumt
   (verifizierte Adresse danach mit fremder Klasse belegt). Nicht als Einstieg verwenden.
6. **Alte Validierung ist falsch.** `_combinedRankInfo`-Backref löst auf `HomePageBillboard_Event`
   auf statt `PlayerRankServiceWrapper`; `_validate` liest ungeschützt `0x800000188` →
   `MemoryReadError`, jedes echte Objekt wird verworfen. Entfernen.

## Soll-Ablauf

1. MTGA-Prozess finden, Helper-`target` setzen.
2. Metadata-Basis finden (Magic im Regionsanfang).
3. Namensstring `CardsAndQuantity\0` im Metadata suchen → Kandidatenadressen.
4. Klassenkandidaten: 8-Byte-Zeiger auf Namensadresse, wobei an Kandidatenadresse `+0x10` ==
   Namensadresse.
5. Instanzen je Klasse: 8-Byte-Zeiger auf Klassenstruktur, wobei an Objektadresse `+0` ==
   Klassenstruktur; `count` plausibel (500 < count < 40000), `entries` lesbar.
6. Dekodieren, dann **Struktur-Prüfung** und **Monotonie-Prüfung** (siehe Abschnitt
   „Validierung"); Anker nur als Warnung mitschreiben.
7. Nur bei bestandener Prüfung Snapshot schreiben. Sonst warten und Grund melden:
   `no_process`, `no_metadata`, `no_class`, `no_instance`, `struct_mismatch`, `monotonicity_mismatch`.

## Warteschleife

- Intervall 5 s, solange der Prozess lebt; Gesamt-Timeout konfigurierbar (Default 600 s).
- **Trigger-Optimierung:** `Player.log` auf Szenenwechsel beobachten; nach Wechsel in
  Home / DeckListViewer / DeckBuilder / Collection sofort einen Versuch starten.
  In `Game`/`Match` (Duell) nicht messen.
- **Cache:** ein Voll-Scan über ~6.800 Regionen kostet 90–120 s. Klassen- und Objektadresse cachen;
  Folgeläufe validieren nur die Instanz (Sekunden). Neu-Scan nur bei ungültigem Cache
  (Klassenname passt nicht mehr / Instanz verschwunden / Prozess neu).

## Validierung: bitte NICHT als hartes Anker-Gate

Die Sammlung liegt als **ein** vollständiges Wörterbuch im Speicher. UI-Filter („nur grün",
Sets, Suche) sind reine Anzeige und bauen dieses Objekt nicht neu — gemessen: 8.610 Einträge
bei 17.519 Slots (Lastfaktor 0,49) spricht für ein einziges gewachsenes Dictionary, nicht für
ein Filterergebnis.

Deshalb gilt:

- **Harte Prüfungen (blockieren):**
  1. *Struktur*: `count` == Anzahl belegter Einträge, alle Mengen 1..4, keine doppelten GRP-Ids,
     GRP-Ids im plausiblen Bereich (< 2.000.000), `entries` vollständig lesbar.
  2. *Monotonie*: Arena entfernt keine Karten (bestätigt: „es gibt kein Löschen bei Magic") →
     `unique` und `total_copies` müssen ≥ letzter Snapshot sein, Zuwachs < 20 %.
     Erster Lauf ohne Referenz: nur Struktur-Prüfung.
- **Weiche Prüfung (nur melden):** Anker `105645` (TVA), `104977` (We Say Thee Nay!) werden
  mitgeschrieben. Abweichung → Warnung `anchor_drift`, **kein** Abbruch. Grund: Mengen ändern
  sich legal (Craften, Dusten, neue Kopien), ein hartes Gate würde gültige Daten blockieren.

Anker sind also eine **Kalibrierung / ein Kanarienvogel**, kein Bereitschaftstest.

## Akzeptanzkriterien

- Ein Aufruf wie `python -m scanner.cli collection --wait` liefert ohne Zutun einen Snapshot mit
  bestandener Struktur- und Monotonie-Prüfung.
- Zweiter Aufruf < 15 s (Cache).
- Bei nicht verfügbarer Sammlung: klare Meldung, **kein** leerer Snapshot, **keine** alten Daten
  als neu ausgegeben.
- Tests: Anker-Gate, Band-Gate, Cache-Invalidierung, Warteschleife mit Fake-Adapter.
- Kein GPL-Code aus `frcaton/mtga-tracker-daemon` oder `mtgatool/mtga-reader` übernehmen —
  Feldpfad ist eigenständig nachgebaut.

## Referenzen (nur Recherche, kein Code-Import)

- `frcaton/mtga-tracker-daemon` (GPL): liest Prozessspeicher mit **festen Offsets je Unity-Version**,
  `GET /cards` → `[{grpId, owned}]`.
- `zetsumei8/mtga-collection-reader`: nutzt diesen Helper, verlangt Arena offen + **Home-Screen**;
  Issue #4 dokumentiert fehlende Karten, weil Unity 6000.3.14 auf 2020.3.13-Offsets zurückfällt.
  → Bestätigt: unser metadata-getriebener Weg ist der robustere.
