"""MTGGoldfish Meta-Daten Scraper.

Scrapt die aktuelle Metagame-Seite von MTGGoldfish und extrahiert:
- Top-Decks (Archetyp-Name, Meta-Anteil %, Deck-Anzahl, Top-Karten)
- Format-Info

Der Scraper verwendet urllib (keine externen Dependencies) und parst das HTML
mit regulären Ausdrücken (keine BeautifulSoup-Abhängigkeit).

Caching:
- Ergebnisse werden als JSON in ~/.config/mtga-advisor/meta-cache.json gespeichert.
- Cache-Dauer: 6 Stunden (konfigurierbar über MTGA_META_CACHE_TTL Sekunden).
- Bei Netzwerkfehlern wird der Cache verwendet (falls vorhanden).

Schema (meta.v1):
{
  "schema": "meta.v1",
  "format": "standard",
  "fetchedAt": "2026-08-02T16:00:00Z",
  "source": "https://www.mtggoldfish.com/metagame/standard",
  "topDecks": [
    {
      "name": "Selesnya Ouroboroid",
      "archetypeUrl": "https://www.mtggoldfish.com/archetype/standard-selesnya-ouroboroid-woe",
      "metaShare": 16.7,
      "deckCount": 260,
      "topCards": ["Badgermole Cub", "Leatherhead, Swamp Stalker", "Spider Manifestation"],
      "priceTabletop": 574,
      "priceMtgo": 150
    }
  ],
  "topCards": [
    {"name": "Badgermole Cub", "decks": 3},
    {"name": "Flow State", "decks": 3}
  ]
}
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOLDFISH_BASE = "https://www.mtggoldfish.com"
METAGAME_URL_TEMPLATE = GOLDFISH_BASE + "/metagame/{format}"
METAGAME_FULL_URL_TEMPLATE = GOLDFISH_BASE + "/metagame/{format}/full"

SUPPORTED_FORMATS = [
    "standard",
    "modern",
    "pioneer",
    "historic",
    "explorer",
    "timeless",
    "alchemy",
    "pauper",
    "legacy",
    "vintage",
    "brawl",
    "commander",
]

CACHE_DIR = Path.home() / ".config" / "mtga-advisor"
CACHE_FILE = CACHE_DIR / "meta-cache.json"
DEFAULT_CACHE_TTL = 6 * 3600  # 6 hours
DEFAULT_TIMEOUT = 30  # seconds
USER_AGENT = "Mtga.advisor/0.1 (meta-scraper)"

# Regex patterns for parsing MTGGoldfish HTML
# Each archetype tile is in a <div class="archetype-tile" id="...">
ARCHETYPE_TILE_RE = re.compile(
    r'<div class="archetype-tile"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>\s*</div>',
    re.DOTALL,
)

# Fallback: capture until the next archetype-tile or end of section
ARCHETYPE_TILE_BLOCKS_RE = re.compile(
    r'<div class="archetype-tile"[^>]*>(.*?)(?=<div class="archetype-tile"|$)',
    re.DOTALL,
)

# Deck name from archetype link: <a href="/archetype/standard-...">Name</a>
# or <a href='/archetype/...'>Name</a>
ARCHETYPE_LINK_RE = re.compile(
    r'<a[^>]*href=["\'](/archetype/[^"\']+)["\'][^>]*>([^<]+)</a>',
)

# META% value: contains "16.7%" and "(260)"
META_PERCENT_RE = re.compile(
    r'(\d+\.?\d*)%\s*<span[^>]*>\s*\((\d+)\)',
)

# Fallback META%: "16.7% (260)" in plain text
META_PERCENT_TEXT_RE = re.compile(
    r'(\d+\.?\d*)%\s*\((\d+)\)',
)

# Top cards: <li>Card Name</li>
TOP_CARDS_RE = re.compile(
    r'<li>([^<]+)</li>',
)

# Price: $ 574 or $&nbsp;574 (HTML entity for non-breaking space)
PRICE_TABLETOP_RE = re.compile(
    r'\$\s*(?:&nbsp;)?\s*(\d+)',
)
PRICE_MTGO_RE = re.compile(
    r'(\d+)\s*(?:&nbsp;)?\s*tix',
)

# Colors: aria-label="colors: white green" or aria-label='colors: white green'
COLORS_RE = re.compile(
    r'aria-label=["\']colors:\s*([^"\']+)["\']',
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MetaDeck:
    """Ein Deck-Archetyp aus der MTGGoldfish Metagame-Seite."""
    name: str = ""
    archetype_url: str = ""
    meta_share: float = 0.0
    deck_count: int = 0
    top_cards: list[str] = field(default_factory=list)
    price_tabletop: float | None = None
    price_mtgo: float | None = None
    colors: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "archetypeUrl": self.archetype_url,
            "metaShare": self.meta_share,
            "deckCount": self.deck_count,
            "topCards": self.top_cards,
            "priceTabletop": self.price_tabletop,
            "priceMtgo": self.price_mtgo,
            "colors": self.colors,
        }


@dataclass
class MetaData:
    """Vollständige Meta-Daten für ein Format."""
    schema: str = "meta.v1"
    format: str = "standard"
    fetched_at: str = ""
    source: str = ""
    top_decks: list[MetaDeck] = field(default_factory=list)
    top_cards: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "format": self.format,
            "fetchedAt": self.fetched_at,
            "source": self.source,
            "topDecks": [d.to_dict() for d in self.top_decks],
            "topCards": self.top_cards,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_meta(
    format_name: str = "standard",
    *,
    use_cache: bool = True,
    cache_ttl: int | None = None,
    full: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> MetaData:
    """Fetch Meta-Daten von MTGGoldfish.

    Args:
        format_name: Format (standard, modern, pioneer, etc.)
        use_cache: Wenn True, verwende Cache bei Network-Fehlern oder wenn
                   der Cache noch gültig ist.
        cache_ttl: Cache-Dauer in Sekunden. Default: 6 Stunden.
        full: Wenn True, scrape die /full Seite (alle Decks, nicht nur Top-12).
        timeout: HTTP-Timeout in Sekunden.

    Returns:
        MetaData mit Top-Decks und Top-Cards.
    """
    fmt = format_name.lower().strip()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported format: {format_name}. "
            f"Supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    ttl = cache_ttl if cache_ttl is not None else int(
        os.environ.get("MTGA_META_CACHE_TTL", str(DEFAULT_CACHE_TTL))
    )

    # Try cache first
    if use_cache:
        cached = _load_cache(fmt, ttl)
        if cached is not None:
            return cached

    # Fetch from MTGGoldfish
    url_template = METAGAME_FULL_URL_TEMPLATE if full else METAGAME_URL_TEMPLATE
    url = url_template.format(format=fmt)

    try:
        html = _fetch_url(url, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        # On network error, try stale cache
        if use_cache:
            stale = _load_cache(fmt, ttl=0, allow_stale=True)
            if stale is not None:
                stale.warnings.append(f"Network error, using stale cache: {exc}")
                return stale
        raise RuntimeError(f"Failed to fetch MTGGoldfish metagame page: {exc}") from exc

    # Parse HTML
    meta = _parse_metagame_html(html, fmt, url)

    # Save cache
    if use_cache:
        _save_cache(meta)

    return meta


def load_meta_from_file(path: Path) -> MetaData:
    """Load Meta-Daten from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return _dict_to_meta(data)


def save_meta_to_file(meta: MetaData, path: Path) -> Path:
    """Save Meta-Daten to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def format_meta_for_prompt(meta: MetaData, max_decks: int = 10) -> str:
    """Format Meta-Daten für den LLM-Prompt.

    Erzeugt einen kompakten Text, der in den LLM-Advisor-Prompt eingefügt wird.

    Args:
        meta: Meta-Daten.
        max_decks: Maximale Anzahl Decks im Prompt.

    Returns:
        Formatierter Meta-Kontext als String.
    """
    if not meta.top_decks:
        return ""

    lines = [
        f"Aktuelles Meta ({meta.format.capitalize()}):",
    ]

    for deck in meta.top_decks[:max_decks]:
        share_str = f"{deck.meta_share:.1f}%"
        count_str = f"({deck.deck_count} Decks)" if deck.deck_count else ""
        cards_str = ", ".join(deck.top_cards[:3]) if deck.top_cards else ""
        lines.append(
            f"- {deck.name}: {share_str} {count_str}"
            + (f" — Top-Karten: {cards_str}" if cards_str else "")
        )

    if meta.top_cards:
        lines.append("")
        lines.append("Häufigste Karten:")
        for card in meta.top_cards[:10]:
            name = card.get("name", "?")
            decks = card.get("decks", 0)
            lines.append(f"  - {name} ({decks} Decks)")

    return "\n".join(lines)


def format_meta_table(meta: MetaData, max_decks: int = 20) -> str:
    """Format Meta-Daten als Tabelle für CLI-Ausgabe.

    Args:
        meta: Meta-Daten.
        max_decks: Maximale Anzahl Decks in der Tabelle.

    Returns:
        Formatierte Tabelle als String.
    """
    if not meta.top_decks:
        return f"Keine Meta-Daten für {meta.format} gefunden."

    lines = [
        f"Meta: {meta.format.capitalize()} (Quelle: MTGGoldfish)",
        f"Abgerufen: {meta.fetched_at}",
        "",
        f"{'#':>3}  {'Deck':<30} {'Meta%':>7} {'Decks':>6} {'Top-Karten':<40}",
        "-" * 90,
    ]

    for i, deck in enumerate(meta.top_decks[:max_decks], 1):
        cards_str = ", ".join(deck.top_cards[:3])
        if len(cards_str) > 38:
            cards_str = cards_str[:35] + "..."
        lines.append(
            f"{i:>3}  {deck.name:<30} {deck.meta_share:>6.1f}% {deck.deck_count:>6} {cards_str:<40}"
        )

    if meta.top_cards:
        lines.append("")
        lines.append("Häufigste Karten:")
        for card in meta.top_cards[:15]:
            name = card.get("name", "?")
            decks = card.get("decks", 0)
            lines.append(f"  {name:<35} {decks:>3} Decks")

    if meta.warnings:
        lines.append("")
        for w in meta.warnings:
            lines.append(f"  WARN: {w}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal: HTTP fetch
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Fetch HTML content from a URL."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Internal: HTML parsing
# ---------------------------------------------------------------------------

def _parse_metagame_html(html: str, fmt: str, source_url: str) -> MetaData:
    """Parse MTGGoldfish metagame HTML into MetaData.

    Uses regex to extract archetype tiles with deck names, meta shares,
    deck counts, top cards, and prices.
    """
    meta = MetaData(
        format=fmt,
        fetched_at=_iso_now(),
        source=source_url,
    )

    # Extract all archetype-tile blocks
    tiles = _extract_archetype_tiles(html)

    if not tiles:
        meta.warnings.append("No archetype tiles found in HTML")
        return meta

    card_counter: dict[str, int] = {}

    for tile_html in tiles:
        deck = _parse_archetype_tile(tile_html)
        if deck and deck.name:
            meta.top_decks.append(deck)
            # Count top cards across all decks
            for card_name in deck.top_cards:
                # Normalize: unescape HTML entities
                clean_name = html_module.unescape(card_name)
                card_counter[clean_name] = card_counter.get(clean_name, 0) + 1

    # Sort decks by meta share descending
    meta.top_decks.sort(key=lambda d: d.meta_share, reverse=True)

    # Build top cards list (cards appearing in most decks)
    meta.top_cards = [
        {"name": name, "decks": count}
        for name, count in sorted(
            card_counter.items(), key=lambda x: x[1], reverse=True
        )
    ][:30]

    return meta


def _extract_archetype_tiles(html: str) -> list[str]:
    """Extract individual archetype-tile HTML blocks from the page.

    Splits the HTML on 'archetype-tile' div boundaries.
    """
    tiles: list[str] = []

    # Find all positions of archetype-tile divs
    tile_starts = [
        m.start()
        for m in re.finditer(r'<div\s+class=["\']archetype-tile["\']', html)
    ]

    if not tile_starts:
        return tiles

    for i, start in enumerate(tile_starts):
        end = tile_starts[i + 1] if i + 1 < len(tile_starts) else None
        # Find a reasonable end: next archetype-tile or 5000 chars max
        if end:
            tile_html = html[start:end]
        else:
            # Last tile: take up to 5000 chars
            tile_html = html[start:start + 5000]

        tiles.append(tile_html)

    return tiles


def _parse_archetype_tile(tile_html: str) -> MetaDeck | None:
    """Parse a single archetype-tile HTML block into a MetaDeck."""
    deck = MetaDeck()

    # Extract deck name and URL from first archetype link
    link_match = ARCHETYPE_LINK_RE.search(tile_html)
    if link_match:
        archetype_path = link_match.group(1)
        deck.name = html_module.unescape(link_match.group(2).strip())
        # Strip URL fragment (#paper, #online) for a clean archetype URL
        archetype_path = archetype_path.split("#")[0]
        deck.archetype_url = GOLDFISH_BASE + archetype_path
    else:
        return None  # No link = not a valid tile

    # Handle duplicate links (paper/online variants) — take first
    # If name appears twice, we already got it

    # Extract META% and deck count
    meta_match = META_PERCENT_RE.search(tile_html)
    if meta_match:
        deck.meta_share = float(meta_match.group(1))
        deck.deck_count = int(meta_match.group(2))
    else:
        # Fallback: text-based regex
        meta_match2 = META_PERCENT_TEXT_RE.search(tile_html)
        if meta_match2:
            deck.meta_share = float(meta_match2.group(1))
            deck.deck_count = int(meta_match2.group(2))

    # Extract top cards (li elements)
    card_matches = TOP_CARDS_RE.findall(tile_html)
    if card_matches:
        deck.top_cards = [html_module.unescape(c.strip()) for c in card_matches if c.strip()]

    # Extract prices
    price_tabletop = PRICE_TABLETOP_RE.search(tile_html)
    if price_tabletop:
        deck.price_tabletop = float(price_tabletop.group(1))

    price_mtgo = PRICE_MTGO_RE.search(tile_html)
    if price_mtgo:
        deck.price_mtgo = float(price_mtgo.group(1))

    # Extract colors
    colors_match = COLORS_RE.search(tile_html)
    if colors_match:
        deck.colors = colors_match.group(1).strip()

    return deck


# ---------------------------------------------------------------------------
# Internal: Caching
# ---------------------------------------------------------------------------

def _load_cache(fmt: str, ttl: int, allow_stale: bool = False) -> MetaData | None:
    """Load cached Meta-Daten.

    Args:
        fmt: Format name.
        ttl: Cache time-to-live in seconds. 0 = always expired (unless allow_stale).
        allow_stale: If True, return cache even if expired.

    Returns:
        MetaData from cache, or None if no cache or expired.
    """
    if not CACHE_FILE.exists():
        return None

    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    # Check if cache has entry for this format
    cached = data.get(fmt)
    if not cached:
        return None

    # Check TTL
    fetched_at = cached.get("fetchedAt", "")
    if fetched_at and not allow_stale:
        try:
            fetched_time = datetime.fromisoformat(
                fetched_at.replace("Z", "+00:00")
            )
            now = datetime.now(timezone.utc)
            age = (now - fetched_time).total_seconds()
            if age > ttl:
                return None
        except (ValueError, TypeError):
            # Can't parse timestamp, treat as expired
            return None

    return _dict_to_meta(cached)


def _save_cache(meta: MetaData) -> None:
    """Save Meta-Daten to cache file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing cache (to preserve other formats)
    existing: dict[str, Any] = {}
    if CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    existing[meta.format] = meta.to_dict()

    CACHE_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _dict_to_meta(data: dict[str, Any]) -> MetaData:
    """Convert a dict (from JSON) to MetaData."""
    meta = MetaData(
        schema=data.get("schema", "meta.v1"),
        format=data.get("format", "unknown"),
        fetched_at=data.get("fetchedAt", ""),
        source=data.get("source", ""),
        top_cards=data.get("topCards", []),
        warnings=data.get("warnings", []),
    )

    for deck_data in data.get("topDecks", []):
        deck = MetaDeck(
            name=deck_data.get("name", ""),
            archetype_url=deck_data.get("archetypeUrl", ""),
            meta_share=deck_data.get("metaShare", 0.0),
            deck_count=deck_data.get("deckCount", 0),
            top_cards=deck_data.get("topCards", []),
            price_tabletop=deck_data.get("priceTabletop"),
            price_mtgo=deck_data.get("priceMtgo"),
            colors=deck_data.get("colors", ""),
        )
        meta.top_decks.append(deck)

    return meta


# ---------------------------------------------------------------------------
# Internal: Utilities
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """ISO 8601 timestamp in UTC."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )