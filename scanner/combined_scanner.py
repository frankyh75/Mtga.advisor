"""Kombinierter Scanner: Collection + Decks + Ranks in einem Durchlauf.

Wenn der Sudo-Helper-Daemon verfügbar ist, öffnet dieser Modul eine
einzige persistente Socket-Verbindung zum Helper und führt alle drei
Scans (Collection, Deck-Scan, Rank-Scan) darüber aus — ohne sudo-Prompt
und ohne den Overhead dreier separater Verbindungen.

Wenn der Helper nicht verfügbar ist, fällt der kombinierte Scan auf den
direkten (sudo-)Weg zurück: Pymem für Collection/Decks, PymemMemoryAdapter
für IL2CPP-Navigation (Ranks).

Die Hauptfunktion ist scan_all().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .helper_client import (
    DEFAULT_SOCK_PATH,
    PersistentHelperBackend,
    PersistentRemoteMemoryAdapter,
    is_helper_available,
    open_helper_connection,
)
from .memory_scanner import MemoryScanResult

logger = logging.getLogger(__name__)


@dataclass
class CombinedScanResult:
    """Ergebnis eines kombinierten Scans (Collection + Decks + Ranks)."""
    collection: MemoryScanResult | None = None
    decks: list[dict[str, Any]] = field(default_factory=list)
    deck_warnings: list[str] = field(default_factory=list)
    ranks: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    rank_warnings: list[str] = field(default_factory=list)
    used_helper: bool = False
    errors: list[str] = field(default_factory=list)


def scan_all(
    *,
    sock_path: str = DEFAULT_SOCK_PATH,
    use_helper: bool | None = None,
    debug: bool = False,
    print_fn: Callable[..., None] = print,
    scan_collection: bool = True,
    scan_decks: bool = True,
    scan_ranks: bool = True,
    read_account: bool = True,
    non_interactive: bool = False,
) -> CombinedScanResult:
    """Führt Collection-, Deck- und Rank-Scan in einem Durchlauf aus.

    Wenn der Helper-Daemon verfügbar ist (use_helper=True oder None mit
    Auto-Detect), wird eine persistente Socket-Verbindung geöffnet und
    für alle drei Scans wiederverwendet.

    Args:
        sock_path:    Pfad zum Helper-UNIX-Socket.
        use_helper:   True → Helper erzwingen; False → direkter Scan;
                      None → auto-detect.
        debug:        Debug-Output aktivieren.
        print_fn:     Print-Funktion für Statusmeldungen.
        scan_collection: Collection-Scan ausführen.
        scan_decks:   Deck-Scan ausführen.
        scan_ranks:   Rank-Scan ausführen.
        read_account: Account-Info im Rank-Scan mitlesen.

    Returns:
        CombinedScanResult mit allen Ergebnissen.
    """
    result = CombinedScanResult()

    # --- Helper-Verfügbarkeit prüfen ---
    if use_helper is None:
        use_helper = is_helper_available(sock_path)

    if use_helper:
        return _scan_all_via_helper(
            sock_path=sock_path,
            debug=debug,
            print_fn=print_fn,
            scan_collection=scan_collection,
            scan_decks=scan_decks,
            scan_ranks=scan_ranks,
            read_account=read_account,
            non_interactive=non_interactive,
            result=result,
        )

    # --- Direkter Scan (sudo erforderlich) ---
    return _scan_all_direct(
        debug=debug,
        print_fn=print_fn,
        scan_collection=scan_collection,
        scan_decks=scan_decks,
        scan_ranks=scan_ranks,
        read_account=read_account,
        result=result,
    )


def _scan_all_via_helper(
    *,
    sock_path: str,
    debug: bool,
    print_fn: Callable[..., None],
    scan_collection: bool,
    scan_decks: bool,
    scan_ranks: bool,
    read_account: bool,
    non_interactive: bool,
    result: CombinedScanResult,
) -> CombinedScanResult:
    """Kombinierter Scan über eine persistente Helper-Verbindung."""
    result.used_helper = True
    print_fn("🔄 Kombinierter Scan über Helper-Daemon (sudo-frei)...")

    try:
        conn = open_helper_connection(sock_path)
    except Exception as exc:
        result.errors.append(f"Helper-Verbindung fehlgeschlagen: {exc}")
        print_fn(f"❌ Helper-Verbindung fehlgeschlagen: {exc}")
        return result

    try:
        # --- Collection-Scan ---
        if scan_collection:
            print_fn("\n📦 Collection-Scan...")
            backend = PersistentHelperBackend(conn)
            from .memory_scanner import scan_collection_detailed

            coll_result = scan_collection_detailed(
                use_helper=True,
                sock_path=sock_path,
                backend=backend,
                debug=debug,
                print_fn=print_fn,
                non_interactive=non_interactive,
            )
            result.collection = coll_result
            if coll_result is None:
                result.errors.append("Collection-Scan: keine Daten")

        # --- Deck-Scan ---
        if scan_decks:
            print_fn("\n🃏 Deck-Scan (IL2CPP-Navigation)...")
            from .il2cpp_nav import scan_decks_il2cpp
            from dataclasses import asdict

            adapter = PersistentRemoteMemoryAdapter(conn)
            il2cpp_result = scan_decks_il2cpp(adapter, debug=debug)
            if il2cpp_result and il2cpp_result.decks:
                result.decks = [asdict(d) for d in il2cpp_result.decks]
                print_fn(f"✅ {len(result.decks)} Decks gefunden")
            elif il2cpp_result:
                result.deck_warnings = il2cpp_result.warnings
                print_fn(f"⚠ keine Decks — {', '.join(il2cpp_result.warnings) or 'unbekannt'}")
            else:
                result.deck_warnings.append("IL2CPP Deck-Scan fehlgeschlagen")
                print_fn("⚠ Deck-Scan fehlgeschlagen")

        # --- Rank-Scan ---
        if scan_ranks:
            print_fn("\n🏆 Rank-Scan (IL2CPP-Navigation)...")
            from .rank_scanner import scan_ranks_and_account

            adapter = PersistentRemoteMemoryAdapter(conn)
            rank_result = scan_ranks_and_account(adapter, read_account=read_account)
            if rank_result.warnings:
                result.rank_warnings = rank_result.warnings
            result.ranks = rank_result.to_dict().get("ranks")
            if read_account:
                result.account = rank_result.to_dict().get("account")
            if result.ranks:
                print_fn("✅ Ränge gefunden")
            else:
                result.errors.append("Rank-Scan: keine Daten")
                print_fn("⚠ keine Ränge gefunden")

    finally:
        conn.close()

    return result


def _scan_all_direct(
    *,
    debug: bool,
    print_fn: Callable[..., None],
    scan_collection: bool,
    scan_decks: bool,
    scan_ranks: bool,
    read_account: bool,
    result: CombinedScanResult,
) -> CombinedScanResult:
    """Kombinierter Scan über direkten Pymem-Zugriff (erfordert sudo)."""
    print_fn("⚠ Kombinierter direkter Scan (erfordert sudo)...")

    from .memory_scanner import _attach_process, scan_collection_detailed
    from .macos_paths import get_macos_mtga_process_names

    candidate_names = get_macos_mtga_process_names()
    pm = _attach_process(candidate_names, print_fn=print_fn)
    if pm is None:
        result.errors.append("MTGA-Prozess nicht gefunden")
        print_fn("❌ MTGA-Prozess nicht gefunden")
        return result

    # --- Collection-Scan ---
    if scan_collection:
        print_fn("\n📦 Collection-Scan...")
        coll_result = scan_collection_detailed(
            use_helper=False,
            debug=debug,
            print_fn=print_fn,
        )
        result.collection = coll_result
        if coll_result is None:
            result.errors.append("Collection-Scan: keine Daten")

    # --- Deck-Scan ---
    if scan_decks:
        print_fn("\n🃏 Deck-Scan (IL2CPP-Navigation)...")
        from .il2cpp_nav import scan_decks_il2cpp, PymemMemoryAdapter
        from dataclasses import asdict

        adapter = PymemMemoryAdapter(pm)
        il2cpp_result = scan_decks_il2cpp(adapter, debug=debug)
        if il2cpp_result and il2cpp_result.decks:
            result.decks = [asdict(d) for d in il2cpp_result.decks]
            print_fn(f"✅ {len(result.decks)} Decks gefunden")
        elif il2cpp_result:
            result.deck_warnings = il2cpp_result.warnings
            print_fn(f"⚠ keine Decks — {', '.join(il2cpp_result.warnings) or 'unbekannt'}")
        else:
            result.deck_warnings.append("IL2CPP Deck-Scan fehlgeschlagen")
            print_fn("⚠ Deck-Scan fehlgeschlagen")

    # --- Rank-Scan ---
    if scan_ranks:
        print_fn("\n🏆 Rank-Scan (IL2CPP-Navigation)...")
        from .rank_scanner import scan_ranks_and_account

        adapter = PymemMemoryAdapter(pm)
        rank_result = scan_ranks_and_account(adapter, read_account=read_account)
        if rank_result.warnings:
            result.rank_warnings = rank_result.warnings
        result.ranks = rank_result.to_dict().get("ranks")
        if read_account:
            result.account = rank_result.to_dict().get("account")
        if result.ranks:
            print_fn("✅ Ränge gefunden")
        else:
            result.errors.append("Rank-Scan: keine Daten")
            print_fn("⚠ keine Ränge gefunden")

    return result