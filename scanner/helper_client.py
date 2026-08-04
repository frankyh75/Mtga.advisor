"""Helper-Client: Kommuniziert mit dem Sudo-Helper-Daemon über UNIX-Socket.

Der Helper-Daemon (als LaunchDaemon unter root) lauscht auf einem UNIX-Socket
und führt Memory-Scans im root-Kontext aus. Dieses Modul kapselt die
Socket-Kommunikation und das JSON-Protokoll.

Protokoll (Low-Level):
  {"action": "ping"}                              → {"status": "ok"}
  {"action": "status"}                            → {"status": "ok", "pid": N, "version": "...", ...}
  {"action": "list_regions"}                      → {"status": "ok", "regions": [{"address": ..., "size": ...}, ...]}
  {"action": "read_memory", "address": N, "size": N} → {"status": "ok", "data": "<base64>", "bytes_read": N}
  {"action": "shutdown"}                         → {"status": "ok"}

  Response: {"status": "ok", ...} | {"error": "..."}
"""

from __future__ import annotations

import json
import os
import socket
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .memory_scanner import MemoryScanResult

# Standard-Socket-Pfad (mit helper/tests/test_e2e.sh kompatibel)
DEFAULT_SOCK_PATH = "/var/run/mtga-helper.sock"

# Timeout für Socket-Verbindung (Sekunden)
CONNECT_TIMEOUT = 3
RECV_TIMEOUT = 30
RECV_BUF = 65536


@dataclass(frozen=True)
class HelperStatus:
    """Status des Helper-Daemons."""
    running: bool
    socket_path: str
    pid: int | None = None
    version: str | None = None


def _send_request(request: dict[str, Any], sock_path: str = DEFAULT_SOCK_PATH, *,
                  timeout: float = RECV_TIMEOUT) -> dict[str, Any]:
    """Sendet ein JSON-Request an den Helper und liefert die Response.

    Raises:
        HelperConnectionError: Wenn der Helper nicht erreichbar ist.
        HelperProtocolError: Wenn die Response ungültig ist.
    """
    if not os.path.exists(sock_path):
        raise HelperConnectionError(f"Helper-Socket nicht gefunden: {sock_path}")

    request_bytes = (json.dumps(request) + "\n").encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(CONNECT_TIMEOUT)
            s.connect(sock_path)
            s.settimeout(timeout)
            s.sendall(request_bytes)

            # Response lesen (kann in mehreren Chunks kommen)
            chunks: list[bytes] = []
            while True:
                try:
                    data = s.recv(RECV_BUF)
                    if not data:
                        break
                    chunks.append(data)
                    # Wenn wir weniger als den Buffer erhalten haben, sind wir wahrscheinlich fertig
                    if len(data) < RECV_BUF:
                        break
                except socket.timeout:
                    break

            if not chunks:
                raise HelperProtocolError("Leere Response vom Helper")

            raw = b"".join(chunks).strip()
            return json.loads(raw.decode("utf-8"))
    except HelperConnectionError:
        raise
    except HelperProtocolError:
        raise
    except (OSError, ConnectionError) as exc:
        raise HelperConnectionError(f"Verbindung zu Helper fehlgeschlagen: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HelperProtocolError(f"Ungültige JSON-Response: {exc}") from exc


def ping(sock_path: str = DEFAULT_SOCK_PATH) -> bool:
    """Sendet ein Ping an den Helper. True, wenn Helper antwortet."""
    try:
        resp = _send_request({"action": "ping"}, sock_path, timeout=CONNECT_TIMEOUT)
        return resp.get("status") == "ok"
    except (HelperConnectionError, HelperProtocolError):
        return False


def get_status(sock_path: str = DEFAULT_SOCK_PATH) -> HelperStatus:
    """Fragt den Helper-Status ab. Wirft nicht — liefert running=False bei Fehler."""
    try:
        resp = _send_request({"action": "status"}, sock_path, timeout=CONNECT_TIMEOUT)
        if resp.get("status") == "ok":
            return HelperStatus(
                running=True,
                socket_path=sock_path,
                pid=resp.get("pid"),
                version=resp.get("version"),
            )
    except (HelperConnectionError, HelperProtocolError):
        pass
    return HelperStatus(running=False, socket_path=sock_path)


def is_helper_available(sock_path: str = DEFAULT_SOCK_PATH) -> bool:
    """Prüft, ob der Helper-Daemon läuft und erreichbar ist."""
    return ping(sock_path)


# --- Low-Level Primitives (neues Protokoll) ---

def request_list_regions(sock_path: str = DEFAULT_SOCK_PATH, *,
                         timeout: float = 60) -> list[dict[str, int]]:
    """Listet beschreibbare Memory-Regionen des MTGA-Prozesses auf.

    Returns:
        Liste von {"address": int, "size": int} Dicts.
    Raises:
        HelperConnectionError, HelperProtocolError, HelperError.
    """
    resp = _send_request({"action": "list_regions"}, sock_path, timeout=timeout)
    if "error" in resp:
        raise HelperError(resp["error"])
    regions = resp.get("regions")
    if not isinstance(regions, list):
        raise HelperProtocolError(f"list_regions-Response fehlt 'regions'-Liste: {resp}")
    return regions


def request_read_memory(sock_path: str = DEFAULT_SOCK_PATH, *,
                        address: int,
                        size: int,
                        timeout: float = 60) -> bytes:
    """Liest rohen Memory-Inhalt an einer Adresse.

    Args:
        address: Speicheradresse (virtual address im MTGA-Prozess).
        size: Anzahl Bytes (max 16 MB).

    Returns:
        Rohe Bytes (base64-decoded).
    Raises:
        HelperConnectionError, HelperProtocolError, HelperError.
    """
    if address < 0:
        raise HelperProtocolError(f"Ungültige Adresse: {address}")
    if size <= 0:
        raise HelperProtocolError(f"Ungültige Größe: {size}")

    resp = _send_request(
        {"action": "read_memory", "address": address, "size": size},
        sock_path, timeout=timeout,
    )
    if "error" in resp:
        raise HelperError(resp["error"])
    data_b64 = resp.get("data")
    if data_b64 is None:
        raise HelperProtocolError(f"read_memory-Response fehlt 'data'-Feld: {resp}")
    return base64.b64decode(data_b64)


def request_scan(sock_path: str = DEFAULT_SOCK_PATH, *,
                 process_names: Sequence[str] | None = None,
                 anchors: Sequence[tuple[int, int, str]] | None = None,
                 debug: bool = False) -> dict[str, Any]:
    """Fordert einen Memory-Scan vom Helper an.

    Args:
        sock_path: Pfad zum UNIX-Socket.
        process_names: Liste der zu scannenden Prozessnamen (z.B. ["MTGA"]).
        anchors: Liste von (grpId, quantity, name) Tuples für Anker-basierten Scan.
        debug: Wenn True, gibt der Helper Debug-Statistiken zurück.

    Returns:
        Response-Dict mit "cards", "anchor_matches", "validation", etc.

    Raises:
        HelperConnectionError: Helper nicht erreichbar.
        HelperProtocolError: Ungültige Response.
        HelperError: Helper meldet Fehler bei der Ausführung.
    """
    request: dict[str, Any] = {
        "action": "scan",
        "debug": debug,
    }
    if process_names:
        request["process_names"] = list(process_names)
    if anchors:
        request["anchors"] = [
            {"grpId": aid, "quantity": aqty, "name": aname}
            for aid, aqty, aname in anchors
        ]

    resp = _send_request(request, sock_path, timeout=60)
    if "error" in resp:
        raise HelperError(resp["error"])
    if "cards" not in resp:
        raise HelperProtocolError(f"Scan-Response fehlt 'cards'-Feld: {resp}")
    return resp


def request_deck_scan(sock_path: str = DEFAULT_SOCK_PATH, *,
                      process_names: Sequence[str] | None = None,
                      method: str = "auto",
                      anchors: Sequence[int] | None = None,
                      debug: bool = False) -> dict[str, Any]:
    """Fordert einen Deck-Scan vom Helper an.

    Returns:
        Response-Dict mit "decks"-Liste.
    """
    request: dict[str, Any] = {
        "action": "deck_scan",
        "method": method,
        "debug": debug,
    }
    if process_names:
        request["process_names"] = list(process_names)
    if anchors:
        request["anchor_ids"] = list(anchors)

    resp = _send_request(request, sock_path, timeout=60)
    if "error" in resp:
        raise HelperError(resp["error"])
    return resp


def request_rank_scan(sock_path: str = DEFAULT_SOCK_PATH, *,
                      process_names: Sequence[str] | None = None,
                      include_account: bool = True) -> dict[str, Any]:
    """Fordert einen Rank/Account-Scan vom Helper an.

    Returns:
        Response-Dict mit "ranks" und optional "account".
    """
    request: dict[str, Any] = {
        "action": "rank_scan",
        "include_account": include_account,
    }
    if process_names:
        request["process_names"] = list(process_names)

    resp = _send_request(request, sock_path, timeout=60)
    if "error" in resp:
        raise HelperError(resp["error"])
    return resp


# --- Helper-based Scan Functions (Drop-in für memory_scanner) ---

def helper_scan_collection(
    sock_path: str = DEFAULT_SOCK_PATH,
    *,
    process_names: Sequence[str] | None = None,
    anchors: Sequence[tuple[int, int, str]] | None = None,
    debug: bool = False,
    print_fn: Callable[..., None] = print,
) -> dict[int, int] | None:
    """Scannt die Collection über den Helper-Daemon.

    Dies ist der sudo-freie Weg: Der Helper läuft als root und führt
    task_for_pid() + Memory-Read aus.

    Returns:
        Dictionary {grpId: quantity} oder None bei Fehler.
    """
    if not is_helper_available(sock_path):
        print_fn("⚠ Helper-Daemon nicht erreichbar.")
        return None

    try:
        resp = request_scan(
            sock_path,
            process_names=process_names,
            anchors=anchors,
            debug=debug,
        )
    except HelperError as exc:
        print_fn(f"❌ Helper-Scan fehlgeschlagen: {exc}")
        return None
    except (HelperConnectionError, HelperProtocolError) as exc:
        print_fn(f"❌ Helper-Kommunikation fehlgeschlagen: {exc}")
        return None

    cards_raw = resp.get("cards", {})
    # JSON-Keys sind Strings → int konvertieren
    cards: dict[int, int] = {}
    for k, v in cards_raw.items():
        try:
            cards[int(k)] = int(v)
        except (ValueError, TypeError):
            continue

    return cards if cards else None


def helper_scan_collection_detailed(
    sock_path: str = DEFAULT_SOCK_PATH,
    *,
    process_names: Sequence[str] | None = None,
    anchors: Sequence[tuple[int, int, str]] | None = None,
    debug: bool = False,
    print_fn: Callable[..., None] = print,
) -> MemoryScanResult | None:
    """Scannt die Collection über den Helper und liefert ein MemoryScanResult.

    Returns:
        MemoryScanResult oder None bei Fehler.
    """
    from .memory_scanner import validate_collection
    from .card_database import load_card_database

    if not is_helper_available(sock_path):
        print_fn("⚠ Helper-Daemon nicht erreichbar.")
        return None

    print_fn("🔄 Scan über Helper-Daemon (sudo-frei)...")
    try:
        resp = request_scan(
            sock_path,
            process_names=process_names,
            anchors=anchors,
            debug=debug,
        )
    except HelperError as exc:
        print_fn(f"❌ Helper-Scan fehlgeschlagen: {exc}")
        return None
    except (HelperConnectionError, HelperProtocolError) as exc:
        print_fn(f"❌ Helper-Kommunikation fehlgeschlagen: {exc}")
        return None

    cards_raw = resp.get("cards", {})
    collection: dict[int, int] = {}
    for k, v in cards_raw.items():
        try:
            collection[int(k)] = int(v)
        except (ValueError, TypeError):
            continue

    if not collection:
        print_fn("❌ Helper lieferte keine Karten.")
        return None

    # Validierung mit Karten-DB
    db = load_card_database()
    validation = validate_collection(collection, db=db, anchors=list(anchors) if anchors else None)

    anchor_matches_raw = resp.get("anchor_matches", {})
    anchor_matches: dict[int, int] = {}
    for k, v in anchor_matches_raw.items():
        try:
            anchor_matches[int(k)] = int(v)
        except (ValueError, TypeError):
            continue

    scan_stats = None
    if debug and "scan_stats" in resp:
        stats_raw = resp["scan_stats"]
        from .pattern_scanner import ScanStats
        try:
            scan_stats = ScanStats(
                regions=stats_raw.get("regions", 0),
                bytes_scanned=stats_raw.get("bytes_scanned", 0),
                read_failures=stats_raw.get("read_failures", 0),
                matches=stats_raw.get("matches", 0),
                region_error=stats_raw.get("region_error", ""),
            )
        except (KeyError, TypeError):
            pass

    print_fn(f"✅ Helper-Scan: {len(collection)} unique Karten gefunden")
    return MemoryScanResult(
        collection=collection,
        anchors=list(anchors) if anchors else [],
        anchor_matches=anchor_matches,
        validation=validation,
        scan_stats=scan_stats,
    )


def helper_scan_decks(
    sock_path: str = DEFAULT_SOCK_PATH,
    *,
    process_names: Sequence[str] | None = None,
    method: str = "auto",
    anchor_ids: Sequence[int] | None = None,
    debug: bool = False,
    print_fn: Callable[..., None] = print,
) -> list[dict[str, Any]] | None:
    """Scannt Decks über den Helper-Daemon.

    Returns:
        Liste von Deck-Dicts oder None bei Fehler.
    """
    if not is_helper_available(sock_path):
        print_fn("⚠ Helper-Daemon nicht erreichbar.")
        return None

    try:
        resp = request_deck_scan(
            sock_path,
            process_names=process_names,
            method=method,
            anchors=anchor_ids,
            debug=debug,
        )
    except HelperError as exc:
        print_fn(f"❌ Helper Deck-Scan fehlgeschlagen: {exc}")
        return None
    except (HelperConnectionError, HelperProtocolError) as exc:
        print_fn(f"❌ Helper-Kommunikation fehlgeschlagen: {exc}")
        return None

    decks = resp.get("decks", [])
    return decks if decks else None


# --- Exceptions ---

class HelperError(Exception):
    """Fehler vom Helper-Daemon gemeldet (z.B. Scan fehlgeschlagen)."""


class HelperConnectionError(Exception):
    """Verbindung zum Helper-Daemon fehlgeschlagen."""


class HelperProtocolError(Exception):
    """Ungültige Protokoll-Antwort vom Helper."""