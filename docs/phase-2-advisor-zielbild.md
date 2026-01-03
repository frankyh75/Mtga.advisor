# Advisor-Zielbild Phase 2 (verbindlich)

## 0) Leitplanken (nicht verhandelbar)
- Kein Pflicht-Abo, keine Paywall für Grundfunktionen.
- Premium ist ein einmaliger Unlock (kein Abo), offline nutzbar; Lizenzprüfung nur bei Updates/Validierung.
- Kein Live-Tracking, kein Overlay, kein Cloud-Zwang im Kernpfad.
- Offline-first, deterministisch, erklärbar.
- Zielgruppe: Anfänger, Familien, iPad-/Mac-Nutzer.
- Fokus: Entscheidungshilfe, nicht Meta-Dominanz.
- Konservativitätsprinzip: Ohne vollständige Evidenz keine harten Aussagen.
- Format-agnostisch: Alle Modelle müssen format-parameterisiert sein.

## 1) Gemeinsame Datenbasis (für Constructed & Jump In)

### 1.1 Eingabedaten (Minimal)
| Datenquelle | Zweck | Pflicht? | Hinweise |
|---|---|---|---|
| `collection.json` (Phase-1) | Besitzstände (Karten + Wildcards) | Pflicht | Vollständigkeitsstatus (`complete|partial|unknown`) beachten. |
| `arena_deck.json` | Ziel-Decklisten | Optional | Nutzer kann eigene Decks importieren. |
| Lokale Karten-Metadaten (Arena IDs → Name, Farbe, Typ, Mana Value, Rarity, Set, Legalitäten) | Rollen-/Kurvenheuristiken | Pflicht für Phase 2 | Offline-DB; keine Cloud-Abhängigkeit. [ANNAHME] |
| Format-Konfiguration (Legalitäten, Rotation, BO1/BO3) | Format-Parametrisierung | Pflicht | Parametrisiert pro Format, keine feste Reihenfolge. |
| Jump-In Pool-Listen (Half-Decks inkl. Rarity) | Jump-In Beratung | Pflicht | Vollständige Listen verfügbar. [BELEGT] |
| Gold/Gems/Vault (Inventar) | Gold-Ausgaben-Advisor | Optional/unsicher | Verfügbarkeit unklar; Fallbacks nötig. [BELEGT]/[ANNAHME] |
| Nutzerpräferenz (Constructed vs Limited, Budget-Tempo, Lieblingsfarben) | Empfehlungen | Optional | Lokale Settings. |

### 1.2 Vollständigkeit & Konservativität (verbindlich)
- **Keine „craftbar/complete“-Aussagen** bei `completeness != complete`.
- Bei `partial/unknown`: nur **What-if**-Aussagen, mit sichtbarem Hinweis.
- Jede Empfehlung muss sichtbare Gründe (Regeln + genutzte Daten) liefern.

---

## 2) TEIL A – Constructed Advisor

### A1) Wildcard-Nutzung (C/U/R/M)

**Eingabedaten**
- Collection-Status + Wildcards (`collection.json`, Vollständigkeit)
- Ziel-Deckliste (`arena_deck.json`) inkl. Format-Parameter
- Karten-Metadaten (Rarity, Farben, Manakurve, Legalität)

**Bewertungslogik (deterministisch)**
- **Regel 1: Vollständigkeits-Gate**
  - Wenn `wildcards.completeness != complete`: keine harten Craft-Empfehlungen, nur What-if.
- **Regel 2: Bedarf pro Karte (Need Score)**
  - `need = required_copies - owned_copies` (clamp 0–4)
- **Regel 3: Format-Legalität**
  - `illegal` ⇒ Score = 0, Hinweis „nicht legal im Format“.
- **Regel 4: Deck-Relevanz**
  - Karten aus Mainboard priorisieren vor Sideboard.
- **Regel 5: Rarity-Bucket**
  - Je Rarity eigene Rangliste (C/U/R/M getrennt), damit Wildcards nicht „mischen“.

**Ergebnis**
- Für jede Rarity: Liste „Top Crafts“ (mit Bedarf + Gründe).
- Konservatives Fazit: „Craft nur, wenn du genau dieses Deck spielen willst.“

**Kostenlos vs Premium**
- **Kostenlos:**
  - Rarity-getrennte Craft-Liste pro Deck.
  - Sichtbare Gründe (Bedarf, Legalität, Main-/Sideboard).
- **Premium:**
  - Mehr-Deck-Vergleich: „Welche Crafts helfen mehreren Decks?“ (Cross-Deck-Value)
  - Craft-Simulationsmodus: „Was ändert sich, wenn ich X Wildcards investiere?“
- **Fairness:**
  - Grundfunktion (ein Deck craften) bleibt kostenlos; Premium erweitert Komfort & Vergleich.

---

### A2) Deck-Spielbarkeit / Completion

**Eingabedaten**
- Collection-Status (Ownership pro Arena ID)
- Ziel-Deckliste + Format
- Karten-Metadaten (Manakurve, Farbidentität, Land-Typ)

**Bewertungslogik (deterministisch)**
- **Completion-Score (0–100)**
  - `owned_ratio = owned_copies / required_copies` pro Karte
  - Gewichtung: Mainboard > Sideboard
  - `score = weighted_avg(owned_ratio)`
- **Mana-Balance-Checks (konservativ)**
  - Landanzahl vs. Deckgröße (z. B. 24/60 als Richtwert) [BEST PRACTICE]
  - Farb-Support (Anteil farbiger Quellen vs. farbige Pips)
- **Confidence**
  - `complete` ⇒ high, `partial` ⇒ medium, `unknown` ⇒ low

**Kostenlos vs Premium**
- **Kostenlos:**
  - Completion-Score, fehlende Kartenliste, Confidence-Anzeige.
- **Premium:**
  - Deck-Vergleich (Welches meiner Decks ist „am spielbarsten“?).
  - Progress-Tracking (Verlauf der Completion über Zeit, lokal gespeichert).
- **Fairness:**
  - Einsteiger können sofort sehen, ob ein Deck spielbar ist; Premium ist Komfort/Übersicht.

---

### A3) Upgrade-Pfade (Reihenfolge von Crafts)

**Eingabedaten**
- Collection, Wildcards, Ziel-Deckliste
- Karten-Metadaten (Mana Value, Karten-Typ, Farben)
- Optional: Nutzerpräferenzen (aggressiv/kontroll, Budgettempo)

**Bewertungslogik (deterministisch)**
- **Phase 1: Mana-Basis stabilisieren**
  - Priorität auf Länder/Manabase, falls Farb-Support zu schwach ist.
- **Phase 2: Kurve glätten**
  - Fehlende 1–3 Drops auffüllen, wenn Kurve Lücken zeigt.
- **Phase 3: Schlüsselkarten**
  - Karten mit 3–4 Kopien im Deck zuerst craften.
- **Regel: Rarity-Schonung**
  - Wenn Rare/Mythic knapp: günstigere, funktional ähnliche Karten priorisieren (falls im Decklist-Template als Ersatz markiert). [ANNAHME]

**Kostenlos vs Premium**
- **Kostenlos:**
  - Reihenfolge-Empfehlung mit 3–5 nächsten Crafts.
- **Premium:**
  - Mehrstufige Upgrade-Pfade (kurz/mittel/lang) + alternative Pfade.
- **Fairness:**
  - Basis-Upgrade bleibt kostenlos; Premium bietet mehr Planungstiefe.

---

### A4) Gold-Ausgaben (Packs vs Sparen vs Draft)

**Eingabedaten**
- Gold/Gems/Vault (optional/unsicher)
- Nutzerpräferenz: „Packs vs Limited“ (optional)
- Format-Präferenz (optional)

**Bewertungslogik (deterministisch, ohne Meta-Zwang)**
- **Wenn Gold-Daten fehlen:**
  - Empfehlung als „Szenario-Modus“ (z. B. „Wenn du 5k Gold hast…“).
- **Wenn Gold-Daten vorhanden:**
  - **Regel 1:** Budget-Puffer (z. B. 5k) für Event-Optionen.
  - **Regel 2:** Spielerpräferenz priorisieren (Constructed vs Limited).
  - **Regel 3:** Keine Winrate-Argumente, nur Sammlungsfortschritt/Erlebniswert.

**Kostenlos vs Premium**
- **Kostenlos:**
  - Einfache Empfehlung „Jetzt ausgeben oder sparen“ + Begründung.
- **Premium:**
  - Szenario-Vergleich (Packs vs Draft) mit simulierten Sammelzuwachs-Schätzungen. [ANNAHME]
- **Fairness:**
  - Grundentscheidungshilfe bleibt kostenlos; Premium liefert Planungskomfort.

---

## 3) TEIL B – Jump In („Leg los!“) Advisor

### B1) Eingabedaten
- Jump-In Pool-Listen (Half-Decks mit vollständigen Kartenlisten + Rarity). [BELEGT]
- Collection-Status (Owned/Unowned pro Arena ID)
- Karten-Metadaten (Farbe, Typ, Mana Value, Set, Legalität)
- Format-Parameter (Rotation, Legalitäten)

### B2) Bewertungsmodell (deterministisch, ohne Winrates)

**Score-Komponenten (0–100)**
1) **Collection-Value**
   - + für neue Karten (`owned_copies == 0`), v. a. Rare/Mythic.
   - Duplikate nur bis 4 zählen (Beyond-4 = 0).
2) **Synergie der Half-Decks**
   - Farb-Kompatibilität (gemeinsame Farben)
   - Kurven-Kompatibilität (keine doppelte „Lücke“ in 1–3 Mana)
   - Archetyp-Tag-Overlap (falls Pools thematische Tags haben). [ANNAHME]
3) **Langzeit-Wert**
   - Legalität im gewählten Format
   - Rotation: Karten mit baldiger Rotation werden abgewertet.
4) **Lernwert**
   - Komplexitäts-Score (z. B. Anzahl komplexer Keywords/Mechaniken)
   - Ziel: Einsteigerfreundliche Kombinationen höher werten.

**Ergebnis**
- Ampel pro Kombination (Grün/Gelb/Rot)
- Sichtbare Gründe (z. B. „10 neue Karten, 2 neue Rares, gleiche Farben“)
- Konservative Hinweise: „Keine Winrate-Aussage, nur Sammlungs-/Lernwert“

### B3) Kostenlos vs Premium
- **Kostenlos:**
  - Top-3 Empfehlungen basierend auf Collection-Value + einfache Synergie (Farben).
  - Sichtbare Gründe in einfacher Sprache.
- **Premium:**
  - Vollanalyse aller Kombinationen (inkl. Lernwert & Rotation-Detail).
  - Was-wäre-wenn: „Wenn du X Karten craften willst, welche Jump-In-Kombi hilft?“
- **Fairness:**
  - Kostenlos gibt echte, sofort nutzbare Entscheidungshilfe; Premium liefert Tiefe.

---

## 4) TEIL C – GUI & UX (Web / iPad)

### C1) Gemeinsames GUI-Konzept
- **Home:** „Was soll ich jetzt tun?“ → 1 primärer CTA.
- **Zwei Hauptbereiche:**
  - **Constructed Advisor** (Decks/Wildcards/Gold)
  - **Jump-In Advisor**
- **Kernmuster:**
  - Ampel-Badges (grün/gelb/rot)
  - Icons für Rarity, Gold, Format
  - Erklärboxen als kurze Bullet-Listen (keine Essays)

### C2) Screens (kostenlos vs Premium sichtbar, nicht aggressiv)
| Screen | Kostenlos | Premium | Sichtbarkeit |
|---|---|---|---|
| Collection-Import & Status | ✅ | ✅ | Basis-Funktion, kein Upsell |
| Deck-Completion & Missing Cards | ✅ | ✅ | Basis-Funktion |
| Wildcard-Craft pro Deck | ✅ | ✅ | Basis-Funktion |
| Multi-Deck-Vergleich | ❌ | ✅ | „Premium Badge“ klein + Tooltip |
| Jump-In Top-3 Empfehlungen | ✅ | ✅ | Basis-Funktion |
| Jump-In Vollanalyse/Filter | ❌ | ✅ | Hinweis „Mehr Optionen in Premium“ |
| Verlauf/History (local) | ❌ | ✅ | Premium Badge |

### C3) Sofortige Handlung
- Jede Ansicht zeigt **1 klare Empfehlung** + „Warum?“ als Bullet-Liste.
- „Jetzt tun“-Button (z. B. „Craft 2 Karten“ / „Jump In: Paket A+B“).

---

## 5) Free / Trial / Premium – Feature-Grenzen (verbindlich)

### 5.1 Global
| Ebene | Zeit / Lizenz | Kernprinzip |
|---|---|---|
| **Free** | dauerhaft | Grundfunktionalität ohne Paywall |
| **Trial (7 Tage)** | volle Features | Voller Umfang zum Ausprobieren |
| **Premium Unlock** | einmalig | Offline nutzbar, Lizenzprüfung nur bei Updates |

### 5.2 Feature-Tabelle (Constructed vs Jump In)
| Bereich | Free | 7-Tage Trial | Premium Unlock |
|---|---|---|---|
| Constructed – Completion | Basis-Score + Missing Cards | Voll | Voll |
| Constructed – Wildcard-Crafts | Pro-Deck-Listung | Voll | Voll |
| Constructed – Upgrade-Pfade | 3–5 nächste Crafts | Voll | Voll |
| Constructed – Multi-Deck-Vergleich | ❌ | ✅ | ✅ |
| Constructed – Progress-History (lokal) | ❌ | ✅ | ✅ |
| Jump-In – Top-3 Empfehlungen | ✅ | ✅ | ✅ |
| Jump-In – Vollanalyse + Filter | ❌ | ✅ | ✅ |
| Jump-In – Lernwert/Rotation-Details | ❌ | ✅ | ✅ |
| Meta-Signals (optional, import/connector) | ❌ | ✅ | ✅ |

---

## 6) Forschungsnotizen & Unsicherheiten

### 6.1 Gold/Gems – typische Open-Source-Ansätze
- **[BELEGT]** MTGA Tracker verarbeitet `Inventory`-Events und erkennt Inventory-Changes inkl. Gold/Gems (aus Logs), mit optionalem Upload. Quelle: [mtgatracker/mtgatracker](https://github.com/mtgatracker/mtgatracker) (`legal/privacy.md`, `app/dispatchers.py`).
- **[BEST PRACTICE]** Bei unsicheren Daten: Gold/Gems als optional behandeln + Szenario-Modus anbieten.
- **[ANNAHME]** Vollständige, zuverlässige Gold/Gems-Daten sind nicht garantiert; UI muss „unbekannt“ tolerieren.

### 6.2 Jump-In Pools – typische Datenhaltung
- **[BELEGT]** Open-Source JSON-Datasets für Jump-In-Pakete existieren (z. B. `mtg-jump-in` veröffentlicht strukturierte Packet-Listen). Quelle: [bluelovers/mtg-jump-in](https://github.com/bluelovers/mtg-jump-in).

### 6.3 Meta-Daten – Import vs Connector
- **[BELEGT]** Meta-Signale sind optional und sollen als Offline-Import + optionaler Connector modelliert werden (Phase-2 Leitplanke). Quelle: `docs/ai-context.md`.
- **[BEST PRACTICE]** Format-gebundene Meta-Snapshots in versionierten JSON-Dateien (`meta_signals.json`) mit klarer Quelle/Datum.
- **[ANNAHME]** Falls Meta fehlt: Advisor nutzt rein deterministische Regeln (Degradationsmodus).

---

## 7) Implikationen für Web-App vs iOS-App

### Identisch (Kern)
- Datenschemata, Heuristiken, Scores, Erklärlogik.
- Free/Trial/Premium-Grenzen und Lizenzregeln.
- Offline-first: lokale Datenhaltung, kein Cloud-Zwang.

### Unterschiede (Hülle)
- **Web-App:**
  - Datei-Import via Upload (collection.json, decklists, meta_signals).
  - Lizenzbindung pro Gerät (Browser-Storage) [ANNAHME].
- **iOS/iPadOS:**
  - Dateipicker + iCloud-Import/Export.
  - Lizenzbindung wahlweise pro Gerät oder iCloud-Account (gegeben).
  - Lokaler Share-Sheet Export für Eltern/Kinder.

---

## 8) Offene Risiken & Validierungsbedarf
- **Gold/Gems-Datenqualität**: Verfügbarkeit in Logs ist unklar → Fallbacks & Szenario-Modus verpflichtend. [BELEGT]/[ANNAHME]
- **Karten-Metadaten lokal**: Verfügbarkeit einer vollständigen Offline-DB nötig. [ANNAHME]
- **Jump-In Pool-Aktualität**: Wizards ändern Pools; Update-Prozess erforderlich. [BEST PRACTICE]
- **Meta-Signals**: Optionalität darf nicht Kernlogik beeinflussen; Degradationsmodus muss testbar sein. [BELEGT]
