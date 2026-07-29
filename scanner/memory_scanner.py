"""Memory-Scanner: Findet die Kartensammlung im MTGA-Prozess-Speicher.

Basiert auf NthPhantom10/MTGA-collection-exporter (v2.0).
Portiert von Windows pymem → macOS pymem-osx.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Any

from pymem import Pymem

from .card_database import load_card_database
from .macos_paths import get_default_cache_dir, get_macos_mtga_process_name
from .pattern_scanner import scan_process_memory

ANCHOR_FILE = get_default_cache_dir() / "last_anchors.json"


def find_blocks(pm: Pymem, addr: int) -> list[dict[int, int]]:
    """Liest Speicher um eine Adresse und sucht nach (k,v)-Paaren.

    Die Collection-Daten liegen als Array von (grpId, quantity)-Paaren
    im Heap. Diese Funktion liest 4MB um die Fundstelle und parst
    alle gültigen Paare.

    Args:
        pm: Pymem-Instanz
        addr: Fundstelle einer Anker-Karte

    Returns:
        Liste von Karten-Dictionaries (grpId → quantity)
    """
    try:
        block_start = max(0, addr - 1024 * 1024)
        data = pm.read_bytes(block_start, 4 * 1024 * 1024)
        ints = struct.unpack(f"<{len(data) // 4}I", data)

        blocks: list[dict[int, int]] = []
        for offset in (0, 1):
            current: dict[int, int] = {}
            misses = 0
            for i in range(offset, len(ints) - 1, 2):
                k, v = ints[i], ints[i + 1]
                if 1000 <= k < 500000 and 1 <= v <= 400:
                    current[k] = v
                    misses = 0
                else:
                    misses += 1
                    if misses > 50:
                        if len(current) > 50:
                            blocks.append(current)
                        current = {}
                        misses = 0
            if len(current) > 50:
                blocks.append(current)

        return blocks
    except Exception:
        return []


def get_user_anchors(name_to_id: dict[str, int]) -> list[tuple[int, int, str]]:
    """Interaktive Anker-Eingabe mit Auto-Save.

    Der User gibt 5 Karten + Mengen ein, die er sicher besitzt.
    Diese dienen als Suchanker im Speicher.

    Args:
        name_to_id: Mapping von Kartenname (lower) → grpId

    Returns:
        Liste von (grpId, quantity, name)-Tupeln
    """
    import difflib
    import json

    # Gespeicherte Anker laden
    if ANCHOR_FILE.exists():
        try:
            with ANCHOR_FILE.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved and isinstance(saved, list):
                print("\n📌 [Gespeicherte Anker gefunden]")
                for i, (_, qty, name) in enumerate(saved, 1):
                    print(f"   {i}. {name} (x{qty})")
                choice = input("   Diese verwenden? [Y/n]: ").strip().lower()
                if choice not in ("n", "no"):
                    return saved
        except Exception:
            pass

    print("\n🔧 [Setup] Gib 5 Karten ein, die du sicher besitzt (Rares/Mythics am besten).")

    anchors: list[tuple[int, int, str]] = []
    while len(anchors) < 5:
        print(f"\nKarte #{len(anchors) + 1} (Enter = fertig):")
        name_input = input("  Name: ").strip()

        if not name_input:
            if anchors:
                break
            print("  ⚠ Bitte eine Karte eingeben.")
            continue

        search = name_input.lower()
        cid = name_to_id.get(search)

        if not cid:
            matches = difflib.get_close_matches(search, name_to_id.keys(), n=5, cutoff=0.5)
            if not matches:
                print("  ❌ Nicht gefunden. Prüfe die Schreibweise.")
                continue

            if len(matches) == 1:
                final_name = matches[0]
                print(f"  → {final_name.title()}")
            else:
                print("  Meintest du?")
                for i, m in enumerate(matches, 1):
                    print(f"    {i}. {m.title()}")
                sel = input("  Auswahl #: ")
                if not sel.isdigit() or not (1 <= int(sel) <= len(matches)):
                    continue
                final_name = matches[int(sel) - 1]

            cid = name_to_id[final_name]
            name_input = final_name.title()

        try:
            qty = int(input(f"  Menge von '{name_input}': "))
            if qty < 1:
                raise ValueError
            anchors.append((cid, qty, name_input))
        except ValueError:
            print("  ❌ Ungültige Menge.")
            continue

    # Anker speichern
    if anchors:
        try:
            import json

            with ANCHOR_FILE.open("w", encoding="utf-8") as f:
                json.dump(anchors, f, indent=2)
        except Exception:
            pass

    return anchors


def scan_collection() -> dict[int, int] | None:
    """Hauptfunktion: Scannt den MTGA-Speicher nach der Collection.

    Returns:
        Dictionary {grpId: quantity} oder None bei Fehler.
    """
    # 1. Karten-DB laden
    db = load_card_database()
    if not db:
        print("❌ Karten-DB konnte nicht geladen werden.")
        return None

    # 2. Name → ID Mapping
    name_to_id = {v["name"].lower(): k for k, v in db.items()}

    # 3. An MTGA-Prozess attach-en
    process_name = get_macos_mtga_process_name()
    print(f"🔗 Verbinde zu {process_name}...")
    try:
        pm = Pymem(process_name)
        print(f"✅ Verbunden (PID: {pm.pid})")
    except Exception as e:
        print(f"❌ MTGA läuft nicht. Starte das Spiel und öffne die 'Decks'-Ansicht.")
        print(f"   (Fehler: {e})")
        print(f"   Hinweis: pymem-osx benötigt sudo. Starte mit: sudo python3 ...")
        return None

    # 4. Anker-Karten eingeben
    anchors = get_user_anchors(name_to_id)
    if not anchors:
        print("❌ Keine Anker-Karten angegeben.")
        return None

    # 5. Memory-Scan
    print("\n🔍 Scanne Speicher nach Collection-Daten...")
    matches: list[int] = []
    total = len(anchors)

    for i, (aid, aqty, aname) in enumerate(anchors, 1):
        display = (aname[:15] + "..") if len(aname) > 15 else aname
        print(f"   [{i}/{total}] Suche {display}...")

        needle = struct.pack("<I", aid)
        found = scan_process_memory(pm, needle)
        print(f"     → {len(found)} Fundstellen")
        matches.extend(found)

    if not matches:
        print("❌ Keine Anker-Karten im Speicher gefunden.")
        print("   Stelle sicher, dass:")
        print("   - MTGA läuft und du in der 'Decks'-Ansicht bist")
        print("   - Die Karten existieren und die Mengen stimmen")
        print("   - Das Script mit sudo läuft")
        return None

    # 6. Blöcke um Fundstellen parsen
    print("\n📦 Parse Speicherblöcke...")
    candidates: list[dict[int, int]] = []
    for m in matches:
        candidates.extend(find_blocks(pm, m))

    if not candidates:
        print("❌ Keine validen Datenblöcke gefunden.")
        return None

    # 7. Besten Block auswählen (meiste Einträge)
    collection = max(candidates, key=len)
    print(f"\n✅ {len(collection)} unique Einträge gefunden!")

    return collection


def main() -> int:
    """CLI-Einstiegspunkt für den Scanner."""
    result = scan_collection()
    if result is None:
        return 1

    # Ausgabe
    print(f"\n📊 Collection: {sum(result.values())} Karten, {len(result)} unique IDs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
