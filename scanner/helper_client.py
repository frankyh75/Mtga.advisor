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
import struct
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


# --- Low-Level Primitives ---

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


# --- HelperBackend (MemoryBackend-kompatibel) ---

class HelperBackend:
    """MemoryBackend-Implementierung über den Helper-Daemon.

    Implementiert das MemoryBackend-Protocol aus pattern_scanner.py,
    sodass scan_process_memory_many_with_stats() und find_blocks()
    ohne Änderungen über den Helper funktionieren.
    """

    def __init__(self, sock_path: str = DEFAULT_SOCK_PATH) -> None:
        self._sock_path = sock_path

    def read_bytes(self, addr: int, size: int) -> bytes | None:
        try:
            return request_read_memory(self._sock_path, address=addr, size=size)
        except (HelperConnectionError, HelperProtocolError, HelperError):
            return None

    def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
        regions = request_list_regions(self._sock_path)
        return [(r["address"], r["size"]) for r in regions]

    def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
        regions = request_list_regions(self._sock_path)
        return ([(r["address"], r["size"]) for r in regions], None)


# --- RemoteMemoryAdapter (IL2CPP-kompatibel) ---

class RemoteMemoryAdapter:
    """Adapter, der über den Helper-Daemon auf MTGA-Speicher zugreift.

    Implementiert dieselbe Schnittstelle wie PymemMemoryAdapter in
    scanner/il2cpp_nav.py, sodass scan_decks_il2cpp() und
    scan_ranks_and_account() ohne Änderungen funktionieren.
    """

    def __init__(self, sock_path: str = DEFAULT_SOCK_PATH) -> None:
        self._sock_path = sock_path
        self._pid: int | None = None

    @property
    def pid(self) -> int:
        if self._pid is None:
            status = get_status(self._sock_path)
            self._pid = status.pid or 0
        return self._pid

    def read_bytes(self, addr: int, size: int) -> bytes:
        try:
            return request_read_memory(self._sock_path, address=addr, size=size)
        except (HelperConnectionError, HelperProtocolError, HelperError):
            return b"\x00" * size

    def read_ptr(self, addr: int) -> int:
        raw = self.read_bytes(addr, 8)
        return struct.unpack_from("<Q", raw)[0]

    def read_u32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<I", raw)[0]

    def read_i32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<i", raw)[0]

    def read_string(self, addr: int) -> str:
        if addr == 0:
            return ""
        raw = self.read_bytes(addr, 256)
        end = raw.find(b"\x00")
        if end == -1:
            end = len(raw)
        return raw[:end].decode("ascii", errors="replace")


# --- Helper-based Scan Functions (Drop-in für memory_scanner) ---

def helper_scan_decks(
    sock_path: str = DEFAULT_SOCK_PATH,
    *,
    method: str = "auto",
    anchor_ids: Sequence[int] | None = None,
    debug: bool = False,
    print_fn: Callable[..., None] = print,
) -> list[dict[str, Any]] | None:
    """Scannt Decks über den Helper-Daemon (via IL2CPP-Navigation).

    Nutzt RemoteMemoryAdapter + scan_decks_il2cpp() aus scanner.il2cpp_nav.

    Returns:
        Liste von Deck-Dicts oder None bei Fehler.
    """
    if not is_helper_available(sock_path):
        print_fn("⚠ Helper-Daemon nicht erreichbar.")
        return None

    try:
        from .il2cpp_nav import scan_decks_il2cpp
        adapter = RemoteMemoryAdapter(sock_path)
        decks = scan_decks_il2cpp(adapter)
        if not decks:
            print_fn("❌ Helper Deck-Scan: keine Decks gefunden.")
            return None
        return decks
    except Exception as exc:
        print_fn(f"❌ Helper Deck-Scan fehlgeschlagen: {exc}")
        return None


def helper_scan_ranks(
    sock_path: str = DEFAULT_SOCK_PATH,
    *,
    include_account: bool = True,
    print_fn: Callable[..., None] = print,
) -> dict[str, Any] | None:
    """Scannt Ranks/Account über den Helper-Daemon (via IL2CPP-Navigation).

    Nutzt RemoteMemoryAdapter + scan_ranks_and_account() aus scanner.il2cpp_nav.

    Returns:
        Dict mit "ranks" und optional "account", oder None bei Fehler.
    """
    if not is_helper_available(sock_path):
        print_fn("⚠ Helper-Daemon nicht erreichbar.")
        return None

    try:
        from .rank_scanner import scan_ranks_and_account
        adapter = RemoteMemoryAdapter(sock_path)
        result = scan_ranks_and_account(adapter, read_account=include_account)
        if not result:
            print_fn("❌ Helper Rank-Scan: keine Daten gefunden.")
            return None
        return result
    except Exception as exc:
        print_fn(f"❌ Helper Rank-Scan fehlgeschlagen: {exc}")
        return None


# --- Exceptions ---

class HelperError(Exception):
    """Fehler vom Helper-Daemon gemeldet (z.B. Scan fehlgeschlagen)."""


class HelperConnectionError(Exception):
    """Verbindung zum Helper-Daemon fehlgeschlagen."""


class HelperProtocolError(Exception):
    """Ungültige Protokoll-Antwort vom Helper."""