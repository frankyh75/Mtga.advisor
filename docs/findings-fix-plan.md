# Findings Fix Plan

**Ziel:** Die zuletzt identifizierten Probleme aus den neuen Commits systematisch beseitigen, ohne bestehende Exportpfade oder die lokale Dashboard-Arbeit zu beschädigen.

## Scope

Adressiert werden diese Findings:

1. Inline-JavaScript im Dashboard ist aktuell zu offen und muss gegen XSS gehärtet werden.
2. Der neue `decks container`-Pfad ist nicht konsistent, weil er `args.logs` erwartet, obwohl das Subcommand keinen solchen Parameter definiert.
3. Die Same-Name-Deduplizierung im Deck-Import ist zu still und kann falsche Karten wählen, wenn mehrere Sets denselben Namen haben.

## Leitprinzipien

- Canonical data first: JSON-Artefakte bleiben die Wahrheit, UI ist nur Darstellung.
- Conservative failure: Bei Mehrdeutigkeiten lieber mit Diagnostics abbrechen als still raten.
- Regression safety: Jede Änderung bekommt Tests oder Fixture-Abdeckung.
- Smallest safe fix: Erst die Brüche beheben, dann aufräumen oder erweitern.

## Reihenfolge

### 1. Dashboard absichern

**Problem:** Das Dashboard baut Inline-Daten direkt in ein Script ein. Das ist schnell, aber angreifbar und unnötig riskant.

**Fix:**
- JSON-Daten nicht mehr roh in Inline-Scripts interpolieren.
- Stattdessen Daten über:
  - initiale `data-*`-Attribute,
  - ein separates JSON-Endpoint-Load,
  - oder ein eingebettetes `<script type="application/json">`
  laden.
- Content-Security-Policy schärfen, soweit das aktuelle UI es zulässt.
- Tests ergänzen, die gefährliche Zeichenfolgen in Deck- oder Collection-Daten abdecken.

**Abnahme:**
- Dashboard rendert weiterhin korrekt.
- Eingaben mit `<`, `</script>`, `"` oder ähnlichen Sequenzen brechen das UI nicht.
- Keine direkte String-Interpolation von JSON in ausführbarem Inline-JS mehr.

### 2. `decks container` reparieren

**Problem:** Der neue CLI-Pfad ist formal vorhanden, aber die Argumente passen nicht zusammen. Das ist ein klassischer "dead branch"-Bug.

**Fix:**
- CLI-Definition und Implementierung synchronisieren.
- Entweder:
  - `--logs` beim Subcommand definieren,
  - oder den Container-Befehl so umbauen, dass er die vorhandene Standard-Log-Discovery nutzt.
- Fehlertext für fehlende Logs klar machen.
- CLI-Test hinzufügen, der den Container-Pfad ohne Sonderargumente ausführt.

**Abnahme:**
- `decks container` läuft mit dem dokumentierten Aufruf.
- Der Befehl scheitert nicht an fehlenden `args.logs`.
- Fehlende Logdateien liefern eine lesbare Diagnose.

### 3. Same-Name-Import entschärfen

**Problem:** Bei Karten mit gleichem Namen aus mehreren Sets wird aktuell still per interner Reihenfolge entschieden. Das ist für einen Advisor zu riskant.

**Fix:**
- Ambiguität wieder explizit machen, wenn keine eindeutige Lösung möglich ist.
- Falls eine Heuristik bestehen bleiben soll, dann nur mit:
  - klarer Dokumentation,
  - sichtbarer Diagnostic,
  - und konservativer Auswahlregel, die testbar ist.
- Die `_SET_ORDER`-Logik prüfen und entweder absichern oder durch eine nachvollziehbare Resolver-Regel ersetzen.
- Tests ergänzen für:
  - gleiche Namen, verschiedene Sets,
  - fehlende Lokalisierung,
  - unerwartete DB-Strukturen.

**Abnahme:**
- Kein stilles "best guess" ohne Kennzeichnung.
- Ambiguität landet in Diagnostics oder expliziter Auswahl.
- Import bleibt deterministisch.

### 4. End-to-end Regression absichern

**Problem:** Die drei Fixes berühren CLI, Parser und UI. Ohne Regressionstest ist das leicht wieder kaputt.

**Fix:**
- Einen kurzen End-to-end-Testpfad definieren:
  - Collection export,
  - Deck import oder Deck-Export,
  - Dashboard rendern.
- Fixtures für problematische Daten ergänzen.
- Mindestens einen Smoke-Test pro betroffener Ebene hinzufügen.

**Abnahme:**
- `pytest`-Suite schützt die drei betroffenen Pfade.
- Ein zukünftiger Commit kann die Findings nicht unbemerkt wieder einführen.

## Empfohlene Umsetzung in Tasks

1. Dashboard-Rendering auf sichere JSON-Übergabe umstellen.
2. CLI-Signatur und `decks container`-Implementation reparieren.
3. Deck-Resolver wieder konservativ machen.
4. Tests und Fixtures ergänzen.
5. Dokumentation in `README.md` oder Roadmap nur dann anpassen, wenn sich das Nutzerverhalten ändert.

## Done-Kriterien

- Kein XSS-anfälliges Inline-Datenmodell im Dashboard.
- `decks container` ist ausführbar und dokumentiert.
- Deck-Import rät nicht still bei Karten-Mutationen über Set-Grenzen hinweg.
- Alle betroffenen Pfade sind testabgesichert.

## Nicht-Ziele

- Kein großer UI-Neubau.
- Keine neue Sync-Architektur.
- Kein Wechsel des grundsätzlichen Exportmodells.
- Keine Ausweitung auf Meta- oder LLM-Funktionen.

