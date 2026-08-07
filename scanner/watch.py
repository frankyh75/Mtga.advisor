"""Watch-Mode: regelmäßiger Scan, wenn MTGA läuft.

Dieser Modul implementiert den Watch-Loop für 'mtga-advisor watch'.
Er prüft periodisch, ob der MTGA-Prozess läuft und führt dann den
kombinierten Scan (T3: Collection + Decks + Ranks) aus. Neue Decks
und Karten werden so automatisch erfasst, ohne manuellen Aufruf.

Die MTGA-Erkennung erfolgt über zwei Wege:
  1. Prozess-Erkennung via pgrep (keine Python-Dependency nötig)
  2. Helper-Socket-Verfügbarkeit (indirekter Indikator — wenn der
     Helper eine Region lesen kann, läuft MTGA)

De-Dup: ein einfacher Lock verhindert überlappende Scans, falls
ein Scan länger dauert als das Interval.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .macos_paths import get_macos_mtga_process_names

logger = logging.getLogger(__name__)

# Default-Intervall in Sekunden (5 Minuten)
DEFAULT_INTERVAL = 300

# Minimales Intervall (verhindert CPU-Pinning bei falscher Konfiguration)
MIN_INTERVAL = 30


@dataclass
class WatchConfig:
    """Konfiguration für den Watch-Loop."""
    interval: int = DEFAULT_INTERVAL
    output_dir: Path = field(default_factory=lambda: Path("out"))
    debug: bool = False
    # Wenn True: beenden nach einem erfolgreichen Scan (für Tests / CI)
    single_shot: bool = False
    # Wenn gesetzt: schreibt Status in diese Datei (für Monitoring)
    state_file: Path | None = None


@dataclass
class WatchState:
    """Laufzeit-Status des Watch-Loops."""
    iterations: int = 0
    scans_run: int = 0
    scans_skipped: int = 0
    mtga_detected: int = 0
    last_scan_time: float | None = None
    last_scan_result: str | None = None  # "ok" | "error" | "partial"
    running: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "scansRun": self.scans_run,
            "scansSkipped": self.scans_skipped,
            "mtgaDetected": self.mtga_detected,
            "lastScanTime": self.last_scan_time,
            "lastScanResult": self.last_scan_result,
            "running": self.running,
        }


def is_mtga_running(
    process_names: Sequence[str] | None = None,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
) -> bool:
    """Prüft, ob ein MTGA-Prozess läuft (via pgrep).

    Args:
        process_names: Zu suchende Prozessnamen (Default: macOS-Namen).
        runner: Inject für Tests — Defaults zu subprocess.run.

    Returns:
        True, wenn mindestens ein MTGA-Prozess gefunden wurde.
    """
    if process_names is None:
        process_names = get_macos_mtga_process_names()

    if runner is None:
        runner = _run_pgrep

    for name in process_names:
        try:
            result = runner(["pgrep", "-x", name])
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            # pgrep nicht verfügbar (nicht-macOS)
            logger.debug("pgrep nicht verfügbar, versuche direkte Suche")
            return _is_mtga_running_fallback(process_names)

    return False


def _run_pgrep(cmd: list[str]) -> subprocess.CompletedProcess:
    """Führt pgrep aus."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def _is_mtga_running_fallback(process_names: Sequence[str]) -> bool:
    """Fallback: ps aux grep (falls pgrep fehlt)."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        output = result.stdout.lower()
        return any(name.lower() in output for name in process_names)
    except (OSError, subprocess.SubprocessError):
        return False


def _write_state(state: WatchState, state_file: Path | None) -> None:
    """Schreibt den Watch-Status in eine JSON-Datei (für Monitoring)."""
    if state_file is None:
        return
    import json
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Status-Datei nicht schreibbar: %s", exc)


def watch_loop(
    config: WatchConfig,
    *,
    print_fn: Callable[..., None] = print,
    scan_fn: Callable[[WatchConfig], int] | None = None,
    is_running_fn: Callable[[], bool] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    stop_event: threading.Event | None = None,
) -> WatchState:
    """Haupt-Loop für den Watch-Mode.

    Prüft alle 'interval' Sekunden, ob MTGA läuft und führt dann den
    kombinierten Scan aus. Beendet sich bei SIGINT/SIGTERM sauber.

    Args:
        config:      Watch-Konfiguration (Interval, Output-Dir, etc.).
        print_fn:    Print-Funktion für Statusmeldungen.
        scan_fn:     Scan-Funktion (Default: _run_combined_scan).
                     Nimmt WatchConfig, gibt Exit-Code zurück (0=ok).
        is_running_fn: MTGA-Detector (Default: is_mtga_running).
        sleep_fn:    Sleep-Funktion (Default: time.sleep).
        stop_event:  Externes Event, um den Loop sauber zu stoppen.

    Returns:
        WatchState mit Statistiken.
    """
    if config.interval < MIN_INTERVAL:
        print_fn(
            f"⚠ Interval {config.interval}s ist sehr klein, "
            f"verwende Minimum {MIN_INTERVAL}s."
        )
        config.interval = MIN_INTERVAL

    if scan_fn is None:
        scan_fn = _run_combined_scan
    if is_running_fn is None:
        is_running_fn = is_mtga_running
    if sleep_fn is None:
        sleep_fn = time.sleep
    if stop_event is None:
        stop_event = threading.Event()

    state = WatchState()
    scan_lock = threading.Lock()

    # Signal-Handler für sauberes Beenden
    def _handle_signal(signum: int, frame: Any) -> None:
        print_fn(f"\n⏹ Signal {signum} empfangen, beende Watch-Loop...")
        state.running = False
        stop_event.set()

    original_int = signal.getsignal(signal.SIGINT)
    original_term = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except ValueError:
        # Signal-Handler kann nicht im Nicht-Main-Thread gesetzt werden (Tests)
        pass

    print_fn(f"👁 Watch-Mode gestartet (Interval: {config.interval}s, Output: {config.output_dir})")
    print_fn(f"   Beenden mit Ctrl+C")

    try:
        while state.running and not stop_event.is_set():
            state.iterations += 1

            if is_running_fn():
                state.mtga_detected += 1
                print_fn(f"🎮 MTGA erkannt — starte Scan...")

                if scan_lock.acquire(blocking=False):
                    try:
                        exit_code = scan_fn(config)
                        state.scans_run += 1
                        state.last_scan_time = time.time()
                        if exit_code == 0:
                            state.last_scan_result = "ok"
                            print_fn(f"✅ Scan abgeschlossen (Iteration {state.iterations})")
                        else:
                            state.last_scan_result = "error"
                            print_fn(f"⚠ Scan mit Exit-Code {exit_code} beendet")
                    finally:
                        scan_lock.release()
                else:
                    state.scans_skipped += 1
                    print_fn(f"⏭ Scan läuft noch — überspringe (Iteration {state.iterations})")
            else:
                logger.debug("MTGA läuft nicht, warte...")

            _write_state(state, config.state_file)

            if config.single_shot:
                break

            if not state.running or stop_event.is_set():
                break

            # Warte, aber reagiere auf stop_event
            if sleep_fn is time.sleep:
                # Production: blockiert bis interval oder stop_event
                stop_event.wait(config.interval)
            else:
                # Test/Inject: nutzt die injizierte sleep_fn
                sleep_fn(config.interval)
    finally:
        try:
            signal.signal(signal.SIGINT, original_int)
            signal.signal(signal.SIGTERM, original_term)
        except (ValueError, TypeError):
            pass
        state.running = False
        _write_state(state, config.state_file)
        print_fn(f"👁 Watch-Loop beendet (Scans: {state.scans_run}, Übersprungen: {state.scans_skipped})")

    return state


def _run_combined_scan(config: WatchConfig) -> int:
    """Führt den kombinierten Scan (T3: scan_all) aus.

    Diese Funktion baut die CLI-Argumente für 'scan --all' nach und
    ruft die combined_scanner.scan_all Funktion direkt auf.
    """
    from .combined_scanner import scan_all
    from .memory_scanner import write_collection_artifacts
    import json as _json

    result = scan_all(
        debug=config.debug,
        print_fn=print,
    )

    if result.errors and not result.collection and not result.decks and not result.ranks:
        print(f"❌ Kombinierter Scan fehlgeschlagen: {'; '.join(result.errors)}")
        return 1

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collection-Artefakte schreiben
    if result.collection is not None:
        collection_path, run_report_path = write_collection_artifacts(
            result.collection.collection, output_dir, scan_result=result.collection
        )
        print(f"  Collection: {collection_path}")

    # Deck-Artefakte schreiben
    if result.decks:
        decks_path = output_dir / "decks.json"
        decks_path.write_text(
            _json.dumps(result.decks, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  Decks: {decks_path} ({len(result.decks)} Decks)")

    # Log-basierte Deck-Kartenlisten (DeckUpsertDeckV3) exportieren.
    # Diese kommen aus den Player.log-Dateien, nicht aus dem Memory-Scan.
    try:
        from parser.deck_cards import export_deck_cards
        from parser.log_paths import discover_logs

        try:
            discovery = discover_logs(None, None)
            log_paths = discovery.found
        except Exception:
            log_paths = []
        if log_paths:
            export_deck_cards(log_paths, output_dir)
            print(f"  Deck-Kartenlisten: {output_dir / 'deck-cards.json'}")
    except Exception as exc:  # pragma: no cover - defensiv
        print(f"  ⚠ Deck-Kartenlisten-Export übersprungen: {exc}")

    # Rank-Artefakte schreiben
    if result.ranks or result.account:
        rank_data = {
            "ranks": result.ranks,
            "account": result.account,
            "warnings": result.rank_warnings,
        }
        rank_path = output_dir / "ranks.json"
        rank_path.write_text(
            _json.dumps(rank_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  Ränge: {rank_path}")

    return 0