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
        # Alternative MTGA-App-Container-Pfade
        Path.home()
        / "Library"
        / "Application Support"
        / "Wizards Of The Coast"
        / "MTGA"
        / "MTGA_Data"
        / "Downloads"
        / "Raw",
        Path.home()
        / "Library"
        / "Application Support"
        / "Wizards of the Coast"
        / "MTGA"
        / "MTGA_Data"
        / "Downloads"
        / "Raw",
        # Epic Games Launcher default install location.
        Path("/Users/Shared/Epic Games/MagicTheGathering/MTGA.app/Contents/Resources/MTGA_Data/Downloads/Raw"),
        Path("/Users/Shared/Epic Games/MagicTheGathering/MTGA_Data/Downloads/Raw"),
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
    """Pfad zu MTGA Player.log auf macOS.

    Der Log-Pfad variiert bei manchen Installationen leicht in der Gross-/Kleinschreibung.
    """
    candidates = [
        Path.home() / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player.log",
        Path.home() / "Library" / "Logs" / "Wizards of the Coast" / "MTGA" / "Player.log",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def get_macos_mtga_process_names() -> tuple[str, ...]:
    """Mögliche MTGA-Prozessnamen auf macOS.

    MTGA läuft unter Rosetta 2, der Prozess heisst normalerweise 'MTGA'.
    Steam-Version könnte 'MTGA' oder 'MTGALauncher' heissen.
    """
    return ("MTGA", "MTGALauncher", "MTGArena")


def get_macos_mtga_process_name() -> str:
    """Name des bevorzugten MTGA-Prozesses auf macOS."""
    return get_macos_mtga_process_names()[0]


def get_default_cache_dir() -> Path:
    """Cache-Verzeichnis für Karten-DB und Anker."""
    return Path.home() / ".mtga_advisor"
