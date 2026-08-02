"""Tests für den MTGGoldfish Meta-Daten Scraper (advisor/meta.py).

Testet:
- HTML-Parsing mit realen MTGGoldfish-HTML-Fragmenten
- Caching (speichern, laden, TTL, stale cache)
- Datenstruktur (MetaData, MetaDeck)
- format_meta_for_prompt (LLM-Prompt-Formatierung)
- format_meta_table (CLI-Tabellen-Formatierung)
- Fehlertoleranz (leeres HTML, fehlende Felder)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import unittest

from advisor.meta import (
    MetaData,
    MetaDeck,
    fetch_meta,
    format_meta_for_prompt,
    format_meta_table,
    load_meta_from_file,
    save_meta_to_file,
    _parse_metagame_html,
    _parse_archetype_tile,
    _extract_archetype_tiles,
    _dict_to_meta,
    _iso_now,
    SUPPORTED_FORMATS,
)


# ---------------------------------------------------------------------------
# Realistic HTML fixture (based on actual MTGGoldfish structure)
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html><body>
<div class="container-fluid layout-container-fluid">
  <div class="archetype-tile" id="29068">
    <div class="archetype-tile-image">
      <div class="card-tile" role="img" aria-label="Image of Badgermole Cub">
        <div class="card-image-tile" style="background-image: url('https://cards.mtggoldfish.com/images/xxx.webp');"></div>
        <a class="card-image-tile-link-overlay" href="/archetype/standard-selesnya-ouroboroid-woe">
          <span class="sr-only">Badgermole Cub</span>
        </a>
      </div>
    </div>
    <div class="archetype-tile-description-wrapper">
      <div class="archetype-tile-description">
        <div class="archetype-tile-title">
          <span class="deck-price-online" style="display: none;">
            <a href="/archetype/standard-selesnya-ouroboroid-woe#online">Selesnya Ouroboroid</a>
          </span>
          <span class="deck-price-paper" style="display: inline;">
            <a href="/archetype/standard-selesnya-ouroboroid-woe#paper">Selesnya Ouroboroid</a>
          </span>
        </div>
        <div class="manacost-container">
          <span class="manacost" aria-label="colors: white green">
            <i class="ms ms-w ms-cost ms-shadow"></i><i class="ms ms-g ms-cost ms-shadow"></i>
          </span>
        </div>
        <ul>
          <li>Badgermole Cub</li>
          <li>Leatherhead, Swamp Stalker</li>
          <li>Spider Manifestation</li>
        </ul>
      </div>
      <div class="archetype-tile-statistics">
        <div class="archetype-tile-statistics-left">
          <div class="archetype-tile-statistic metagame-percentage">
            <div class="archetype-tile-statistic-name">META%</div>
            <div class="archetype-tile-statistic-value">
              16.7%
              <span class="archetype-tile-statistic-value-extra-data">(260)</span>
            </div>
          </div>
        </div>
        <div class="archetype-tile-statistics-right">
          <div class="archetype-tile-statistic deck-price-paper" style="display: block;">
            <div class="archetype-tile-statistic-name">Tabletop</div>
            <div class="archetype-tile-statistic-value">$&nbsp;574</div>
          </div>
          <div class="archetype-tile-statistic deck-price-online" style="display: none;">
            <div class="archetype-tile-statistic-name">MTGO</div>
            <div class="archetype-tile-statistic-value">150&nbsp;tix</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="archetype-tile" id="29069">
    <div class="archetype-tile-image">
      <div class="card-tile" role="img" aria-label="Image of Jeskai Revelation">
        <a class="card-image-tile-link-overlay" href="/archetype/standard-jeskai-lessons-woe">
          <span class="sr-only">Jeskai Revelation</span>
        </a>
      </div>
    </div>
    <div class="archetype-tile-description-wrapper">
      <div class="archetype-tile-description">
        <div class="archetype-tile-title">
          <span class="deck-price-paper" style="display: inline;">
            <a href="/archetype/standard-jeskai-lessons-woe#paper">Jeskai Lessons</a>
          </span>
        </div>
        <div class="manacost-container">
          <span class="manacost" aria-label="colors: white blue red">
            <i class="ms ms-w ms-cost ms-shadow"></i><i class="ms ms-u ms-cost ms-shadow"></i><i class="ms ms-r ms-cost ms-shadow"></i>
          </span>
        </div>
        <ul>
          <li>Jeskai Revelation</li>
          <li>Tablet of Discovery</li>
          <li>Accumulate Wisdom</li>
        </ul>
      </div>
      <div class="archetype-tile-statistics">
        <div class="archetype-tile-statistics-left">
          <div class="archetype-tile-statistic metagame-percentage">
            <div class="archetype-tile-statistic-name">META%</div>
            <div class="archetype-tile-statistic-value">
              14.9%
              <span class="archetype-tile-statistic-value-extra-data">(231)</span>
            </div>
          </div>
        </div>
        <div class="archetype-tile-statistics-right">
          <div class="archetype-tile-statistic deck-price-paper" style="display: block;">
            <div class="archetype-tile-statistic-name">Tabletop</div>
            <div class="archetype-tile-statistic-value">$&nbsp;275</div>
          </div>
          <div class="archetype-tile-statistic deck-price-online" style="display: none;">
            <div class="archetype-tile-statistic-name">MTGO</div>
            <div class="archetype-tile-statistic-value">43&nbsp;tix</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="archetype-tile" id="29070">
    <div class="archetype-tile-image">
      <div class="card-tile" role="img" aria-label="Image of Astrologian's Planisphere">
        <a class="card-image-tile-link-overlay" href="/archetype/standard-izzet-prowess-woe">
          <span class="sr-only">Astrologian's Planisphere</span>
        </a>
      </div>
    </div>
    <div class="archetype-tile-description-wrapper">
      <div class="archetype-tile-description">
        <div class="archetype-tile-title">
          <span class="deck-price-paper" style="display: inline;">
            <a href="/archetype/standard-izzet-prowess-woe#paper">Izzet Prowess</a>
          </span>
        </div>
        <div class="manacost-container">
          <span class="manacost" aria-label="colors: blue red">
            <i class="ms ms-u ms-cost ms-shadow"></i><i class="ms ms-r ms-cost ms-shadow"></i>
          </span>
        </div>
        <ul>
          <li>Astrologian's Planisphere</li>
          <li>Slickshot Show-Off</li>
          <li>Flow State</li>
        </ul>
      </div>
      <div class="archetype-tile-statistics">
        <div class="archetype-tile-statistics-left">
          <div class="archetype-tile-statistic metagame-percentage">
            <div class="archetype-tile-statistic-name">META%</div>
            <div class="archetype-tile-statistic-value">
              14.8%
              <span class="archetype-tile-statistic-value-extra-data">(230)</span>
            </div>
          </div>
        </div>
        <div class="archetype-tile-statistics-right">
          <div class="archetype-tile-statistic deck-price-paper" style="display: block;">
            <div class="archetype-tile-statistic-name">Tabletop</div>
            <div class="archetype-tile-statistic-value">$&nbsp;326</div>
          </div>
          <div class="archetype-tile-statistic deck-price-online" style="display: none;">
            <div class="archetype-tile-statistic-name">MTGO</div>
            <div class="archetype-tile-statistic-value">157&nbsp;tix</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</body></html>
"""


# ---------------------------------------------------------------------------
# HTML Parsing Tests
# ---------------------------------------------------------------------------

class TestArchetypeTileExtraction(unittest.TestCase):
    """Tests for extracting archetype-tile blocks from HTML."""

    def test_extract_finds_three_tiles(self) -> None:
        tiles = _extract_archetype_tiles(SAMPLE_HTML)
        self.assertEqual(len(tiles), 3)

    def test_extract_empty_html_returns_empty(self) -> None:
        tiles = _extract_archetype_tiles("<html><body>no decks here</body></html>")
        self.assertEqual(tiles, [])

    def test_extract_tile_contains_archetype_link(self) -> None:
        tiles = _extract_archetype_tiles(SAMPLE_HTML)
        self.assertIn("/archetype/standard-selesnya-ouroboroid-woe", tiles[0])


class TestArchetypeTileParsing(unittest.TestCase):
    """Tests for parsing individual archetype-tile HTML blocks."""

    def test_parse_first_deck(self) -> None:
        tiles = _extract_archetype_tiles(SAMPLE_HTML)
        deck = _parse_archetype_tile(tiles[0])
        self.assertIsNotNone(deck)
        self.assertEqual(deck.name, "Selesnya Ouroboroid")
        self.assertEqual(
            deck.archetype_url,
            "https://www.mtggoldfish.com/archetype/standard-selesnya-ouroboroid-woe",
        )
        self.assertAlmostEqual(deck.meta_share, 16.7, places=1)
        self.assertEqual(deck.deck_count, 260)
        self.assertEqual(deck.top_cards, [
            "Badgermole Cub",
            "Leatherhead, Swamp Stalker",
            "Spider Manifestation",
        ])
        self.assertEqual(deck.price_tabletop, 574.0)
        self.assertEqual(deck.price_mtgo, 150.0)
        self.assertEqual(deck.colors, "white green")

    def test_parse_second_deck(self) -> None:
        tiles = _extract_archetype_tiles(SAMPLE_HTML)
        deck = _parse_archetype_tile(tiles[1])
        self.assertIsNotNone(deck)
        self.assertEqual(deck.name, "Jeskai Lessons")
        self.assertAlmostEqual(deck.meta_share, 14.9, places=1)
        self.assertEqual(deck.deck_count, 231)
        self.assertEqual(deck.top_cards, [
            "Jeskai Revelation",
            "Tablet of Discovery",
            "Accumulate Wisdom",
        ])
        self.assertEqual(deck.price_tabletop, 275.0)
        self.assertEqual(deck.price_mtgo, 43.0)
        self.assertEqual(deck.colors, "white blue red")

    def test_parse_third_deck(self) -> None:
        tiles = _extract_archetype_tiles(SAMPLE_HTML)
        deck = _parse_archetype_tile(tiles[2])
        self.assertIsNotNone(deck)
        self.assertEqual(deck.name, "Izzet Prowess")
        self.assertAlmostEqual(deck.meta_share, 14.8, places=1)
        self.assertEqual(deck.deck_count, 230)
        self.assertEqual(deck.colors, "blue red")

    def test_parse_tile_without_link_returns_none(self) -> None:
        deck = _parse_archetype_tile("<div>no link here</div>")
        self.assertIsNone(deck)

    def test_parse_tile_with_missing_meta(self) -> None:
        html = """
        <div class="archetype-tile" id="123">
          <a href="/archetype/some-deck">Some Deck</a>
          <ul><li>Card A</li><li>Card B</li></ul>
        </div>
        """
        deck = _parse_archetype_tile(html)
        self.assertIsNotNone(deck)
        self.assertEqual(deck.name, "Some Deck")
        self.assertEqual(deck.meta_share, 0.0)
        self.assertEqual(deck.deck_count, 0)
        self.assertEqual(deck.top_cards, ["Card A", "Card B"])


class TestFullHtmlParsing(unittest.TestCase):
    """Tests for the full HTML parsing pipeline."""

    def test_parse_metagame_html_returns_meta_data(self) -> None:
        meta = _parse_metagame_html(SAMPLE_HTML, "standard", "https://example.com")
        self.assertEqual(meta.schema, "meta.v1")
        self.assertEqual(meta.format, "standard")
        self.assertEqual(len(meta.top_decks), 3)

    def test_parse_metagame_html_sorts_by_meta_share(self) -> None:
        meta = _parse_metagame_html(SAMPLE_HTML, "standard", "https://example.com")
        shares = [d.meta_share for d in meta.top_decks]
        self.assertEqual(shares, sorted(shares, reverse=True))
        self.assertEqual(meta.top_decks[0].name, "Selesnya Ouroboroid")
        self.assertAlmostEqual(meta.top_decks[0].meta_share, 16.7, places=1)

    def test_parse_metagame_html_builds_top_cards(self) -> None:
        meta = _parse_metagame_html(SAMPLE_HTML, "standard", "https://example.com")
        # Each deck has 3 unique top cards; some may overlap
        self.assertGreater(len(meta.top_cards), 0)
        # All cards should appear
        all_card_names = {c["name"] for c in meta.top_cards}
        self.assertIn("Badgermole Cub", all_card_names)
        self.assertIn("Flow State", all_card_names)

    def test_parse_metagame_html_empty_html(self) -> None:
        meta = _parse_metagame_html("<html></html>", "standard", "https://example.com")
        self.assertEqual(len(meta.top_decks), 0)
        self.assertTrue(any("No archetype" in w for w in meta.warnings))

    def test_parse_metagame_html_source_url_preserved(self) -> None:
        url = "https://www.mtggoldfish.com/metagame/standard"
        meta = _parse_metagame_html(SAMPLE_HTML, "standard", url)
        self.assertEqual(meta.source, url)


# ---------------------------------------------------------------------------
# Data Structure Tests
# ---------------------------------------------------------------------------

class TestMetaDataStructure(unittest.TestCase):
    """Tests for MetaData and MetaDeck data classes."""

    def test_meta_deck_to_dict(self) -> None:
        deck = MetaDeck(
            name="Test Deck",
            archetype_url="https://example.com/deck",
            meta_share=12.5,
            deck_count=100,
            top_cards=["Card A", "Card B"],
            price_tabletop=300.0,
            price_mtgo=50.0,
            colors="blue red",
        )
        d = deck.to_dict()
        self.assertEqual(d["name"], "Test Deck")
        self.assertEqual(d["metaShare"], 12.5)
        self.assertEqual(d["deckCount"], 100)
        self.assertEqual(d["topCards"], ["Card A", "Card B"])
        self.assertEqual(d["priceTabletop"], 300.0)
        self.assertEqual(d["priceMtgo"], 50.0)
        self.assertEqual(d["colors"], "blue red")

    def test_meta_data_to_dict(self) -> None:
        meta = MetaData(
            format="modern",
            fetched_at="2026-01-01T00:00:00Z",
            source="https://example.com",
        )
        meta.top_decks.append(MetaDeck(name="Deck 1", meta_share=10.0))
        meta.top_cards.append({"name": "Card 1", "decks": 5})

        d = meta.to_dict()
        self.assertEqual(d["schema"], "meta.v1")
        self.assertEqual(d["format"], "modern")
        self.assertEqual(len(d["topDecks"]), 1)
        self.assertEqual(d["topDecks"][0]["name"], "Deck 1")
        self.assertEqual(len(d["topCards"]), 1)

    def test_dict_to_meta_roundtrip(self) -> None:
        meta = MetaData(
            format="standard",
            fetched_at="2026-01-01T00:00:00Z",
            source="https://example.com",
        )
        meta.top_decks.append(MetaDeck(
            name="Test",
            meta_share=5.0,
            deck_count=10,
            top_cards=["A", "B"],
        ))
        meta.top_cards.append({"name": "A", "decks": 1})

        d = meta.to_dict()
        restored = _dict_to_meta(d)
        self.assertEqual(restored.format, "standard")
        self.assertEqual(len(restored.top_decks), 1)
        self.assertEqual(restored.top_decks[0].name, "Test")
        self.assertEqual(restored.top_decks[0].meta_share, 5.0)
        self.assertEqual(len(restored.top_cards), 1)


# ---------------------------------------------------------------------------
# Caching Tests
# ---------------------------------------------------------------------------

class TestCaching(unittest.TestCase):
    """Tests for the file-based caching system."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.cache_file = Path(self.tmpdir) / "meta-cache.json"
        self._orig_cache_file = None

    def tearDown(self) -> None:
        if self._orig_cache_file is not None:
            import advisor.meta as meta_mod
            meta_mod.CACHE_FILE = self._orig_cache_file
            meta_mod.CACHE_DIR = self._orig_cache_dir

    def _patch_cache_path(self) -> None:
        import advisor.meta as meta_mod
        self._orig_cache_file = meta_mod.CACHE_FILE
        self._orig_cache_dir = meta_mod.CACHE_DIR
        meta_mod.CACHE_FILE = self.cache_file
        meta_mod.CACHE_DIR = Path(self.tmpdir)

    def test_save_and_load_cache(self) -> None:
        self._patch_cache_path()
        from advisor.meta import _save_cache, _load_cache

        meta = MetaData(format="standard", fetched_at=_iso_now())
        meta.top_decks.append(MetaDeck(name="Test", meta_share=10.0))

        _save_cache(meta)
        loaded = _load_cache("standard", ttl=3600)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.format, "standard")
        self.assertEqual(len(loaded.top_decks), 1)
        self.assertEqual(loaded.top_decks[0].name, "Test")

    def test_cache_expired_returns_none(self) -> None:
        self._patch_cache_path()
        from advisor.meta import _save_cache, _load_cache

        # Create cache with old timestamp
        meta = MetaData(format="standard", fetched_at="2020-01-01T00:00:00Z")
        _save_cache(meta)

        # TTL=0 means always expired
        loaded = _load_cache("standard", ttl=0)
        self.assertIsNone(loaded)

    def test_cache_stale_allowed(self) -> None:
        self._patch_cache_path()
        from advisor.meta import _save_cache, _load_cache

        meta = MetaData(format="standard", fetched_at="2020-01-01T00:00:00Z")
        _save_cache(meta)

        loaded = _load_cache("standard", ttl=0, allow_stale=True)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.format, "standard")

    def test_cache_missing_format_returns_none(self) -> None:
        self._patch_cache_path()
        from advisor.meta import _save_cache, _load_cache

        meta = MetaData(format="standard", fetched_at=_iso_now())
        _save_cache(meta)

        loaded = _load_cache("modern", ttl=3600)
        self.assertIsNone(loaded)

    def test_cache_corrupt_file_returns_none(self) -> None:
        self._patch_cache_path()
        from advisor.meta import _load_cache

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text("not valid json {{{", encoding="utf-8")

        loaded = _load_cache("standard", ttl=3600)
        self.assertIsNone(loaded)


# ---------------------------------------------------------------------------
# File I/O Tests
# ---------------------------------------------------------------------------

class TestFileIO(unittest.TestCase):
    """Tests for save_meta_to_file and load_meta_from_file."""

    def test_save_and_load_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            tmp_path = Path(f.name)

        try:
            meta = MetaData(format="standard", fetched_at="2026-01-01T00:00:00Z")
            meta.top_decks.append(MetaDeck(name="Test", meta_share=10.0))

            save_meta_to_file(meta, tmp_path)
            self.assertTrue(tmp_path.exists())

            loaded = load_meta_from_file(tmp_path)
            self.assertEqual(loaded.format, "standard")
            self.assertEqual(len(loaded.top_decks), 1)
            self.assertEqual(loaded.top_decks[0].name, "Test")
        finally:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Formatting Tests
# ---------------------------------------------------------------------------

class TestFormatMetaForPrompt(unittest.TestCase):
    """Tests for LLM prompt formatting."""

    def test_format_with_decks(self) -> None:
        meta = MetaData(format="standard")
        meta.top_decks = [
            MetaDeck(name="Mono-Red Aggro", meta_share=18.5, deck_count=200, top_cards=["Goblin", "Burn"]),
            MetaDeck(name="Esper Control", meta_share=12.3, deck_count=130, top_cards=["Sunfall", "Sheoldred"]),
        ]
        text = format_meta_for_prompt(meta)
        self.assertIn("Aktuelles Meta (Standard):", text)
        self.assertIn("Mono-Red Aggro: 18.5%", text)
        self.assertIn("Esper Control: 12.3%", text)
        self.assertIn("(200 Decks)", text)
        self.assertIn("Goblin", text)

    def test_format_with_top_cards(self) -> None:
        meta = MetaData(format="standard")
        meta.top_decks = [MetaDeck(name="Deck A", meta_share=10.0, deck_count=50)]
        meta.top_cards = [
            {"name": "Sheoldred", "decks": 42},
            {"name": "Sunfall", "decks": 38},
        ]
        text = format_meta_for_prompt(meta)
        self.assertIn("Häufigste Karten:", text)
        self.assertIn("Sheoldred (42 Decks)", text)
        self.assertIn("Sunfall (38 Decks)", text)

    def test_format_empty_meta(self) -> None:
        meta = MetaData(format="standard")
        text = format_meta_for_prompt(meta)
        self.assertEqual(text, "")

    def test_format_max_decks_limit(self) -> None:
        meta = MetaData(format="standard")
        meta.top_decks = [
            MetaDeck(name=f"Deck {i}", meta_share=1.0, deck_count=1)
            for i in range(20)
        ]
        text = format_meta_for_prompt(meta, max_decks=5)
        # Only 5 decks should appear
        self.assertIn("Deck 0", text)
        self.assertIn("Deck 4", text)
        self.assertNotIn("Deck 5", text)


class TestFormatMetaTable(unittest.TestCase):
    """Tests for CLI table formatting."""

    def test_table_with_decks(self) -> None:
        meta = MetaData(format="standard", fetched_at="2026-08-02T16:00:00Z")
        meta.top_decks = [
            MetaDeck(name="Selesnya Ouroboroid", meta_share=16.7, deck_count=260,
                     top_cards=["Badgermole Cub", "Leatherhead", "Spider"]),
            MetaDeck(name="Jeskai Lessons", meta_share=14.9, deck_count=231,
                     top_cards=["Jeskai Revelation", "Tablet", "Wisdom"]),
        ]
        table = format_meta_table(meta)
        self.assertIn("Meta: Standard", table)
        self.assertIn("Selesnya Ouroboroid", table)
        self.assertIn("16.7%", table)
        self.assertIn("260", table)
        self.assertIn("Badgermole Cub", table)

    def test_table_empty_meta(self) -> None:
        meta = MetaData(format="standard")
        table = format_meta_table(meta)
        self.assertIn("Keine Meta-Daten", table)

    def test_table_max_decks(self) -> None:
        meta = MetaData(format="standard")
        meta.top_decks = [
            MetaDeck(name=f"Deck {i}", meta_share=1.0, deck_count=1)
            for i in range(50)
        ]
        table = format_meta_table(meta, max_decks=10)
        self.assertIn("Deck 0", table)
        self.assertIn("Deck 9", table)
        self.assertNotIn("Deck 10", table)

    def test_table_with_warnings(self) -> None:
        meta = MetaData(format="standard")
        meta.top_decks = [MetaDeck(name="Deck A", meta_share=10.0, deck_count=5)]
        meta.warnings.append("Test warning")
        table = format_meta_table(meta)
        self.assertIn("WARN: Test warning", table)


# ---------------------------------------------------------------------------
# fetch_meta Tests (with mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchMeta(unittest.TestCase):
    """Tests for the fetch_meta function with mocked HTTP."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self._patch_cache()

    def tearDown(self) -> None:
        self._unpatch_cache()

    def _patch_cache(self) -> None:
        import advisor.meta as meta_mod
        self._orig_cache_file = meta_mod.CACHE_FILE
        self._orig_cache_dir = meta_mod.CACHE_DIR
        meta_mod.CACHE_FILE = Path(self.tmpdir) / "meta-cache.json"
        meta_mod.CACHE_DIR = Path(self.tmpdir)

    def _unpatch_cache(self) -> None:
        import advisor.meta as meta_mod
        meta_mod.CACHE_FILE = self._orig_cache_file
        meta_mod.CACHE_DIR = self._orig_cache_dir

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_parses_html(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = SAMPLE_HTML
        meta = fetch_meta("standard", use_cache=False)
        self.assertEqual(meta.format, "standard")
        self.assertEqual(len(meta.top_decks), 3)
        self.assertEqual(meta.top_decks[0].name, "Selesnya Ouroboroid")
        self.assertAlmostEqual(meta.top_decks[0].meta_share, 16.7, places=1)

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_caches_result(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = SAMPLE_HTML
        # First call: fetches from network
        meta1 = fetch_meta("standard", use_cache=True)
        self.assertEqual(len(meta1.top_decks), 3)
        self.assertEqual(mock_fetch.call_count, 1)

        # Second call: should use cache (no new HTTP call)
        meta2 = fetch_meta("standard", use_cache=True)
        self.assertEqual(mock_fetch.call_count, 1)  # No new fetch
        self.assertEqual(len(meta2.top_decks), 3)

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_no_cache_refetches(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = SAMPLE_HTML
        fetch_meta("standard", use_cache=True)
        fetch_meta("standard", use_cache=False)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_unsupported_format_raises(self, mock_fetch: MagicMock) -> None:
        with self.assertRaises(ValueError):
            fetch_meta("invalid_format", use_cache=False)

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_network_error_no_cache_raises(self, mock_fetch: MagicMock) -> None:
        mock_fetch.side_effect = OSError("Network error")
        with self.assertRaises(RuntimeError):
            fetch_meta("standard", use_cache=False)

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_network_error_with_stale_cache(self, mock_fetch: MagicMock) -> None:
        # First populate cache
        mock_fetch.return_value = SAMPLE_HTML
        fetch_meta("standard", use_cache=True)

        # Now simulate network error
        mock_fetch.side_effect = OSError("Network error")
        meta = fetch_meta("standard", use_cache=True, cache_ttl=0)
        # Should return stale cache with warning
        self.assertEqual(len(meta.top_decks), 3)
        self.assertTrue(any("Network error" in w for w in meta.warnings))

    @patch("advisor.meta._fetch_url")
    def test_fetch_meta_full_url(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = SAMPLE_HTML
        fetch_meta("standard", use_cache=False, full=True)
        called_url = mock_fetch.call_args[0][0]
        self.assertIn("/full", called_url)


# ---------------------------------------------------------------------------
# Supported Formats Test
# ---------------------------------------------------------------------------

class TestSupportedFormats(unittest.TestCase):
    """Tests for supported format validation."""

    def test_standard_is_supported(self) -> None:
        self.assertIn("standard", SUPPORTED_FORMATS)

    def test_all_arena_formats(self) -> None:
        arena_formats = ["standard", "historic", "explorer", "alchemy", "brawl", "timeless"]
        for fmt in arena_formats:
            self.assertIn(fmt, SUPPORTED_FORMATS)

    def test_formats_are_lowercase(self) -> None:
        for fmt in SUPPORTED_FORMATS:
            self.assertEqual(fmt, fmt.lower())


if __name__ == "__main__":
    unittest.main()