#!/usr/bin/env python3
"""Generiert einen arena_deck.json mit echten englischen Kartennamen aus der lokalen MTGA-DB."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from scanner.card_database import load_card_database
from advisor.deck_import import import_arena_deck, write_deck


# Beispiel-Deck: Mono-Red Aggro (Standard)
DECK_TEXT = """
Deck Name: Mono-Red Aggro

Deck
4 Lightning Strike
4 Shock
4 Flame Channel
4 Goblin Chainwhirler
4 Goblin Dark-Drawler
2 Monarch of Wings
4 Cultivate
4 Search the Squares
4 Lightning Helix

Sideboard
4 Flame Channel
2 Abrade
2 Lightning Strike
"""

FORMAT = "standard"


def main() -> None:
    """Generiert arena_deck.json."""
    print("📦 Lade Karten-DB...")
    card_db = load_card_database(refresh_cache=False)
    print(f"   ✅ {len(card_db)} Karten geladen")

    if not card_db:
        print("❌ Keine Karten in DB. Scryfall-Fallback fehlgeschlagen.")
        sys.exit(1)

    print("\n🃏 Importiere Deck...")
    deck = import_arena_deck(
        DECK_TEXT,
        card_db=card_db,
        deck_format=FORMAT,
    )

    print(f"   ✅ Deck '{deck['name']}' importiert")
    print(f"      Hauptboard: {len(deck['mainboard'])} Karten")
    print(f"      Sideboard:  {len(deck['sideboard'])} Karten")

    if deck["diagnostics"]["unresolved"]:
        print(f"   ⚠️  {len(deck['diagnostics']['unresolved'])} Unresolved:")
        for u in deck["diagnostics"]["unresolved"]:
            print(f"      - {u}")

    if deck["diagnostics"]["ambiguous"]:
        print(f"   ⚠️  {len(deck['diagnostics']['ambiguous'])} Ambiguous:")
        for a in deck["diagnostics"]["ambiguous"]:
            print(f"      - {a['name']}: {[c['name'] for c in a['candidates'][:3]]}")

    print("\n💾 Schreibe arena_deck.json...")
    output_dir = Path(__file__).parent / "out"
    out_path = write_deck(deck, output_dir)
    print(f"   ✅ {out_path}")

    # Zeige Statistik
    print("\n📊 Statistik:")
    total_cards = sum(e["count"] for e in deck["mainboard"] + deck["sideboard"])
    print(f"   Gesamtanzahl: {total_cards} Karten")
    print(f"   Resolution-Rate: {100 * (1 - len(deck['diagnostics']['unresolved']) / 17):.1f}%")


if __name__ == "__main__":
    main()
