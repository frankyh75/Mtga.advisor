"""macOS-spezifische Pfade für MTGA-Daten."""

from __future__ import annotations

from pathlib import Path


def get_macos_mtga_data_path() -> Path | None:
    """Findet den Raw-Daten-Ordner mit .mtga SQLite-Dateien.

    Suchreihenfolge:
      1. Standalone (Epic) — ~/Library/Application Support/MTGA/...
      2. Steam — ~/Library/Application Support/Steam/.../MTGA/...
      3. /Applications/MTGA.app (Bundle)
    """
    candidates = [
        # Standalone (Epic Games Launcher)
        Path.home()
        / "Library"
        / "Application Support"
        / "MTGA"
        / "MTGA_Data"
        / "Downloads"
        / "Raw",
        # Steam
        Path.home()
        / "Library"
        / "Application Support"
        / "Steam"
        / "steamapps"
        / "common"
        / "MTGA"
        / "MTGA_Data"
        / "Downloads"
        / "Raw",
        # Direkt aus dem App Bundle (falls symlink)
        Path("/Applications/MTGA.app/Contents/Resources/MTGA_Data/Downloads/Raw"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def get_macos_log_path() -> Path:
    """Pfad zu MTGA Player.log auf macOS."""
    return Path.home() / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player.log"


def get_macos_mtga_process_name() -> str:
    """Name des MTGA-Prozesses auf macOS.

    MTGA läuft unter Rosetta 2, der Prozess heisst normalerweise 'MTGA'.
    Steam-Version könnte 'MTGA' oder 'MTGALauncher' heissen.
    """
    return "MTGA"


def get_default_cache_dir() -> Path:
    """Cache-Verzeichnis für Karten-DB und Anker."""
    return Path.home() / ".mtga_advisor"
