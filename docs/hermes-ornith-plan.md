# Hermes + Ornith Arbeitsplan

**Ziel:** Die bestehenden MTGA-Artefakte so erweitern, dass wir Decks, Decklisten und Advisor-Ausgaben sauber mit `Hermes` steuern und den lokalen LLM-Endpoint `ornith` über Tailscale nutzen können.

## Ausgangslage

- `collection.json` kommt bereits aus dem deterministischen Scanpfad.
- `decks.json` existiert bereits als Deck-Summary-Export aus `StartHook`.
- `arena_deck.json` existiert bereits als normalisierte Einzel-Deckliste.
- `advisor llm` ist implementiert und kann gegen einen OpenAI-kompatiblen Endpoint laufen.
- Der Tailscale-Endpunkt `http://100.95.116.78:8081/v1/chat/completions` war von hier aus nicht erreichbar; die Integration ist also technisch vorbereitet, aber netzwerkseitig noch nicht bestätigt.

## Arbeitsmodell

- `Hermes` ist die Steuer- und Arbeitsoberfläche für diesen Flow.
- `ornith` ist das lokale LLM für Analyse, Zusammenfassung und Vorschläge.
- Die JSON-Artefakte bleiben die Wahrheit.
- Weder `Hermes` noch `ornith` dürfen die Exportdaten stillschweigend überschreiben.

## Konkrete Ziele

1. Deck-Summaries aus Logs sauber exportieren.
2. Echte Decklisten mit englischen Kartennamen reproduzierbar erzeugen.
3. `ornith` über Tailscale zuverlässig ansprechen.
4. `Hermes` auf die lokalen Artefakte und den LLM-Output aufsetzen.
5. Die UI so halten, dass sie nur anzeigt, nicht berechnet.

## Phasen

### Phase 1: Deck-Summaries stabilisieren

**Ziel:** `decks.json` bleibt verlässlich und vollständig genug für Navigation, Auswahl und UI.

Tasks:
- `StartHook`-Parsing weiter absichern.
- Exportdiagnostik für fehlende Decks ergänzen.
- GUI und CLI auf `decks.json` als Summary-Quelle vereinheitlichen.

Ergebnis:
- Nutzer sehen in `Hermes` alle bekannten Decks des Accounts.
- Fehlende oder unvollständige Logdaten sind explizit markiert.

### Phase 2: Englische Kartennamen standardisieren

**Ziel:** Jede exportierte Deckliste nutzt englische Kartennamen als kanonische Namen.

Tasks:
- Lokale MTGA-DB als Primärquelle für Namen verwenden.
- `Localizations_enUS` und Legacy-Fallbacks validieren.
- Bei Mehrdeutigkeiten konservativ abbrechen statt still zu raten.
- Testfälle mit aktuellen MTGA-Schema-Varianten ergänzen.

Ergebnis:
- `arena_deck.json` enthält stabile, englische Namen.
- Unbekannte oder doppelte Zuordnungen landen in Diagnostics.

### Phase 3: Vollständigen Deck-Export definieren

**Ziel:** Nicht nur Summaries, sondern echte Decklisten exportieren.

Tasks:
- Quelle für vollständige Decklisten festlegen.
- Exportformat dokumentieren.
- Ein sauberes Zielartefakt definieren:
  - entweder pro Deck eine Datei
  - oder ein Containerformat mit mehreren Decks
- Import/Export-Pfade nicht vermischen.

Ergebnis:
- `Hermes` kann Decks anzeigen und gezielt einzelne Decklisten laden.
- Der Export bleibt reproduzierbar und diff-freundlich.

### Phase 4: Ornith über Tailscale anschließen

**Ziel:** Der LLM-Advisor läuft gegen den entfernten `ornith`-Endpoint.

Tasks:
- Endpoint-Konfiguration über `mtga-advisor.json`, Env und CLI festziehen.
- Erreichbarkeit per `curl` und CLI validieren.
- Host-seitige Voraussetzungen auf dem `ornith`-Rechner dokumentieren:
  - Dienst auf `0.0.0.0` oder Tailscale-IP binden
  - Firewall/Tailscale-ACL prüfen
  - OpenAI-kompatiblen `/v1/chat/completions`-Pfad bestätigen

Ergebnis:
- `advisor llm` kann gegen `ornith` laufen, ohne dass lokale Defaults geändert werden müssen.

### Phase 5: Hermes-Workflow bauen

**Ziel:** Ein klarer Ablauf für lokale Arbeit mit Scan, Decks und LLM.

Tasks:
- Reihenfolge für den Alltag festlegen:
  1. Scan / Export
  2. Deck-Export
  3. `advisor complete`
  4. optional `advisor llm`
  5. Anzeige in `Hermes`
- Statusanzeige für:
  - Collection vorhanden?
  - Decks vorhanden?
  - Advisor Ergebnis vorhanden?
  - LLM erreichbar?
- Nur lesende Anzeige in der GUI, keine Doppelberechnung.

Ergebnis:
- Ein verständlicher Arbeitsfluss statt verstreuter Einzelschritte.

## Offene Punkte

- Was genau ist `Hermes` bei euch technisch?
  - CLI-Wrapper
  - Desktop-App
  - Dashboard
  - Agent/Orchestrator
- Woher sollen vollständige Decklisten kommen?
  - Logs
  - Export aus MTGA
  - manueller Import
  - kombinierter Ansatz
- Soll `ornith` nur Advisor-Text liefern oder auch Decklisten zusammenfassen?

## Erfolgskriterien

- `decks.json` ist verfügbar und in der UI sichtbar.
- Mindestens ein vollständiges Deck mit englischen Namen ist sauber exportiert.
- `advisor llm` erreicht `ornith` über Tailscale.
- `Hermes` zeigt Collection, Decks und Advisor-Status ohne eigene Business-Logik.
- Fehler sind als Diagnostics sichtbar und nicht still versteckt.

## Nächste Implementierungsreihenfolge

1. Deck-Export und Namensauflösung festigen.
2. Tailscale-Erreichbarkeit von `ornith` verifizieren.
3. `Hermes` auf die existierenden Artefakte aufsetzen.
4. Tests für Export, Resolver und LLM-Config ergänzen.
5. Doku in README und Roadmap referenzieren.
