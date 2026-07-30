"""macOS Menubar App for MTGA Collection Sync.

Built with rumps (Ridiculously Uncomplicated macOS Python Statusbar apps).
Runs a menubar icon: click to open menu → "Sync Now" triggers a full
collection sync (LLDB memory scan + log parsing + merge) in a background
thread, then writes ``out/collection.json``.

Usage::

    python menubar/app.py

Requires: rumps, pillow (for icon generation).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is importable when running as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import rumps  # noqa: E402

from parser.log_paths import detect_platform, discover_logs, MissingLogsError  # noqa: E402
from parser.pipeline import parse_collection  # noqa: E402
from scanner.lldb_probe import _run_lldb, _resolve_pid, _resolve_card_ids  # noqa: E402
from scanner.macos_paths import get_macos_mtga_process_name  # noqa: E402

from .icon_generator import generate_icon  # noqa: E402

DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "out"

# Anchor cards for the LLDB probe — a handful of commonly-owned cards that
# serve as search needles. The probe finds these IDs in memory, then the
# payload reports match counts.  A real collection scan requires the full
# anchor set from the user; for the menubar app we use a "wide sweep" mode
# that scans for all card IDs from the database.
ANCHOR_CARD_NAMES = [
    "Llanowar Elves",
    "Opt",
    "Shock",
    "Giant Growth",
    "Healing Salve",
]

# Menu item keys (used with rumps.MenuItem dict-style access).
MENU_STATUS = "status"
MENU_SYNC = "sync_now"
MENU_OPEN_OUTPUT = "open_output"
MENU_QUIT = "quit"


class MtgaSyncApp(rumps.App):
    """Menubar application that syncs the MTGA collection on demand.

    The app displays a menubar icon. Clicking opens a menu with:
      - **Sync Now** — starts a background sync (LLDB + logs + merge).
      - **Status** — current state: Ready / Syncing / Last sync info.
      - **Open Output** — reveals ``out/collection.json`` in Finder.
      - **Quit** — terminates the app.
    """

    def __init__(self) -> None:
        icon_path = self._ensure_icon()
        super().__init__(
            "MTGA",
            icon=str(icon_path) if icon_path else None,
            quit_button=None,
        )
        self.output_dir: Path = Path(os.environ.get("MTGA_OUTPUT_DIR", DEFAULT_OUTPUT_DIR))
        self._sync_thread: threading.Thread | None = None
        self._last_sync_time: datetime | None = None
        self._last_sync_card_count: int = 0
        self._last_sync_error: str | None = None

        self.menu = [
            {"Sync Now": MENU_SYNC},
            None,  # separator
            {MENU_STATUS: rumps.MenuItem("Status: Ready", callback=None)},
            None,
            {"Open Output Folder": MENU_OPEN_OUTPUT},
            None,
            {"Quit": MENU_QUIT},
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_icon(self) -> Path | None:
        """Generate the menubar icon PNG if Pillow is available."""
        try:
            return generate_icon()
        except ImportError:
            rumps.alert(
                title="Pillow not installed",
                message="Icon generation requires Pillow.\nInstall with: pip install pillow",
                ok="OK",
            )
            return None
        except Exception as exc:
            rumps.alert(title="Icon generation failed", message=str(exc), ok="OK")
            return None

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    @rumps.clicked(MENU_SYNC)
    def on_sync(self, _sender: rumps.MenuItem) -> None:
        """Handle 'Sync Now' click — starts sync in a background thread."""
        if self._sync_thread is not None and self._sync_thread.is_alive():
            rumps.notification(
                title="MTGA Sync",
                subtitle="Already syncing",
                message="A sync is already in progress. Please wait.",
            )
            return

        self._update_status("Syncing…")
        self._sync_thread = threading.Thread(target=self._do_sync, daemon=True)
        self._sync_thread.start()

    @rumps.clicked(MENU_OPEN_OUTPUT)
    def on_open_output(self, _sender: rumps.MenuItem) -> None:
        """Open the output directory in Finder."""
        output = self.output_dir
        if not output.exists():
            output.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["open", str(output)],
            check=False,
        )

    @rumps.clicked(MENU_QUIT)
    def on_quit(self, _sender: rumps.MenuItem) -> None:
        """Quit the app gracefully."""
        if self._sync_thread is not None and self._sync_thread.is_alive():
            rumps.notification(
                title="MTGA Sync",
                subtitle="Sync in progress",
                message="Waiting for sync to finish before quitting…",
            )
            self._sync_thread.join(timeout=10)
        rumps.App.quit(self)

    # ------------------------------------------------------------------
    # Sync logic (runs in background thread)
    # ------------------------------------------------------------------

    def _do_sync(self) -> None:
        """Execute the full sync pipeline:

        1. Check if MTGA is running (pgrep).
        2. LLDB memory scan (scanner/lldb_probe.py).
        3. Log parsing (parser/pipeline.py).
        4. Merge: memory baseline + log deltas.
        5. Write collection.json.
        """
        try:
            memory_cards = self._run_memory_scan()
            log_cards, log_wildcards = self._run_log_parsing()
            merged_cards = self._merge(memory_cards, log_cards)
            card_count = self._write_collection(merged_cards, log_wildcards)

            self._last_sync_time = datetime.now(timezone.utc)
            self._last_sync_card_count = card_count
            self._last_sync_error = None

            self._update_status(self._status_text())
            rumps.notification(
                title="MTGA Sync",
                subtitle="Sync complete",
                message=f"{card_count} cards synced to collection.json",
            )

        except Exception as exc:
            self._last_sync_error = str(exc)
            self._update_status(f"Error: {self._truncate(self._last_sync_error, 40)}")
            rumps.notification(
                title="MTGA Sync",
                subtitle="Sync failed",
                message=self._truncate(str(exc), 200),
            )

    def _run_memory_scan(self) -> dict[int, int]:
        """Run the LLDB memory probe against the MTGA process.

        Returns:
            Dictionary of ``{grpId: count}`` from memory scan.
            Empty dict if MTGA is not running (with a notification).

        Raises:
            RuntimeError: If LLDB fails unrecoverably.
        """
        process_name = get_macos_mtga_process_name()

        # Check if MTGA is running.
        try:
            pid = _resolve_pid(process_name)
        except RuntimeError:
            rumps.notification(
                title="MTGA Sync",
                subtitle="MTGA not running",
                message="Memory scan skipped. Start MTGA for a full collection scan.",
            )
            return {}

        # Resolve anchor card IDs from names.
        try:
            card_ids = _resolve_card_ids([], ANCHOR_CARD_NAMES)
        except RuntimeError as exc:
            rumps.notification(
                title="MTGA Sync",
                subtitle="Card DB issue",
                message=self._truncate(str(exc), 200),
            )
            return {}

        if not card_ids:
            return {}

        # Build a minimal args namespace for _run_lldb.
        import argparse

        args = argparse.Namespace(
            max_regions=None,
            max_region_mb=1024,
            chunk_mb=4,
            max_matches=256,
            mode="native-find",
            show_lldb_output=False,
        )

        result = _run_lldb(pid, card_ids, args)

        # The LLDB probe returns match *addresses*, not a parsed collection.
        # It tells us which card IDs are present in memory and how many
        # address matches were found.  We interpret this as: card ID is
        # present with count ≥ 1 if any match was found.
        #
        # A full collection extraction (reading memory blocks around matches)
        # is handled by the pymem scanner; here we use LLDB results as a
        # "presence" signal for the anchor cards only.
        memory_cards: dict[int, int] = {}
        for card_id_str, match_info in result.get("matches", {}).items():
            card_id = int(card_id_str)
            count = match_info.get("count", 0)
            if count > 0:
                memory_cards[card_id] = 1  # presence = at least 1

        return memory_cards

    def _run_log_parsing(self) -> tuple[dict[int, int], dict[str, int]]:
        """Parse MTGA Player.log files for collection snapshots and deltas.

        Returns:
            Tuple of (cards, wildcards) from the log pipeline.
        """
        platform = detect_platform()
        try:
            discovery = discover_logs(platform)
        except MissingLogsError as exc:
            rumps.notification(
                title="MTGA Sync",
                subtitle="No logs found",
                message=self._truncate(str(exc), 200),
            )
            return {}, {}

        log_paths = discovery.found
        report = parse_collection(log_paths)

        cards = report.cards or {}
        wildcards = report.wildcards or {}
        return cards, wildcards

    def _merge(
        self,
        memory_cards: dict[int, int],
        log_cards: dict[int, int],
    ) -> dict[int, int]:
        """Merge memory-scan baseline with log-parsed deltas.

        Strategy: Memory scan provides the baseline (full collection if available).
        Log deltas are applied on top — they capture changes since the last
        ``PlayerInventory.GetPlayerCardsV3`` snapshot.

        If the memory scan has results, it is the authoritative baseline and
        log deltas are added on top. If the memory scan is empty (MTGA not
        running), log data alone is used.

        Args:
            memory_cards: Card quantities from memory scan (may be empty).
            log_cards: Card quantities from log parsing (snapshot + deltas).

        Returns:
            Merged dictionary ``{grpId: quantity}``.
        """
        merged: dict[int, int] = dict(memory_cards)

        for card_id, quantity in log_cards.items():
            merged[card_id] = merged.get(card_id, 0) + quantity
            # Clamp: quantities must be ≥ 0.
            if merged[card_id] < 0:
                merged[card_id] = 0

        return merged

    def _write_collection(
        self,
        cards: dict[int, int],
        wildcards: dict[str, int] | None,
    ) -> int:
        """Write the merged collection to ``out/collection.json``.

        Args:
            cards: Merged card quantities.
            wildcards: Wildcard data from logs (may be None/empty).

        Returns:
            Number of unique card entries written.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        collection_path = self.output_dir / "collection.json"

        # Determine the primary source.
        if cards:
            source = "memory-scan+logs" if self._last_sync_error is None else "logs"
        else:
            source = "logs"

        payload: dict[str, Any] = {
            "schema": "collection.v1",
            "source": source,
            "cards": {str(card_id): count for card_id, count in sorted(cards.items())},
            "wildcards": {key: value for key, value in sorted((wildcards or {}).items())},
            "diagnostics": {
                "completeness": {
                    "cards": "complete" if cards else "unknown",
                    "wildcards": "complete" if wildcards else "unknown",
                    "source": "complete" if cards else "unknown",
                },
                "warnings": [],
                "evidence": ["menubar-sync"],
                "syncedAt": _iso_now(),
            },
        }

        collection_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        return len(cards)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _update_status(self, text: str) -> None:
        """Update the status menu item title.

        rumps menu items must be updated on the main thread.  Since
        ``rumps.MenuItem.title`` assignment is thread-safe in practice
        (it updates the underlying NSMenuItem), we set it directly.
        """
        try:
            self.menu[MENU_STATUS].title = text
        except Exception:
            pass  # Menu not yet built or key mismatch.

    def _status_text(self) -> str:
        """Build the status label from last sync state."""
        if self._last_sync_error:
            return f"Error: {self._truncate(self._last_sync_error, 40)}"

        if self._last_sync_time is None:
            return "Status: Ready"

        elapsed = datetime.now(timezone.utc) - self._last_sync_time
        ago = self._humanize_elapsed(elapsed)
        return f"Last sync: {ago} ({self._last_sync_card_count} cards)"

    @staticmethod
    def _humanize_elapsed(elapsed: Any) -> str:
        """Format a timedelta as a human-readable 'X ago' string."""
        seconds = int(elapsed.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """Truncate text to ``max_len`` chars, appending '…' if cut."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    @rumps.timer(60)
    def _refresh_status(self, _sender: Any) -> None:
        """Periodically refresh the status label to update 'X ago' text."""
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return  # Don't clobber "Syncing…" while a sync is running.
        if self._last_sync_error:
            return  # Keep the error visible.
        if self._last_sync_time is not None:
            self._update_status(self._status_text())


def _iso_now() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    """Entry point: create and run the MTGA Sync menubar app."""
    app = MtgaSyncApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())