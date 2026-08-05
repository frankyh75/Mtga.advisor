"""Tests für den Watch-Mode (scanner/watch.py)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from scanner.watch import (
    DEFAULT_INTERVAL,
    WatchConfig,
    WatchState,
    is_mtga_running,
    watch_loop,
)


# --- is_mtga_running Tests ---


def test_is_mtga_running_with_pgrep_match():
    """pgrep findet den Prozess."""
    def fake_runner(cmd):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="12345\n")

    assert is_mtga_running(["MTGA"], runner=fake_runner) is True


def test_is_mtga_running_no_match():
    """pgrep findet nichts."""
    def fake_runner(cmd):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="")

    assert is_mtga_running(["MTGA", "MTGALauncher"], runner=fake_runner) is False


def test_is_mtga_running_pgrep_not_found():
    """pgrep fehlt → Fallback via ps."""
    def fake_runner(cmd):
        raise FileNotFoundError("pgrep not found")

    # Mock den Fallback
    import scanner.watch as watch_mod
    original_fallback = watch_mod._is_mtga_running_fallback
    watch_mod._is_mtga_running_fallback = lambda names: True
    try:
        assert is_mtga_running(["MTGA"], runner=fake_runner) is True
    finally:
        watch_mod._is_mtga_running_fallback = original_fallback


def test_is_mtga_running_multiple_names_first_match():
    """Erster Name matcht → True ohne zweite Prüfung."""
    calls = []
    def fake_runner(cmd):
        calls.append(cmd)
        if cmd[-1] == "MTGA":
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="1")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="")

    assert is_mtga_running(["MTGA", "MTGALauncher"], runner=fake_runner) is True
    assert len(calls) == 1  # nur einmal aufgerufen


# --- watch_loop Tests ---


def test_watch_loop_single_shot_mtga_running(tmp_path):
    """Single-Shot mit laufendem MTGA → ein Scan wird ausgeführt."""
    scan_calls = []
    def fake_scan(config):
        scan_calls.append(config)
        return 0

    state = watch_loop(
        WatchConfig(interval=60, output_dir=tmp_path, single_shot=True),
        scan_fn=fake_scan,
        is_running_fn=lambda: True,
        sleep_fn=lambda s: None,
    )

    assert state.iterations == 1
    assert state.scans_run == 1
    assert state.scans_skipped == 0
    assert state.last_scan_result == "ok"
    assert state.mtga_detected == 1
    assert len(scan_calls) == 1


def test_watch_loop_single_shot_mtga_not_running(tmp_path):
    """Single-Shot ohne MTGA → kein Scan."""
    scan_calls = []
    def fake_scan(config):
        scan_calls.append(config)
        return 0

    state = watch_loop(
        WatchConfig(interval=60, output_dir=tmp_path, single_shot=True),
        scan_fn=fake_scan,
        is_running_fn=lambda: False,
        sleep_fn=lambda s: None,
    )

    assert state.iterations == 1
    assert state.scans_run == 0
    assert state.scans_skipped == 0
    assert state.mtga_detected == 0
    assert len(scan_calls) == 0


def test_watch_loop_multiple_iterations(tmp_path):
    """Mehrere Iterationen: MTGA läuft nur in Iteration 2 und 4."""
    mtga_states = iter([False, True, False, True])
    scan_calls = []
    def fake_scan(config):
        scan_calls.append(config)
        return 0

    # stop nach 4 sleep-Aufrufen
    call_count = [0]
    stop_event = threading.Event()

    def smart_sleep(seconds):
        call_count[0] += 1
        if call_count[0] >= 4:
            stop_event.set()

    state = watch_loop(
        WatchConfig(interval=30, output_dir=tmp_path),
        scan_fn=fake_scan,
        is_running_fn=lambda: next(mtga_states, False),
        sleep_fn=smart_sleep,
        stop_event=stop_event,
    )

    # 4 Iterationen: 2 mit MTGA, 2 ohne
    assert state.iterations == 4
    assert state.scans_run == 2
    assert state.mtga_detected == 2


def test_watch_loop_dedup_lock_busy(tmp_path):
    """De-Dup: wenn der scan_lock belegt ist, wird der Scan übersprungen."""
    import scanner.watch as watch_mod
    import threading

    # Erzeuge einen Lock, den wir von außen halten
    fake_lock = threading.Lock()
    fake_lock.acquire()

    scan_calls = []
    def fake_scan(config):
        scan_calls.append(config)
        return 0

    stop_event = threading.Event()
    sleep_count = [0]
    def fast_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            stop_event.set()

    # Patche threading.Lock in watch_mod, damit watch_loop unseren Lock nutzt
    original_lock = watch_mod.threading.Lock
    watch_mod.threading.Lock = lambda: fake_lock
    try:
        state = watch_loop(
            WatchConfig(interval=30, output_dir=tmp_path),
            scan_fn=fake_scan,
            is_running_fn=lambda: True,
            sleep_fn=fast_sleep,
            stop_event=stop_event,
        )
    finally:
        watch_mod.threading.Lock = original_lock
        fake_lock.release()

    # Alle Iterationen übersprungen, weil Lock blockiert
    assert state.scans_run == 0
    assert state.scans_skipped >= 1
    assert len(scan_calls) == 0


def test_watch_loop_scan_error(tmp_path):
    """Scan-Fehler wird als last_scan_result='error' gespeichert."""
    def error_scan(config):
        return 1

    state = watch_loop(
        WatchConfig(interval=60, output_dir=tmp_path, single_shot=True),
        scan_fn=error_scan,
        is_running_fn=lambda: True,
        sleep_fn=lambda s: None,
    )

    assert state.scans_run == 1
    assert state.last_scan_result == "error"


def test_watch_loop_interval_too_small(tmp_path, capsys):
    """Zu kleines Interval wird auf Minimum gesetzt."""
    state = watch_loop(
        WatchConfig(interval=5, output_dir=tmp_path, single_shot=True),
        scan_fn=lambda c: 0,
        is_running_fn=lambda: False,
        sleep_fn=lambda s: None,
    )

    captured = capsys.readouterr()
    assert "sehr klein" in captured.out or "Minimum" in captured.out


def test_watch_loop_state_file(tmp_path):
    """Status-Datei wird geschrieben."""
    state_file = tmp_path / "state.json"
    state = watch_loop(
        WatchConfig(
            interval=60,
            output_dir=tmp_path,
            single_shot=True,
            state_file=state_file,
        ),
        scan_fn=lambda c: 0,
        is_running_fn=lambda: True,
        sleep_fn=lambda s: None,
    )

    assert state_file.exists()
    import json
    data = json.loads(state_file.read_text())
    assert data["scansRun"] == 1
    assert data["iterations"] == 1
    assert data["running"] is False


def test_watch_loop_stop_event_immediate(tmp_path):
    """Stop-Event beendet den Loop vor der ersten Iteration."""
    stop_event = threading.Event()
    stop_event.set()

    state = watch_loop(
        WatchConfig(interval=60, output_dir=tmp_path),
        scan_fn=lambda c: 0,
        is_running_fn=lambda: True,
        sleep_fn=lambda s: None,
        stop_event=stop_event,
    )

    assert state.iterations == 0
    assert state.scans_run == 0


# --- WatchConfig Tests ---


def test_watch_config_defaults():
    config = WatchConfig()
    assert config.interval == DEFAULT_INTERVAL
    assert config.single_shot is False
    assert config.state_file is None


def test_watch_config_custom():
    config = WatchConfig(interval=120, single_shot=True)
    assert config.interval == 120
    assert config.single_shot is True


# --- WatchState Tests ---


def test_watch_state_to_dict():
    state = WatchState()
    state.iterations = 5
    state.scans_run = 3
    state.scans_skipped = 1
    state.last_scan_result = "ok"
    d = state.to_dict()
    assert d["iterations"] == 5
    assert d["scansRun"] == 3
    assert d["scansSkipped"] == 1
    assert d["lastScanResult"] == "ok"
    assert d["running"] is True