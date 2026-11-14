"""macOS-spezifische Pfade für MTGA-Daten."""

from __future__ import annotations

import os
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
        / "com.wizards.mtga"
        / "Downloads"
        / "Raw",
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
    """Cache-Verzeichnis für Karten-DB und Anker.

    Wichtig: Läuft der Scan über ``sudo``, ist ``HOME`` = ``/var/root``.
    Dann würde die Anker-Datei in ``/var/root/.mtga_advisor`` landen — der
    aufrufende User sieht sie nie und muss die 5 Karten bei JEDEM Lauf neu
    eintippen. Deshalb wird das Home des aufrufenden Users bevorzugt.

    Reihenfolge:
      1. ``MTGA_ADVISOR_CACHE_DIR`` (explizit gesetzt — für Tests/Launcher)
      2. ``Path.home()`` — auch unter sudo, denn der Launcher setzt HOME
         bewusst auf das Home des MTGA-Besitzers (dort liegt die Karten-DB)
      3. Nur falls HOME das künstliche root-Home ist: Home des ``SUDO_USER``
    """
    override = os.environ.get("MTGA_ADVISOR_CACHE_DIR")
    if override:
        return Path(override).expanduser()

    home = Path.home()

    # HOME ist unter sudo auf /var/root gesetzt: kein echtes Zuhause, dort
    # liegt keine Karten-DB und der aufrufende User findet die Anker nie.
    # Nur DANN auf den echten Aufrufer zurückfallen — hat der Launcher HOME
    # dagegen schon korrigiert, bleibt seine Wahl bestehen.
    root_homes = {Path("/var/root"), Path("/private/var/root")}
    if home in root_homes:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            try:
                import pwd

                return Path(pwd.getpwnam(sudo_user).pw_dir) / ".mtga_advisor"
            except (KeyError, ImportError):
                pass

    return home / ".mtga_advisor"
