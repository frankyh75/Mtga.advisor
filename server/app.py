"""MTGA Advisor Dashboard — lokaler Webserver.

Zeigt Collection, Decks, LLM-Advisor-Ergebnisse und Run-Reports an.
Unterstützt interaktive Deck-Auswahl und LLM-Chat.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.request
import urllib.error
import urllib.parse
import threading
import time
import socket
import io
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import html
import os
import secrets
from string import Template
from urllib.parse import urlparse, parse_qs

from advisor.llm_config import LLMConfig, load_config, write_default_config
from scanner.history import (
    list_snapshots as list_history_snapshots,
    diff_snapshots as diff_history_snapshots,
    save_snapshot as save_history_snapshot,
)


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "out"
DASHBOARD_JS_PATH = Path(__file__).with_name("dashboard.js")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], *, status: int) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _html_response(handler: BaseHTTPRequestHandler, body: str, *, status: int) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' https://cards.scryfall.io https://c1.scryfall.com https://c2.scryfall.com data:; script-src 'self'; connect-src 'self';",
    )
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _js_response(handler: BaseHTTPRequestHandler, body: str, *, status: int) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/javascript; charset=utf-8")
    handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _dashboard_js() -> str:
    return DASHBOARD_JS_PATH.read_text(encoding="utf-8")


# --- Scryfall image lookup with in-memory cache ---

_SCRYFALL_IMAGE_CACHE: dict[int, str | None] = {}
_SCRYFALL_CACHE_LOCK = threading.Lock()
_SCRYFALL_API_BASE = "https://api.scryfall.com/cards/arena"


def _fetch_scryfall_image_url(grp_id: int) -> str | None:
    """Fetch a card image URL from Scryfall by Arena grp_id.

    Returns the 'normal' size image URI, or None if not found / on error.
    Caches results in-memory (including None for misses) to avoid repeated API calls.
    """
    with _SCRYFALL_CACHE_LOCK:
        if grp_id in _SCRYFALL_IMAGE_CACHE:
            return _SCRYFALL_IMAGE_CACHE[grp_id]

    url = f"{_SCRYFALL_API_BASE}/{grp_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mtga.advisor/0.1",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            with _SCRYFALL_CACHE_LOCK:
                _SCRYFALL_IMAGE_CACHE[grp_id] = None
            return None
        with _SCRYFALL_CACHE_LOCK:
            _SCRYFALL_IMAGE_CACHE[grp_id] = None
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        with _SCRYFALL_CACHE_LOCK:
            _SCRYFALL_IMAGE_CACHE[grp_id] = None
        return None

    # Scryfall card object: image_uris.normal or image_uris.small
    image_uris = data.get("image_uris")
    if not image_uris:
        # Double-faced cards: use card_faces[0].image_uris
        card_faces = data.get("card_faces", [])
        if card_faces:
            image_uris = card_faces[0].get("image_uris")

    image_url = None
    if image_uris:
        image_url = image_uris.get("normal") or image_uris.get("small")

    with _SCRYFALL_CACHE_LOCK:
        _SCRYFALL_IMAGE_CACHE[grp_id] = image_url
    return image_url


def _send_redirect(handler: BaseHTTPRequestHandler, target_url: str) -> None:
    handler.send_response(HTTPStatus.FOUND)
    handler.send_header("Location", target_url)
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _send_image_not_found(handler: BaseHTTPRequestHandler) -> None:
    # Return a 1x1 transparent PNG as placeholder
    transparent_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c6300010000050001a5f645240000000049454e44ae426082"
    )
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Content-Length", str(len(transparent_png)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(transparent_png)


def _format_json(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return "(keine Daten gefunden)"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)



def _json_for_script(payload: Any) -> str:
    """Serialisiere JSON so, dass es sicher in einem script[type=json] liegt."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
# ---------------------------------------------------------------------------
# Basic-Auth middleware
# ---------------------------------------------------------------------------

_AUTH_ENABLED: bool = False
_AUTH_USER: str = ""
_AUTH_PASS: str = ""


def configure_basic_auth(credentials: str | None) -> None:
    """Enable optional Basic-Auth.

    Args:
        credentials: ``user:password`` string, or None/empty to disable.
    """
    global _AUTH_ENABLED, _AUTH_USER, _AUTH_PASS
    if not credentials or ":" not in credentials:
        _AUTH_ENABLED = False
        _AUTH_USER = ""
        _AUTH_PASS = ""
        return
    user, _, password = credentials.partition(":")
    _AUTH_USER = user
    _AUTH_PASS = password
    _AUTH_ENABLED = True

def _check_auth(handler: BaseHTTPRequestHandler) -> bool:
    """Return True if the request is authorized (or auth is disabled)."""
    if not _AUTH_ENABLED:
        return True
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, _, password = decoded.partition(":")
    # Constant-time comparison to prevent timing attacks
    user_ok = secrets.compare_digest(user, _AUTH_USER)
    pass_ok = secrets.compare_digest(password, _AUTH_PASS)
    return user_ok and pass_ok


def _send_auth_required(handler: BaseHTTPRequestHandler) -> None:
    """Send a 401 Unauthorized response with WWW-Authenticate header."""
    body = json.dumps({"error": "unauthorized", "message": "Authentifizierung erforderlich."}, ensure_ascii=False)
    encoded = body.encode("utf-8")
    handler.send_response(HTTPStatus.UNAUTHORIZED)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("WWW-Authenticate", 'Basic realm="MTGA Advisor"')
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(encoded)


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------

def _health_response(handler: BaseHTTPRequestHandler, output_dir: Path) -> None:
    """Return a JSON health status."""
    status: dict[str, Any] = {
        "status": "ok",
        "timestamp": time.time(),
        "version": "0.3",
        "output_dir": str(output_dir),
        "auth_enabled": _AUTH_ENABLED,
    }
    # Check for key data files
    checks: dict[str, bool] = {}
    for fname in ("collection.json", "decks.json", "run-report.json"):
        checks[fname] = (output_dir / fname).exists()
    status["files"] = checks
    _json_response(handler, status, status=HTTPStatus.OK)


# ---------------------------------------------------------------------------
# QR-Code generation for terminal display
# ---------------------------------------------------------------------------

def _get_lan_ip() -> str:
    """Determine the local network IP address (non-loopback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _generate_qr_ascii(url: str) -> str:
    """Generate a QR code as ASCII art string.

    Falls back to a simple message if the ``qrcode`` package is not installed.
    """
    try:
        import qrcode  # type: ignore[import-untyped]
    except ImportError:
        return f"  (qrcode package not installed — install with: pip install qrcode)\n  URL: {url}"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,  # type: ignore[attr-defined]
        box_size=1,
        border=1,
    )
    qr.add_data(url)
    qr.make()
    buf = io.StringIO()
    qr.print_ascii(invert=True, out=buf)
    return buf.getvalue()


def _print_server_banner(host: str, port: int, lan_ip: str, auth_enabled: bool, show_qr: bool) -> None:
    """Print a startup banner with connection info and optional QR code."""
    # Determine display URL
    if host == "0.0.0.0":
        display_host = lan_ip
    else:
        display_host = host
    url = f"http://{display_host}:{port}"

    print()
    print("=" * 60)
    print("  MTGA Advisor Dashboard")
    print("=" * 60)
    print(f"  URL:   {url}")
    print(f"  Host:  {host}:{port}")
    if host == "0.0.0.0":
        print(f"  LAN:   {lan_ip}:{port}")
    print(f"  Auth:  {'enabled' if auth_enabled else 'disabled'}")
    print("=" * 60)

    if show_qr:
        print()
        print(_generate_qr_ascii(url))
        print()
        print("  Scan the QR code with your phone to open the dashboard.")
        print()
        print("=" * 60)


def _extract_diagnostics(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"completeness": None, "warnings": ["Artefakt fehlt."], "evidence": []}
    diagnostics = payload.get("diagnostics", {})
    return {
        "completeness": diagnostics.get("completeness"),
        "warnings": list(diagnostics.get("warnings", [])),
        "evidence": list(diagnostics.get("evidence", [])),
    }


def _render_list(items: list[str]) -> str:
    if not items:
        return "<p>Keine.</p>"
    rows = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{rows}</ul>"


def _slug(value: str) -> str:
    value = value.lower().strip()
    slug = []
    for ch in value:
        if ch.isalnum():
            slug.append(ch)
        else:
            slug.append("-")
    return "".join(slug).strip("-") or "deck"


def _deck_keys_for_payload(deck: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    deck_key = deck.get("deckKey")
    if deck_key:
        keys.add(str(deck_key))
    deck_id = deck.get("deckId")
    if deck_id:
        keys.add(str(deck_id))
    deck_tile_id = deck.get("deckTileId")
    if deck_tile_id is not None:
        keys.add(f"tile-{deck_tile_id}")
    name = deck.get("name")
    if isinstance(name, str) and name:
        keys.add(name)
        keys.add(_slug(name))
    return keys


def _find_deck_payload(output_dir: Path, deck_key: str) -> dict[str, Any] | None:
    normalized = urllib.parse.unquote(deck_key).strip()
    if not normalized:
        return None

    deck_dir = output_dir / "decks"
    container_candidates = [
        deck_dir / f"deck-{_slug(normalized)}.arena.json",
        deck_dir / f"deck-{_slug(normalized)}.json",
        deck_dir / f"deck-{normalized}.arena.json",
        deck_dir / f"deck-{normalized}.json",
        deck_dir / f"{_slug(normalized)}.arena.json",
        deck_dir / f"{_slug(normalized)}.json",
        deck_dir / f"{normalized}.arena.json",
        deck_dir / f"{normalized}.json",
    ]
    for candidate in container_candidates:
        if candidate.exists():
            payload = _read_json(candidate)
            if payload is not None:
                return payload

    decks = _read_json(output_dir / "decks.json")
    if not decks:
        return None

    for deck in decks.get("decks", []):
        if normalized in _deck_keys_for_payload(deck):
            return deck
    return None


def _extract_deck_cards(deck: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "deckId": deck.get("deckId", ""),
        "name": deck.get("name", "Unnamed"),
        "mainboard": [],
        "sideboard": [],
        "commandZone": [],
        "companions": [],
        "totalCards": 0,
    }

    cards_field = deck.get("cards")
    if isinstance(cards_field, dict):
        for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
            pile = cards_field.get(pile_key, [])
            if isinstance(pile, list):
                result[pile_key] = pile
            elif isinstance(pile, dict):
                result[pile_key] = [
                    {"cardId": int(gid) if str(gid).isdigit() else gid, "name": f"ID:{gid}", "count": qty}
                    for gid, qty in sorted(pile.items())
                ]
    elif isinstance(cards_field, list):
        result["mainboard"] = cards_field

    cards_by_id = deck.get("cardsById")
    if isinstance(cards_by_id, dict):
        for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
            if not result.get(pile_key) and pile_key in cards_by_id:
                pile = cards_by_id[pile_key]
                if isinstance(pile, dict):
                    result[pile_key] = [
                        {"cardId": int(gid) if str(gid).isdigit() else gid, "name": f"ID:{gid}", "count": qty}
                        for gid, qty in sorted(pile.items())
                    ]

    if not result["mainboard"]:
        main_deck = deck.get("mainDeck", [])
        if isinstance(main_deck, list):
            result["mainboard"] = [
                {
                    "cardId": c.get("grpId", c.get("cardId", 0)),
                    "name": c.get("name", f"ID:{c.get('grpId', '?')}"),
                    "count": c.get("quantity", c.get("count", 1)),
                }
                for c in main_deck
            ]

    if not result["sideboard"]:
        sideboard = deck.get("sideboard", [])
        if isinstance(sideboard, list):
            result["sideboard"] = [
                {
                    "cardId": c.get("grpId", c.get("cardId", 0)),
                    "name": c.get("name", f"ID:{c.get('grpId', '?')}"),
                    "count": c.get("quantity", c.get("count", 1)),
                }
                for c in sideboard
            ]

    total = 0
    for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
        for card in result.get(pile_key, []):
            total += int(card.get("count", card.get("quantity", 1)))
    result["totalCards"] = total
    return result


def _summary_card(label: str, value: Any, detail: str = "") -> str:
    return (
        '<div class="card">'
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(detail)}</small>"
        "</div>"
    )


def _collection_summary(collection: dict[str, Any] | None) -> str:
    if not collection:
        return _summary_card("Collection", "fehlt", "collection.json nicht gefunden")
    cards = collection.get("cards", {})
    total = sum(int(value) for value in cards.values()) if isinstance(cards, dict) else 0
    return "".join(
        [
            _summary_card("Unique IDs", len(cards), "collection.json"),
            _summary_card("Total Cards", total, str(collection.get("source", "unknown"))),
        ]
    )


def _decks_summary(decks: dict[str, Any] | None) -> str:
    if not decks:
        return _summary_card("Decks", "fehlt", "decks.json nicht gefunden")
    deck_list = decks.get("decks", [])
    return _summary_card("Decks", len(deck_list), "decks.json")


def _llm_advisor_summary(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return _summary_card("LLM Advisor", "fehlt", "advisor-result.json nicht gefunden")
    summary = advisor.get("summary", {})
    priorities = advisor.get("craftingPriorities", [])
    optimizations = advisor.get("deckOptimizations", [])
    return "".join(
        [
            _summary_card("Top Priority", summary.get("topPriority", "?"), "LLM-Empfehlung"),
            _summary_card("Crafting Priorities", len(priorities), "Gruppen"),
            _summary_card("Deck Optimizations", len(optimizations), "Decks"),
        ]
    )


def _render_decks_table(decks: dict[str, Any] | None) -> str:
    if not decks:
        return "<p>Noch kein Deck-Export vorhanden. Führe <code>python3 -m cli.main run</code> aus.</p>"
    deck_list = decks.get("decks", [])
    if not deck_list:
        return "<p>Keine Decks gefunden.</p>"
    rows = []
    for i, deck in enumerate(deck_list[:50]):
        name = deck.get("name", "Unnamed")
        fmt = deck.get("attributes", {}).get("Format", "?")
        deck_id = deck.get("deckKey") or deck.get("deckId") or deck.get("deckTileId") or i
        legalities = deck.get("formatLegalities", {})
        legal = ", ".join(f for f, v in legalities.items() if v)[:40] or "?"
        is_precon = bool(deck.get("isPrecon"))
        type_label = "Precon" if is_precon else "Deck"
        row = (
            "<tr class='deck-row' "
            f"data-deck-key='{html.escape(str(deck_id))}' "
            f"data-deck-name='{html.escape(name)}' "
            f"data-is-precon='{str(is_precon).lower()}'>"
            + f"<td>{html.escape(name)}</td>"
            + f"<td>{html.escape(fmt)}</td>"
            + f"<td><span class='deck-type {'precon' if is_precon else 'normal'}'>{html.escape(type_label)}</span></td>"
            + f"<td>{html.escape(legal)}</td>"
            + (
                "<td><button class='btn-select-deck' "
                f"data-deck-key='{html.escape(str(deck_id))}' "
                f"data-deck-name='{html.escape(name)}'>Select</button></td>"
            )
            + "</tr>"
        )
        rows.append(row)
    suffix = ""
    if len(deck_list) > 50:
        suffix = f'<p class="meta">Weitere {len(deck_list) - 50} Decks im JSON.</p>'
    return (
        "<table><thead><tr><th>Name</th><th>Format</th><th>Typ</th><th>Legal in</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{suffix}"
    )


def _render_crafting_priorities(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return "<p>Noch kein LLM-Advisor-Ergebnis vorhanden.</p>"
    priorities = advisor.get("craftingPriorities", [])
    if not priorities:
        return "<p>Keine Crafting-Prioritäten.</p>"
    sections = []
    for p in priorities[:10]:
        reason = p.get("reason", "?")
        cards = p.get("cards", [])
        card_rows = "".join(
            f"<li>{html.escape(c.get('name', '?'))} "
            f"({c.get('count', 1)}x, {c.get('rarity', '?')}) "
            f"für {', '.join(c.get('forDecks', ['?']))}</li>"
            for c in cards
        )
        sections.append(
            "<div class='priority-group'>"
            f"<h3>{html.escape(reason)}</h3>"
            f"<ul>{card_rows}</ul>"
            "</div>"
        )
    return "".join(sections)


def _render_deck_optimizations(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return ""
    optimizations = advisor.get("deckOptimizations", [])
    if not optimizations:
        return ""
    sections = []
    for d in optimizations[:5]:
        name = d.get("deckName", "?")
        fmt = d.get("format", "?")
        issues = d.get("issues", [])
        suggestions = d.get("suggestions", [])
        crafting = d.get("craftingNeeded", "")
        sections.append(
            "<div class='optimization-group'>"
            f"<h3>{html.escape(name)} ({html.escape(fmt)})</h3>"
            f"<h4>Issues</h4><ul>{''.join(f'<li>{html.escape(i)}</li>' for i in issues)}</ul>"
            f"<h4>Suggestions</h4><ul>{''.join(f'<li>{html.escape(s)}</li>' for s in suggestions)}</ul>"
            f"<p><strong>Crafting:</strong> {html.escape(crafting)}</p>"
            "</div>"
        )
    return "".join(sections)


def _extract_deck_cards(deck: dict[str, Any]) -> dict[str, Any]:
    """Extract card lists from a deck dict (supports both deck-scan and log-export formats).

    Deck-scan format (from deck-scan CLI):
      deck["cards"] = {"mainboard": [{cardId, name, count}, ...], "sideboard": [...]}
      deck["cardsById"] = {"mainboard": {"grpId": qty, ...}}

    Log-export format (from StartHook):
      deck["mainDeck"] = [{grpId, quantity, ...}, ...]  (or similar)

    Returns a normalized dict:
      {"deckId": ..., "name": ..., "mainboard": [...], "sideboard": [...],
       "commandZone": [...], "companions": [...], "totalCards": N}
    """
    deck_id = deck.get("deckId", "")
    name = deck.get("name", "Unnamed")
    result: dict[str, Any] = {
        "deckId": deck_id,
        "name": name,
        "mainboard": [],
        "sideboard": [],
        "commandZone": [],
        "companions": [],
        "totalCards": 0,
    }

    cards_field = deck.get("cards")
    if isinstance(cards_field, dict):
        # Deck-scan format: cards = {pileKey: [{cardId, name, count}, ...]}
        for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
            pile = cards_field.get(pile_key, [])
            if isinstance(pile, list):
                result[pile_key] = pile
            elif isinstance(pile, dict):
                # cardsById format: {grpId_str: qty}
                result[pile_key] = [
                    {"cardId": int(gid) if str(gid).isdigit() else gid, "name": f"ID:{gid}", "count": qty}
                    for gid, qty in sorted(pile.items())
                ]
    elif isinstance(cards_field, list):
        # Flat card list — treat as mainboard
        result["mainboard"] = cards_field

    # Also check cardsById
    cards_by_id = deck.get("cardsById")
    if isinstance(cards_by_id, dict):
        for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
            if not result.get(pile_key) and pile_key in cards_by_id:
                pile = cards_by_id[pile_key]
                if isinstance(pile, dict):
                    result[pile_key] = [
                        {"cardId": int(gid) if str(gid).isdigit() else gid, "name": f"ID:{gid}", "count": qty}
                        for gid, qty in sorted(pile.items())
                    ]

    # Log-export format fallback: mainDeck / sideboard
    if not result["mainboard"]:
        main_deck = deck.get("mainDeck", [])
        if isinstance(main_deck, list):
            result["mainboard"] = [
                {"cardId": c.get("grpId", c.get("cardId", 0)), "name": c.get("name", f"ID:{c.get('grpId', '?')}"), "count": c.get("quantity", c.get("count", 1))}
                for c in main_deck
            ]
    if not result["sideboard"]:
        sb = deck.get("sideboard", [])
        if isinstance(sb, list):
            result["sideboard"] = [
                {"cardId": c.get("grpId", c.get("cardId", 0)), "name": c.get("name", f"ID:{c.get('grpId', '?')}"), "count": c.get("quantity", c.get("count", 1))}
                for c in sb
            ]

    # Total card count
    total = 0
    for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
        for card in result.get(pile_key, []):
            total += int(card.get("count", card.get("quantity", 1)))
    result["totalCards"] = total

    return result


def _render_deck_cards_section(deck: dict[str, Any] | None) -> str:
    """Render a deck's card list as HTML for the dashboard.

    Shows mainboard and sideboard in a compact table. Called when a deck is selected.
    Returns empty string if no cards available.
    """
    if not deck:
        return ""
    cards_data = _extract_deck_cards(deck)
    if not any(cards_data.get(k) for k in ("mainboard", "sideboard", "commandZone", "companions")):
        return ""

    sections = []
    for pile_key, label in [("mainboard", "Mainboard"), ("sideboard", "Sideboard"), ("commandZone", "Command Zone"), ("companions", "Companions")]:
        pile = cards_data.get(pile_key, [])
        if not pile:
            continue
        rows = ""
        for card in pile[:80]:
            name = html.escape(str(card.get("name", "?")))
            count = card.get("count", card.get("quantity", 1))
            card_id = card.get("cardId", card.get("grpId", ""))
            card_id_str = str(card_id) if card_id else ""
            rows += (
                f"<tr class='card-row' data-card-id='{html.escape(card_id_str)}'>"
                f"<td>{count}x</td>"
                f"<td>{name}</td>"
                f"<td class='card-thumb-cell'>"
                f"<div class='card-thumb-placeholder' data-card-id='{html.escape(card_id_str)}'>?</div>"
                f"</td></tr>"
            )
        suffix = ""
        if len(pile) > 80:
            suffix = f'<p class="meta">+{len(pile) - 80} weitere</p>'
        sections.append(
            f'<div class="deck-cards-pile">'
            f'<h4>{label} ({len(pile)} Karten)</h4>'
            f'<table><thead><tr><th>Anz</th><th>Name</th><th>Img</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>{suffix}</div>'
        )

    return f'<div class="deck-cards-section">{"=".join(sections)}</div>' if sections else ""


def _render_meta_notes(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return ""
    notes = advisor.get("metaNotes", [])
    if not notes:
        return ""
    return f"<ul>{''.join(f'<li>{html.escape(n)}</li>' for n in notes)}</ul>"


def _render_history_section(output_dir: Path) -> str:
    """Render the Collection-History section for the dashboard.

    Shows snapshot list and a diff button. The actual diff content is
    loaded dynamically via JavaScript from /api/history/diff.
    """
    snapshots = list_history_snapshots(output_dir=output_dir)
    snapshot_count = len(snapshots)

    if snapshot_count == 0:
        return (
            '<div class="section" id="history-section">'
            '<h2>Collection-History <span class="badge">T4</span></h2>'
            '<p class="meta">Noch keine Snapshots gespeichert. '
            'Führe <code>python3 -m cli.main history save</code> aus '
            'oder klicke auf "Snapshot speichern".</p>'
            '<button id="history-save-btn" class="btn-select-deck">Snapshot speichern</button>'
            '</div>'
        )

    # Build snapshot list
    snapshot_rows = ""
    for i, s in enumerate(snapshots):
        ts = html.escape(s.get("timestamp", "?"))
        stats = []
        if "collectionStats" in s:
            cs = s["collectionStats"]
            stats.append(f"{cs['uniqueCards']} unique, {cs['totalCards']} total")
        if "deckStats" in s:
            ds = s["deckStats"]
            stats.append(f"{ds['deckCount']} decks")
        stat_str = f" ({', '.join(stats)})" if stats else ""
        marker = " ← latest" if i == snapshot_count - 1 else ""
        snapshot_rows += f"<li>{ts}{html.escape(stat_str)}{html.escape(marker)}</li>"

    diff_available = snapshot_count >= 2

    diff_btn = ""
    if diff_available:
        diff_btn = '<button id="history-diff-btn" class="btn-select-deck" style="margin-left:.5rem">Diff anzeigen</button>'

    return (
        '<div class="section" id="history-section">'
        '<h2>Collection-History <span class="badge">T4</span></h2>'
        f'<p class="meta">{snapshot_count} Snapshot(s) gespeichert.</p>'
        f'<ul class="history-list">{snapshot_rows}</ul>'
        '<div class="history-controls">'
        '<button id="history-save-btn" class="btn-select-deck">Snapshot speichern</button>'
        f'{diff_btn}'
        '</div>'
        '<div id="history-diff-result" style="display:none;margin-top:1rem;">'
        '<h3>Diff (letzter vs. vorletzter Snapshot)</h3>'
        '<div id="history-diff-content"></div>'
        '</div>'
        '</div>'
    )


def _build_chat_prompt(deck: dict[str, Any], collection: dict[str, Any] | None, question: str) -> str:
    """Baue einen Chat-Prompt für eine spezifische Deck-Frage."""
    lines = [
        "Du bist ein MTG Arena Deck-Building Experte.",
        "Beantworte die Frage des Nutzers basierend auf dem Deck und der Collection.",
        "",
        "## Deck",
    ]

    name = deck.get("name", "Unnamed")
    fmt = deck.get("attributes", {}).get("Format", "unknown")
    lines.append(f"Name: {name}")
    lines.append(f"Format: {fmt}")

    # Deck cards if available — supports deck-scan format (cards as dict of piles)
    # and log-export format (mainDeck as flat list)
    deck_cards = deck.get("cards", deck.get("mainDeck", []))
    if deck_cards:
        lines.append("\nKarten:")
        if isinstance(deck_cards, dict):
            # Deck-scan format: {"mainboard": [{cardId, name, count}, ...], "sideboard": [...]}
            for pile_key, label in [("mainboard", "Mainboard"), ("sideboard", "Sideboard"), ("commandZone", "Command Zone"), ("companions", "Companions")]:
                pile = deck_cards.get(pile_key, [])
                if not pile:
                    continue
                lines.append(f"  {label}:")
                if isinstance(pile, list):
                    for card in pile[:60]:
                        if isinstance(card, dict):
                            lines.append(f"    {card.get('name', '?')} x{card.get('count', card.get('quantity', 1))}")
                        elif isinstance(card, str):
                            lines.append(f"    {card}")
                elif isinstance(pile, dict):
                    for card_id, count in list(pile.items())[:60]:
                        lines.append(f"    ID:{card_id} x{count}")
        elif isinstance(deck_cards, list):
            for card in deck_cards[:60]:
                if isinstance(card, dict):
                    lines.append(f"  {card.get('name', '?')} x{card.get('count', card.get('quantity', 1))}")
                elif isinstance(card, str):
                    lines.append(f"  {card}")

    # Collection context
    if collection:
        cards = collection.get("cards", {})
        wildcards = collection.get("wildcards", {})
        total = sum(int(v) for v in cards.values()) if isinstance(cards, dict) else 0
        lines.append(f"\n## Collection ({len(cards)} unique, {total} total)")
        if wildcards:
            lines.append("Wildcards:")
            for key in ("wcCommon", "wcUncommon", "wcRare", "wcMythic"):
                val = wildcards.get(key, 0)
                lines.append(f"  {key.replace('wc', '')}: {val}")

    lines.append(f"\n## Frage\n{question}")
    lines.append("\nAntworte auf Deutsch, präzise und mit konkreten Kartennamen.")

    return "\n".join(lines)


def _call_llm_chat(config: LLMConfig, prompt: str) -> dict[str, Any]:
    """Rufe LLM für Chat auf. Returns dict with response or error."""
    payload = {
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": False,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {exc.code}: {error_body[:500]}"}
    except urllib.error.URLError as exc:
        return {"error": f"LLM nicht erreichbar: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}

    choices = body.get("choices", [])
    if not choices:
        return {"error": "LLM gab keine Choices zurück"}

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        return {"error": "LLM gab leere Antwort"}

    return {"response": content, "model": config.model_name}


# ---------------------------------------------------------------------------
# /api/helper/status — Helper-Daemon Status
# ---------------------------------------------------------------------------

_HELPER_BUNDLE_PATH = Path(__file__).resolve().parents[1] / "helper" / "build" / "mtga-helper.bundle"
_HELPER_INSTALL_SCRIPT = Path(__file__).resolve().parents[1] / "helper" / "install.sh"
_HELPER_PLIST_PATH = "/Library/LaunchDaemons/com.mtga.helper.plist"
_HELPER_SOCK_PATH = "/tmp/mtga-helper.sock"


def _helper_status_response() -> dict[str, Any]:
    """Ermittelt den Status des Sudo-Helper-Daemons für das Dashboard.

    Returns:
        Dict mit Feldern:
        - installed: bool — Bundle liegt in /Library/PrivilegedHelperTools
        - running: bool — Daemon antwortet auf Ping
        - socket_path: str — Pfad zum UNIX-Socket
        - pid: int | None — Prozess-ID des Daemons
        - version: str | None — Version des Daemons
        - bundle_built: bool — Helper-Bundle wurde lokal gebaut
        - install_instructions: str — Kurze Anleitung falls nicht installiert
    """

    status: dict[str, Any] = {
        "installed": False,
        "running": False,
        "socket_path": _HELPER_SOCK_PATH,
        "pid": None,
        "version": None,
        "bundle_built": _HELPER_BUNDLE_PATH.exists(),
        "install_instructions": "",
    }

    # Prüfe ob der Helper installiert ist (plist oder bundle im Systemverzeichnis)
    helper_system_bundle = Path("/Library/PrivilegedHelperTools/mtga-helper.bundle")
    status["installed"] = (
        Path(_HELPER_PLIST_PATH).exists()
        or helper_system_bundle.exists()
    )

    # Prüfe ob der Helper läuft und antwortet
    try:
        from scanner.helper_client import get_status, is_helper_available

        helper_status = get_status(_HELPER_SOCK_PATH)
        status["running"] = helper_status.running
        status["pid"] = helper_status.pid
        status["version"] = helper_status.version

        # Falls get_status running=False meldet, versuche nochmal mit ping
        if not status["running"] and is_helper_available(_HELPER_SOCK_PATH):
            status["running"] = True

    except Exception:
        # Import-Fehler oder andere Probleme — Helper nicht verfügbar
        pass

    if not status["installed"]:
        if not status["bundle_built"]:
            status["install_instructions"] = (
                "Helper noch nicht gebaut. Terminal öffnen und ausführen: "
                "make -C helper && sudo ./helper/install.sh"
            )
        else:
            status["install_instructions"] = (
                "Helper gebaut aber nicht installiert. Ausführen: "
                "sudo ./helper/install.sh"
            )

    return status


def _render_index(
    collection: dict[str, Any] | None,
    run_report: dict[str, Any] | None,
    deck: dict[str, Any] | None,
    advisor_result: dict[str, Any] | None,
    decks: dict[str, Any] | None,
    llm_config: LLMConfig,
    output_dir: Path | None = None,
) -> str:
    collection_diag = _extract_diagnostics(collection)
    report_diag = _extract_diagnostics(run_report)
    advisor_warnings = list(advisor_result.get("warnings", [])) if advisor_result else []

    return Template("""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>MTGA Advisor – Dashboard</title>
  <style>
    :root {
      --ink: #1f2933;
      --muted: #667085;
      --line: #d8cfc0;
      --paper: #f7f1e7;
      --panel: #fffaf0;
      --accent: #b45309;
      --danger: #b00020;
      --green: #2d6a4f;
    }
    * { box-sizing: border-box; }
    body {
      background:
        radial-gradient(circle at 15% 10%, rgba(180,83,9,.18), transparent 24rem),
        linear-gradient(135deg, #f7f1e7 0%, #eadfcb 100%);
      color: var(--ink);
      font-family: ui-serif, Georgia, "Times New Roman", serif;
      margin: 0;
      line-height: 1.45;
    }
    main { max-width: 1160px; margin: 0 auto; padding: 2rem; }
    h1 { font-size: clamp(2rem, 5vw, 4.2rem); line-height: .95; margin: 1rem 0 .5rem; }
    h2 { margin-bottom: .4rem; }
    h3 { margin: .6rem 0 .2rem; font-size: 1.1rem; }
    h4 { margin: .4rem 0 .1rem; font-size: .95rem; color: var(--muted); }
    .hero { border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; padding-bottom: 1rem; }
    .section { background: rgba(255,250,240,.82); border: 1px solid var(--line); border-radius: 18px; margin-bottom: 1rem; padding: 1rem 1.2rem; box-shadow: 0 14px 40px rgba(60,40,20,.08); }
    .grid { display: grid; gap: .8rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin: 1rem 0; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: .9rem; }
    .card span, .card small, .meta { color: var(--muted); font-size: .92rem; }
    .card strong { display: block; font-size: 1.65rem; color: var(--accent); }
    pre { background: #231f1a; color: #f8ead1; padding: 1rem; overflow: auto; border-radius: 12px; max-height: 26rem; font-size: .85rem; }
    .warning { color: var(--danger); }
    .priority-group, .optimization-group { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: .8rem 1rem; margin-bottom: .6rem; }
    .priority-group ul, .optimization-group ul { margin: .2rem 0; padding-left: 1.2rem; }
    .priority-group li, .optimization-group li { margin: .15rem 0; }
    table { width: 100%; border-collapse: collapse; margin-top: .8rem; }
    th, td { border-bottom: 1px solid var(--line); padding: .55rem; text-align: left; }
    th { color: var(--muted); font-size: .85rem; text-transform: uppercase; letter-spacing: .04em; }
    nav a { color: var(--accent); margin-right: 1rem; }
    .badge { display: inline-block; background: var(--accent); color: #fff; border-radius: 10px; padding: .1rem .5rem; font-size: .8rem; }
    .badge-green { background: var(--green); }
    .btn-select-deck { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .3rem .8rem; cursor: pointer; font-size: .85rem; }
    .btn-select-deck:hover { opacity: .85; }
    .deck-row:hover { background: rgba(180,83,9,.06); cursor: pointer; }
    .deck-row.is-selected { background: rgba(180,83,9,.14); }
    .deck-row.hidden-by-filter { display: none; }
    .deck-type { display: inline-block; padding: .1rem .45rem; border-radius: 999px; font-size: .75rem; }
    .deck-type.precon { background: #fee8d1; color: #8a4b08; }
    .deck-type.normal { background: #e7efe9; color: #26513a; }
    .deck-toolbar { display: flex; justify-content: space-between; gap: 1rem; align-items: center; margin-bottom: .5rem; flex-wrap: wrap; }
    .deck-toolbar label { display: inline-flex; gap: .45rem; align-items: center; cursor: pointer; }
    .deck-details { margin-top: 1rem; border: 1px solid var(--line); border-radius: 16px; background: rgba(247,241,231,.9); padding: 1rem; }
    .deck-details code, .deck-details pre { background: #231f1a; color: #f8ead1; }
    .deck-details pre { max-height: 20rem; }
    .deck-details-grid { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 1rem; }
    .deck-detail-empty { color: var(--muted); padding: .5rem 0; }
    .deck-detail-section { background: rgba(255,250,240,.7); border: 1px solid var(--line); border-radius: 12px; padding: .8rem; }
    .deck-list { margin: 0; padding-left: 1.1rem; }
    .deck-list li { margin: .15rem 0; }

    /* Deck Detail Panel */
    #deck-detail { display: none; }
    #deck-detail.active { display: block; }
    .deck-detail-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: .5rem; }
    .deck-detail-header h2 { margin: 0; }
    .deck-detail-close { background: none; border: 1px solid var(--line); border-radius: 8px; padding: .3rem .8rem; cursor: pointer; font-size: .85rem; color: var(--muted); }
    .deck-cards-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: .5rem; }
    @media (max-width: 700px) { .deck-cards-grid { grid-template-columns: 1fr; } }
    .deck-pile h3 { margin: .2rem 0 .4rem; font-size: 1rem; }
    .history-list { list-style: none; padding: 0; margin: .5rem 0; }
    .history-list li { padding: .2rem 0; font-size: .9rem; color: var(--muted); }
    .history-controls { margin: .5rem 0; }
    #history-diff-result h3 { margin: .5rem 0; font-size: 1rem; }
    #history-diff-result h4 { margin: .4rem 0 .2rem; font-size: .95rem; }
    #history-diff-result h5 { margin: .3rem 0 .1rem; font-size: .85rem; color: var(--muted); }
    #history-diff-result ul { margin: .2rem 0 .4rem; padding-left: 1.2rem; font-size: .85rem; }
    #history-diff-result li { padding: .1rem 0; }
    .deck-pile ul { list-style: none; padding: 0; margin: 0; }
    .deck-pile li { padding: .2rem .4rem; border-bottom: 1px solid var(--line); font-size: .9rem; display: flex; justify-content: space-between; }
    .deck-pile li .qty { color: var(--accent); font-weight: bold; }
    .deck-pile li .card-name { flex: 1; }

    /* Card thumbnails */
    .card-thumb {
      width: 80px;
      height: 112px;
      border-radius: 6px;
      object-fit: cover;
      background: #e8dcc8;
      border: 1px solid var(--line);
      flex-shrink: 0;
    }
    .deck-pile li.with-thumb {
      align-items: center;
      gap: .5rem;
    }
    .deck-pile li .card-info {
      flex: 1;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-thumb-placeholder {
      width: 80px;
      height: 112px;
      border-radius: 6px;
      background: linear-gradient(135deg, #e8dcc8, #d4c4a8);
      border: 1px solid var(--line);
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: .7rem;
      color: var(--muted);
    }

    /* Chat Panel */
    #chat-panel { display: none; position: fixed; bottom: 0; right: 0; width: 480px; max-width: 100vw; height: 600px; max-height: 80vh; background: var(--panel); border: 2px solid var(--accent); border-radius: 16px 0 0 0; box-shadow: -8px -8px 32px rgba(0,0,0,.15); flex-direction: column; z-index: 100; }
    #chat-panel.active { display: flex; }
    #chat-header { padding: .8rem 1rem; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: center; }
    #chat-header h3 { margin: 0; }
    #chat-close { background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--muted); }
    #chat-messages { flex: 1; overflow-y: auto; padding: 1rem; }
    .chat-msg { margin-bottom: .8rem; padding: .6rem .8rem; border-radius: 12px; max-width: 90%; }
    .chat-msg.user { background: var(--accent); color: #fff; margin-left: auto; }
    .chat-msg.assistant { background: #f0e8d8; color: var(--ink); }
    .chat-msg.error { background: #fee; color: var(--danger); border: 1px solid var(--danger); }
    #chat-input-area { padding: .8rem; border-top: 1px solid var(--line); display: flex; gap: .5rem; }
    #chat-input { flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: .5rem; font-family: inherit; }
    #chat-send { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .5rem 1rem; cursor: pointer; }
    #chat-send:disabled { opacity: .5; cursor: default; }
    #chat-loading { font-size: .85rem; color: var(--muted); padding: .4rem; display: none; }

    /* Deck cards in chat panel */
    #chat-deck-cards { max-height: 200px; overflow-y: auto; padding: .5rem 1rem; border-bottom: 1px solid var(--line); font-size: .82rem; }
    #chat-deck-cards:empty { display: none; }
    .chat-deck-pile { margin: .2rem 0; }
    .chat-deck-pile.meta { color: var(--muted); }
    .deck-cards-info { color: var(--muted); margin-bottom: .3rem; }
    .deck-cards-pile { margin-bottom: .4rem; }
    .deck-cards-pile h4 { margin: .2rem 0; font-size: .82rem; color: var(--accent); }
    .deck-cards-pile table { width: 100%; }
    .deck-cards-pile td { padding: .15rem .3rem; border-bottom: 1px solid rgba(216,207,192,.5); }
    .deck-cards-pile td.meta { color: var(--muted); font-size: .8rem; }

    /* Config Panel */
    #config-panel { margin-top: .5rem; padding: .5rem; background: #f0e8d8; border-radius: 8px; }
    #config-panel label { display: block; font-size: .85rem; color: var(--muted); margin-top: .3rem; }
    #config-panel input, #config-panel select { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: .3rem; font-size: .85rem; }
    #config-toggle { font-size: .85rem; color: var(--accent); cursor: pointer; }
    #config-fields { display: none; margin-top: .5rem; }
    #config-fields.open { display: block; }

    /* Helper-Status Section */
    #helper-status-content { margin-top: .8rem; }
    .helper-status-box { display: flex; align-items: center; gap: 1rem; padding: .8rem; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); margin-bottom: .5rem; flex-wrap: wrap; }
    .helper-indicator { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
    .helper-indicator.running { background: var(--green); box-shadow: 0 0 8px rgba(45,106,79,.5); }
    .helper-indicator.installed { background: var(--accent); }
    .helper-indicator.not-installed { background: var(--danger); }
    .helper-indicator.unknown { background: var(--muted); }
    .helper-status-text { flex: 1; min-width: 200px; }
    .helper-status-text strong { font-size: 1.1rem; }
    .helper-status-text .meta { margin: .15rem 0; }
    .helper-btn { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .4rem 1rem; cursor: pointer; font-size: .9rem; flex-shrink: 0; }
    .helper-btn:hover { opacity: .85; }
    .helper-btn:disabled { opacity: .5; cursor: default; }
    .helper-btn.btn-green { background: var(--green); }
    .helper-detail-list { font-size: .85rem; color: var(--muted); margin: .3rem 0; padding-left: 1.2rem; }
    .helper-detail-list li { padding: .1rem 0; }
  </style>
</head>
<body>
<main>
  <div class="hero">
    <p class="meta">Lokaler Advisor · LLM-gestützt · offline-first</p>
    <h1>MTGA Advisor</h1>
    <nav>
      <a href="/api/collection">collection.json</a>
      <a href="/api/decks">decks.json</a>
      <a href="/api/ranks">ranks.json</a>
      <a href="/api/run-report">run-report.json</a>
      <a href="/api/advisor-result">advisor-result.json</a>
      <a href="/api/meta">meta.json</a>
      <a href="/api/helper/status">helper/status</a>
      <span id="config-toggle">⚙ LLM-Config</span>
    </nav>
  </div>

  <div id="config-panel">
    <div id="config-fields">
      <label>Endpoint</label>
      <input id="cfg-endpoint" value="$cfg_endpoint">
      <label>Modell</label>
      <input id="cfg-model" value="$cfg_model">
      <label>Temperature</label>
      <input id="cfg-temperature" type="number" step="0.1" min="0" max="2" value="$cfg_temp">
      <label>Max Tokens</label>
      <input id="cfg-max-tokens" type="number" step="64" min="64" value="$cfg_max_tokens">
      <button id="cfg-save" style="margin-top:.5rem;background:var(--green);color:#fff;border:none;border-radius:8px;padding:.4rem 1rem;cursor:pointer;">Speichern</button>
      <span id="cfg-status" style="margin-left:.5rem;font-size:.85rem;"></span>
    </div>
  </div>

  <div class="section" id="helper-section">
    <h2>Sudo-Helper <span class="badge">T5</span></h2>
    <p class="meta">Memory-Scanner als LaunchDaemon (root) — sudo-freie Scans nach einmaliger Installation.</p>
    <div id="helper-status-content">
      <p class="meta" id="helper-loading">Prüfe Helper-Status...</p>
    </div>
  </div>

  <div class="grid">
    $collection_summary
    $decks_summary
    $llm_advisor_summary
  </div>

  <div class="section" id="ranks-section">
    <h2>Ränge &amp; Account <span class="badge">Phase 1.3</span></h2>
    <p class="meta">Spieler-Rang und Account-Info aus dem MTGA-Prozessspeicher (IL2CPP-Navigation).</p>
    <div id="ranks-content">
      <p class="meta" id="ranks-loading">Lade Rang-Daten...</p>
    </div>
  </div>

  <div class="section">
    <h2>Decks <span class="badge">Phase 1.2</span></h2>
    <div class="deck-toolbar">
      <label><input id="hide-precons" type="checkbox" checked> Precons ausblenden</label>
      <span id="deck-visibility" class="meta">Klick auf eine Zeile für Details, Chat-Button für Advisor.</span>
    </div>
    $decks_table
    <div id="deck-details" class="deck-details">
      <div class="deck-detail-empty" id="deck-detail-empty">Klicke auf ein Deck, um die Detailansicht zu laden.</div>
      <div id="deck-detail-content" style="display:none;">
        <div class="deck-details-grid">
          <div class="deck-detail-section">
            <h3 id="deck-detail-name">Deck</h3>
            <p class="meta" id="deck-detail-meta"></p>
            <div id="deck-detail-badges"></div>
            <h4>Summary</h4>
            <pre id="deck-detail-json"></pre>
          </div>
          <div class="deck-detail-section">
            <h4>Deckliste</h4>
            <div id="deck-detail-cards" class="deck-detail-empty">Noch keine Deckliste geladen.</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  $history_section

  <div class="section" id="meta-section">
    <h2>Meta-Daten <span class="badge badge-green">Phase 2</span></h2>
    <p class="meta">Aktuelle Metagame-Daten von MTGGoldfish. Top-Decks und häufigste Karten.</p>
    <div id="meta-content">
      <p class="meta" id="meta-loading">Lade Meta-Daten...</p>
    </div>
  </div>

  <div class="section">
    <h2>LLM Advisor <span class="badge badge-green">Phase 2</span></h2>
    <div class="warning">
      <h3>Warnings</h3>
      $advisor_warnings
    </div>
    <h3>Crafting Priorities</h3>
    $crafting_priorities
    <h3>Deck Optimizations</h3>
    $deck_optimizations
    <h3>Meta Notes</h3>
    $meta_notes
  </div>

  <div class="section">
    <h2>Collection</h2>
    <p>Completeness: <strong>$collection_completeness</strong></p>
    <div class="warning">
      <h3>Warnings</h3>
      $collection_warnings
    </div>
    <h3>JSON</h3>
    <pre>$collection_json</pre>
  </div>

  <div class="section">
    <h2>Run-Report</h2>
    <p>Completeness: <strong>$report_completeness</strong></p>
    <div class="warning">
      <h3>Warnings</h3>
      $report_warnings
    </div>
    <h3>JSON</h3>
    <pre>$report_json</pre>
  </div>
</main>

<!-- Chat Panel -->
  <div id="chat-panel">
  <div id="chat-header">
    <h3 id="chat-deck-name">Deck</h3>
    <button id="chat-close">×</button>
  </div>
  <div id="chat-deck-cards"></div>
  <div id="chat-messages"></div>
  <div id="chat-loading">LLM denkt...</div>
  <div id="chat-input-area">
    <input id="chat-input" placeholder="Frage zum Deck..." autocomplete="off">
    <button id="chat-send">Senden</button>
  </div>
</div>
<script type="application/json" id="decks-json">$decks_json</script>
<script src="/dashboard.js" defer></script>
</body>
</html>
""").substitute(
        collection_summary=_collection_summary(collection),
        decks_summary=_decks_summary(decks),
        llm_advisor_summary=_llm_advisor_summary(advisor_result),
        decks_table=_render_decks_table(decks),
        decks_json=_json_for_script(decks or {}),
        advisor_warnings=_render_list(advisor_warnings),
        crafting_priorities=_render_crafting_priorities(advisor_result),
        deck_optimizations=_render_deck_optimizations(advisor_result),
        meta_notes=_render_meta_notes(advisor_result),
        history_section=_render_history_section(output_dir) if output_dir else "",
        collection_completeness=html.escape(str(collection_diag["completeness"])),
        collection_warnings=_render_list(collection_diag["warnings"]),
        collection_json=html.escape(_format_json(collection)),
        report_completeness=html.escape(str(report_diag["completeness"])),
        report_warnings=_render_list(report_diag["warnings"]),
        report_json=html.escape(_format_json(run_report)),
        cfg_endpoint=html.escape(llm_config.endpoint),
        cfg_model=html.escape(llm_config.model_name),
        cfg_temp=llm_config.temperature,
        cfg_max_tokens=llm_config.max_tokens,
    )


class MtgaAdvisorHandler(BaseHTTPRequestHandler):
    server_version = "mtga-advisor/0.3"

    def do_GET(self) -> None:
        output_dir = self.server.output_dir

        # /health endpoint — always accessible (even with auth) for health checks
        if self.path == "/health":
            _health_response(self, output_dir)
            return

        # Auth check (skip for /health so monitoring tools can probe)
        if not _check_auth(self):
            _send_auth_required(self)
            return

        if self.path == "/dashboard.js":
            try:
                _js_response(self, _dashboard_js(), status=HTTPStatus.OK)
            except OSError:
                _json_response(
                    self,
                    {"error": "not_found", "message": "dashboard.js fehlt."},
                    status=HTTPStatus.NOT_FOUND,
                )
            return

        if self.path == "/api/collection":
            payload = _read_json(output_dir / "collection.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "collection.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/decks":
            payload = _read_json(output_dir / "decks.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "decks.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/decks-container":
            payload = _read_json(output_dir / "decks-container.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "decks-container.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/ranks":
            payload = _read_json(output_dir / "ranks.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "ranks.json fehlt. Führe 'mtga-export ranks --output out/ranks.json' aus."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        # /api/helper/status — Helper-Daemon Status (running, pid, version, installed)
        if self.path == "/api/helper/status":
            _json_response(self, _helper_status_response(), status=HTTPStatus.OK)
            return

        # /api/card-image/<grp_id> — redirect to Scryfall image URL
        if self.path.startswith("/api/card-image/"):
            grp_id_str = self.path[len("/api/card-image/"):]
            if "?" in grp_id_str:
                grp_id_str = grp_id_str.split("?")[0]
            if not grp_id_str or not grp_id_str.lstrip("-").isdigit():
                _json_response(self, {"error": "bad_request", "message": "Invalid card ID."}, status=HTTPStatus.BAD_REQUEST)
                return
            grp_id = int(grp_id_str)
            image_url = _fetch_scryfall_image_url(grp_id)
            if image_url:
                _send_redirect(self, image_url)
            else:
                _send_image_not_found(self)
            return

        # /api/deck/<deckId> — individual deck file
        if self.path.startswith("/api/deck/"):
            deck_id = self.path[len("/api/deck/"):]
            if not deck_id or not all(c.isalnum() or c in "-_" for c in deck_id):
                _json_response(self, {"error": "bad_request", "message": "Invalid deck ID."}, status=HTTPStatus.BAD_REQUEST)
                return
            deck = _find_deck_payload(output_dir, deck_id)
            if deck is None:
                _json_response(self, {"error": "not_found", "message": f"Deck {deck_id} nicht gefunden."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, deck, status=HTTPStatus.OK)
            return

        if self.path == "/api/run-report":
            payload = _read_json(output_dir / "run-report.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "run-report.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/advisor-result":
            payload = _read_json(output_dir / "advisor-result.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "advisor-result.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/config":
            config = load_config()
            _json_response(self, config.to_dict(), status=HTTPStatus.OK)
            return

        # /api/meta — MTGGoldfish Meta-Daten (cached, optional format query param)
        parsed = urlparse(self.path)
        if parsed.path == "/api/meta":
            qs = parse_qs(parsed.query)
            fmt = qs.get("format", ["standard"])[0]
            no_cache = qs.get("no_cache", ["false"])[0].lower() in ("1", "true", "yes")
            try:
                from advisor.meta import fetch_meta
                meta = fetch_meta(fmt, use_cache=not no_cache)
                _json_response(self, meta.to_dict(), status=HTTPStatus.OK)
            except ValueError as exc:
                _json_response(self, {"error": "bad_request", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except RuntimeError as exc:
                _json_response(self, {"error": "fetch_failed", "message": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- History API ---
        if self.path == "/api/history/snapshots":
            snapshots = list_history_snapshots(output_dir=output_dir)
            _json_response(self, {"snapshots": snapshots, "count": len(snapshots)}, status=HTTPStatus.OK)
            return

        if parsed.path == "/api/history/diff":
            qs = parse_qs(parsed.query)
            older = qs.get("older", [None])[0]
            newer = qs.get("newer", [None])[0]
            diff = diff_history_snapshots(output_dir=output_dir, older=older, newer=newer)
            _json_response(self, diff, status=HTTPStatus.OK if "error" not in diff else HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/deck-cards":
            qs = parse_qs(parsed.query)
            deck_id = qs.get("deck_id", [None])[0]
            if not deck_id:
                _json_response(self, {"error": "deck_id parameter required"}, status=HTTPStatus.BAD_REQUEST)
                return

            decks = _read_json(output_dir / "decks.json")
            if not decks:
                _json_response(self, {"error": "decks.json fehlt"}, status=HTTPStatus.NOT_FOUND)
                return

            deck = None
            for d in decks.get("decks", []):
                if str(d.get("deckId")) == str(deck_id):
                    deck = d
                    break

            if not deck:
                _json_response(self, {"error": f"Deck {deck_id} nicht gefunden"}, status=HTTPStatus.NOT_FOUND)
                return

            cards_data = _extract_deck_cards(deck)
            _json_response(self, cards_data, status=HTTPStatus.OK)
            return

        if self.path == "/":
            collection = _read_json(output_dir / "collection.json")
            run_report = _read_json(output_dir / "run-report.json")
            deck = _read_json(output_dir / "arena_deck.json")
            advisor_result = _read_json(output_dir / "advisor-result.json")
            decks = _read_json(output_dir / "decks.json")
            llm_config = load_config()
            body = _render_index(collection, run_report, deck, advisor_result, decks, llm_config, output_dir=output_dir)
            _html_response(self, body, status=HTTPStatus.OK)
            return

        _json_response(self, {"error": "not_found", "message": "Pfad nicht gefunden."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        output_dir = self.server.output_dir

        # Auth check for POST requests
        if not _check_auth(self):
            _send_auth_required(self)
            return

        if self.path == "/api/chat":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, {"error": "invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
                return

            deck_id = data.get("deck_id")
            question = data.get("question", "").strip()
            if not question:
                _json_response(self, {"error": "Keine Frage"}, status=HTTPStatus.BAD_REQUEST)
                return

            # Deck finden
            decks = _read_json(output_dir / "decks.json")
            if not decks:
                _json_response(self, {"error": "decks.json fehlt"}, status=HTTPStatus.NOT_FOUND)
                return

            deck = None
            for d in decks.get("decks", []):
                if str(deck_id) in _deck_keys_for_payload(d):
                    deck = d
                    break

            if not deck:
                _json_response(self, {"error": f"Deck {deck_id} nicht gefunden"}, status=HTTPStatus.NOT_FOUND)
                return

            # Collection laden
            collection = _read_json(output_dir / "collection.json")

            # Prompt bauen
            prompt = _build_chat_prompt(deck, collection, question)

            # Config laden (mit optionalen CLI-Overrides aus dem Request)
            cli_overrides = {}
            if data.get("endpoint"):
                cli_overrides["endpoint"] = data["endpoint"]
            if data.get("model"):
                cli_overrides["model_name"] = data["model"]
            if data.get("temperature") is not None:
                cli_overrides["temperature"] = float(data["temperature"])
            if data.get("max_tokens") is not None:
                cli_overrides["max_tokens"] = int(data["max_tokens"])

            config = load_config(cli_overrides=cli_overrides)

            # LLM aufrufen
            result = _call_llm_chat(config, prompt)
            _json_response(self, result, status=HTTPStatus.OK)
            return

        if self.path == "/api/config":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, {"error": "invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
                return

            # Config aktualisieren und speichern
            from advisor.llm_config import CONFIG_FILE
            current = load_config()
            if data.get("endpoint"):
                current.endpoint = data["endpoint"]
            if data.get("model_name"):
                current.model_name = data["model_name"]
            if data.get("temperature") is not None:
                current.temperature = float(data["temperature"])
            if data.get("max_tokens") is not None:
                current.max_tokens = int(data["max_tokens"])
            if data.get("system_prompt"):
                current.system_prompt = data["system_prompt"]
            if data.get("timeout"):
                current.timeout = int(data["timeout"])

            # Speichern
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = current.to_dict()
            payload["_comment"] = "MTGA Advisor LLM Config (via Dashboard gespeichert)"
            CONFIG_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            _json_response(self, {"status": "saved", "config": current.to_dict()}, status=HTTPStatus.OK)
            return

        if self.path == "/api/history/save":
            history_dir = save_history_snapshot(output_dir=output_dir)
            snapshots = list_history_snapshots(output_dir=output_dir)
            _json_response(self, {
                "status": "saved",
                "historyDir": str(history_dir),
                "snapshotCount": len(snapshots),
                "latest": snapshots[-1] if snapshots else None,
            }, status=HTTPStatus.OK)
            return

        _json_response(self, {"error": "not_found", "message": "Pfad nicht gefunden."}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return


class MtgaAdvisorServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], output_dir: Path):
        super().__init__(server_address, handler)
        self.output_dir = output_dir


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    auth: str | None = None,
    show_qr: bool = True,
) -> None:
    """Start the MTGA Advisor dashboard server.

    Args:
        host: Bind address (default 0.0.0.0 for LAN/Tailscale access).
        port: Port number (default 8000).
        output_dir: Directory containing collection.json, decks.json, etc.
        auth: Optional ``user:password`` for Basic-Auth. If None, checks
              the ``MTGA_ADVISOR_AUTH`` environment variable.
        show_qr: If True, print a QR code in the terminal for mobile access.
    """
    # Resolve auth from env var if not explicitly provided
    if auth is None:
        auth = os.environ.get("MTGA_ADVISOR_AUTH")
    configure_basic_auth(auth)

    lan_ip = _get_lan_ip()
    _print_server_banner(host, port, lan_ip, _AUTH_ENABLED, show_qr)

    server = MtgaAdvisorServer((host, port), MtgaAdvisorHandler, output_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server wird heruntergefahren ...")
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MTGA Advisor Dashboard Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--output", type=Path, default=None, help="Output directory with JSON artifacts")
    parser.add_argument("--auth", default=None, help="Basic-Auth credentials as user:password")
    parser.add_argument("--no-qr", action="store_true", help="Disable QR code display in terminal")
    args = parser.parse_args()

    output_env = os.environ.get("MTGA_ADVISOR_OUTPUT_DIR")
    resolved_output = Path(args.output or output_env or DEFAULT_OUTPUT_DIR).expanduser().resolve()

    run_server(
        host=args.host,
        port=args.port,
        output_dir=resolved_output,
        auth=args.auth,
        show_qr=not args.no_qr,
    )
