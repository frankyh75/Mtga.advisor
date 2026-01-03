# ADR 0001: Tech-Stack

## Status
Accepted

## Kontext
Phase 0/1 benötigt einen lokalen, deterministischen CLI-Export ohne externe Services. Der Code soll klein bleiben, gut testbar sein und plattformübergreifend arbeiten (Windows/macOS, perspektivisch Linux/Wine). LLM-Nutzung ist optional und ausdrücklich nicht Teil der Kernfunktion.

## Entscheidung
- **Sprache/Runtime:** Python 3.12 als primäre Runtime für Phase 0/1.
- **Packaging:** `pyproject.toml` mit `pipx`/`pip`-Installation für CLI-Nutzung.
- **CLI:** `typer` für ergonomische CLI-UX und klare Help-Ausgaben.
- **Datenformate:** JSON für alle Output-Artefakte (stabil, diff-freundlich).
- **Tests:** `pytest` als Standard-Testframework.

## Konsequenzen
- Schneller Prototyping-Flow ohne Build-Tooling.
- Python erleichtert Datei-/Log-Handling und Cross-Platform-Pfade.
- Abhängigkeiten bleiben schlank; keine GUI/Service-Komponenten in Phase 0/1.
