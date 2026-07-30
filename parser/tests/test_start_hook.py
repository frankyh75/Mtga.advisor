"""Tests für den StartHook-Parser und Deck-Export."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from parser.start_hook import parse_start_hook, DeckSummary, StartHookData
from parser.decks import export_decks


class TestParseStartHook:
    """Tests für parse_start_hook()."""

    def test_empty_data(self):
        """Leeres oder fehlendes DeckSummaries-Array."""
        assert parse_start_hook({}) is None
        assert parse_start_hook({"DeckSummaries": []}) is None
        assert parse_start_hook({"DeckSummaries": None}) is None

    def test_single_deck(self):
        """Ein einzelnes Deck mit allen Feldern."""
        data = {
            "DeckSummaries": [
                {
                    "Name": "My Deck",
                    "DeckId": "550e8400-e29b-41d4-a716-446655440000",
                    "DeckTileId": 12345,
                    "Description": "A test deck",
                    "Mana": "UR",
                    "IsCompanionValid": True,
                    "Attributes": {"Format": "Standard", "LastPlayed": "2026-07-30"},
                    "FormatLegalities": {"Standard": True, "Historic": False},
                }
            ],
            "InventoryInfo": {"Gems": 100, "Gold": 5000},
            "DeckLimit": 75,
        }
        result = parse_start_hook(data)
        assert result is not None
        assert len(result.deck_summaries) == 1

        deck = result.deck_summaries[0]
        assert deck.name == "My Deck"
        assert deck.deck_id == "550e8400-e29b-41d4-a716-446655440000"
        assert deck.deck_tile_id == 12345
        assert deck.description == "A test deck"
        assert deck.mana == "UR"
        assert deck.is_companion_valid is True
        assert deck.attributes["Format"] == "Standard"
        assert deck.format_legalities["Standard"] is True
        assert deck.format_legalities["Historic"] is False

        assert result.inventory is not None
        assert result.inventory["Gems"] == 100
        assert result.deck_limit == 75

    def test_multiple_decks(self):
        """Mehrere Decks im selben Event."""
        data = {
            "DeckSummaries": [
                {"Name": "Deck A", "DeckId": "id-a"},
                {"Name": "Deck B", "DeckId": "id-b"},
                {"Name": "Deck C", "DeckId": "id-c"},
            ]
        }
        result = parse_start_hook(data)
        assert result is not None
        assert len(result.deck_summaries) == 3
        assert [d.name for d in result.deck_summaries] == ["Deck A", "Deck B", "Deck C"]

    def test_deck_without_id(self):
        """Deck ohne DeckId (sollte trotzdem funktionieren)."""
        data = {"DeckSummaries": [{"Name": "No ID Deck"}]}
        result = parse_start_hook(data)
        assert result is not None
        assert len(result.deck_summaries) == 1
        assert result.deck_summaries[0].name == "No ID Deck"
        assert result.deck_summaries[0].deck_id is None

    def test_attributes_as_list(self):
        """Attributes können als Liste von {key, value} kommen (wie im Log)."""
        data = {
            "DeckSummaries": [
                {
                    "Name": "List Attrs",
                    "Attributes": [
                        {"key": "Format", "value": "Historic"},
                        {"key": "Version", "value": "1"},
                    ],
                }
            ]
        }
        result = parse_start_hook(data)
        assert result is not None
        deck = result.deck_summaries[0]
        assert deck.attributes["Format"] == "Historic"
        assert deck.attributes["Version"] == "1"

    def test_inventory_cleaned(self):
        """SeqId, Changes, CustomTokens, Vouchers, Cosmetics werden entfernt."""
        data = {
            "DeckSummaries": [{"Name": "Test"}],
            "InventoryInfo": {
                "SeqId": 42,
                "Changes": [],
                "CustomTokens": {},
                "Vouchers": {},
                "Cosmetics": {},
                "Gems": 100,
                "Gold": 5000,
            },
        }
        result = parse_start_hook(data)
        assert result is not None
        assert "SeqId" not in result.inventory
        assert "Changes" not in result.inventory
        assert "Gems" in result.inventory
        assert result.inventory["Gems"] == 100

    def test_card_metadata(self):
        """CardMetadataInfo wird extrahiert."""
        data = {
            "DeckSummaries": [{"Name": "Test"}],
            "CardMetadataInfo": {
                "NonCraftableCardList": [123, 456],
                "NonCollectibleCardList": [789],
                "UnreleasedSets": ["EDG"],
            },
        }
        result = parse_start_hook(data)
        assert result is not None
        assert result.card_metadata is not None
        assert result.card_metadata["NonCraftableCardList"] == [123, 456]


class TestExportDecks:
    """Tests für export_decks()."""

    def _write_log(self, path: Path, events: list[dict]) -> None:
        """Schreibe eine simulierte Player.log mit JSON-Events."""
        lines = []
        for event in events:
            lines.append(f"[2026-07-30 12:00:00] [SomeClass] {event['event']}")
            lines.append(json.dumps(event["data"]))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_no_start_hook(self):
        """Log ohne StartHook → leeres decks.json."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "Player.log"
            self._write_log(log_path, [
                {"event": "PlayerInventory.GetPlayerCardsV3", "data": {"cards": []}},
                {"event": "Inventory.Updated", "data": {"delta": {}}},
            ])
            output_dir = Path(tmp) / "out"
            export_paths = export_decks([log_path], output_dir)

            assert export_paths.decks.exists()
            payload = json.loads(export_paths.decks.read_text(encoding="utf-8"))
            assert payload["diagnostics"]["deckCount"] == 0
            assert payload["decks"] == []

    def test_single_start_hook(self):
        """Ein StartHook mit zwei Decks."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "Player.log"
            self._write_log(log_path, [
                {
                    "event": "StartHook",
                    "data": {
                        "DeckSummaries": [
                            {"Name": "Deck 1", "DeckId": "id-1"},
                            {"Name": "Deck 2", "DeckId": "id-2"},
                        ]
                    },
                },
            ])
            output_dir = Path(tmp) / "out"
            export_paths = export_decks([log_path], output_dir)

            payload = json.loads(export_paths.decks.read_text(encoding="utf-8"))
            assert payload["diagnostics"]["deckCount"] == 2
            assert payload["decks"][0]["name"] == "Deck 1"
            assert payload["decks"][1]["name"] == "Deck 2"

    def test_multiple_start_hooks(self):
        """Mehrere StartHooks → letzter gewinnt bei gleicher ID."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "Player.log"
            self._write_log(log_path, [
                {
                    "event": "StartHook",
                    "data": {
                        "DeckSummaries": [
                            {"Name": "Old Name", "DeckId": "id-1"},
                        ]
                    },
                },
                {
                    "event": "StartHook",
                    "data": {
                        "DeckSummaries": [
                            {"Name": "New Name", "DeckId": "id-1"},
                            {"Name": "Deck 2", "DeckId": "id-2"},
                        ]
                    },
                },
            ])
            output_dir = Path(tmp) / "out"
            export_paths = export_decks([log_path], output_dir)

            payload = json.loads(export_paths.decks.read_text(encoding="utf-8"))
            assert payload["diagnostics"]["deckCount"] == 2
            # Letzter StartHook gewinnt → "New Name"
            names = [d["name"] for d in payload["decks"]]
            assert "New Name" in names
            assert "Old Name" not in names

    def test_run_report(self):
        """Run-Report wird korrekt geschrieben."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "Player.log"
            self._write_log(log_path, [
                {
                    "event": "StartHook",
                    "data": {
                        "DeckSummaries": [{"Name": "Test Deck", "DeckId": "id-1"}],
                    },
                },
            ])
            output_dir = Path(tmp) / "out"
            export_paths = export_decks([log_path], output_dir)

            assert export_paths.run_report.exists()
            report = json.loads(export_paths.run_report.read_text(encoding="utf-8"))
            assert report["schema"] == "run-report-decks.v1"
            assert report["summary"]["deckCount"] == 1
            assert "start-hook-parser" in report["diagnostics"]["evidence"]
